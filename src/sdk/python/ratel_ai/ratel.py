"""The `ratel()` core: one construction point — the Python mirror of `src/sdk/ts/src/ratel.ts`.

`ratel(...)` builds one `ToolCatalog`, one `SkillCatalog`, one merged
`RuntimeEvents` stream and one `RuntimeCatalog` over them, and hands back a
`Ratel` that owns the framework-independent guards: reserved capability-tool
ids, top-K clamping and the fixed set of three capability tools. The fact
catalog is experimental and built on first access to `Ratel.facts`.

The TypeScript `adaptTo` and the adapter SPI are not ported yet.
"""

from __future__ import annotations

from collections.abc import Awaitable, Iterable, Sequence
from typing import TYPE_CHECKING, Any, TypedDict

from ._custom_ranking import RankFn
from ._native import SearchHit
from .capabilities import (
    _DEFAULT_TOP_K_TOOLS,
    INVOKE_TOOL_ID,
    SEARCH_CAPABILITIES_ID,
    _clamp_top_k,
    invoke_tool_tool,
    run_capabilities_search,
    search_capabilities_tool,
)
from .catalog import (
    EmbeddingSpec,
    ExecutableTool,
    RerankerConfig,
    SearchMethod,
    Tool,
    ToolCatalog,
    TraceSinkConfig,
)
from .embedding_artifact import ExperimentalEmbeddingArtifact
from .runtime_events import RuntimeCatalog, RuntimeEvents
from .skill_catalog import SkillCatalog
from .skill_tools import GET_SKILL_CONTENT_ID, get_skill_content_tool
from .turns import Turn, current_turn_id

if TYPE_CHECKING:
    from .fact_catalog import FactCatalog
    from .grounding import GroundingResult, GroundingSnapshotItem

__all__ = ["Ratel", "RatelEventsOptions", "ToolCollection", "ratel"]


class RatelEventsOptions(TypedDict, total=False):
    """Identity and delivery options of the merged runtime-event stream (ADR-0020).

    The keys are the keyword arguments of `RuntimeEvents`.
    """

    session_id: str
    source_id: str
    queue_capacity: int
    batch_size: int
    experimental_catalog_definitions: bool


# The capability-tool ids: an app tool may not shadow them (registration raises).
_RESERVED_TOOL_IDS = frozenset({SEARCH_CAPABILITIES_ID, INVOKE_TOOL_ID, GET_SKILL_CONTENT_ID})


def ratel(
    *,
    method: SearchMethod = "bm25",
    embedding: EmbeddingSpec | None = None,
    experimental_embedding_artifact: ExperimentalEmbeddingArtifact | None = None,
    reranker: RerankerConfig | None = None,
    retrieve_fn: RankFn | None = None,
    reranker_fn: RankFn | None = None,
    reranker_depth: int | None = None,
    recall_top_k: int | None = None,
    facts_top_k: int | None = None,
    trace: TraceSinkConfig | None = None,
    events: RatelEventsOptions | None = None,
) -> Ratel:
    """Create a framework-neutral Ratel core.

    Register tools on `r.tools` and skills on `r.skills`, hand the model
    `r.model_tools()`, and rank with `r.recall(query)`.

    Args:
        method: default retrieval method of the tool, skill and fact catalogs
            (default ``"bm25"``). Facts have no custom path: with
            ``"custom"`` they rank with BM25.
        embedding: model for ``"semantic"``/``"hybrid"`` retrieval, forwarded
            to every catalog — see `EmbeddingSpec`.
        experimental_embedding_artifact: build-time artifact forwarded to the
            tool and skill catalogs (default ``on_miss="error"``). It must
            cover every non-empty corpus that registers.
        reranker: second-stage built-in reranker of the tool and skill
            catalogs. Facts are not reranked. **Experimental.**
        retrieve_fn: your own ranking function, with ``method="custom"``.
            **Experimental.**
        reranker_fn: your own reranking function. **Experimental.**
        reranker_depth: how many first-stage candidates ``reranker_fn`` sees
            (default 50).
        recall_top_k: max tools each `Ratel.recall` returns. Capped at 50;
            invalid values fall back to 5.
        facts_top_k: max retrieval-gated facts each `Ratel.ground` considers.
            Capped at 50; invalid values fall back to 3. **Experimental.**
        trace: local trace-stream destination of every catalog (default: discard).
        events: identity and delivery options of `Ratel.events`.

    Returns:
        The core.

    Raises:
        ValueError: if the ranking options are invalid.
    """
    ranking: dict[str, Any] = {
        "method": method,
        "embedding": embedding,
        "trace": trace,
        "experimental_embedding_artifact": experimental_embedding_artifact,
        "reranker": reranker,
        "retrieve_fn": retrieve_fn,
        "reranker_fn": reranker_fn,
        "reranker_depth": reranker_depth,
    }
    return Ratel(
        ToolCatalog(**ranking),
        SkillCatalog(**ranking),
        method=method,
        embedding=embedding,
        recall_top_k=recall_top_k,
        facts_top_k=facts_top_k,
        trace=trace,
        events=events,
    )


class ToolCollection:
    """The core's guarded handle over its shared `ToolCatalog`.

    Registration rejects the reserved capability-tool ids and keeps the
    catalog's replace-in-place semantics. Host searches clamp ``top_k`` to
    [1, 50] like the capability funnel; `catalog` is the unclamped escape hatch.
    """

    def __init__(self, catalog: ToolCatalog) -> None:
        """Wrap the core's tool catalog."""
        self.catalog = catalog

    def register(self, tools: ExecutableTool | Iterable[ExecutableTool]) -> Awaitable[None]:
        """Register one tool or many (replace in place on a duplicate id).

        The whole batch is validated before anything registers, so a bad item
        leaves the catalog unchanged. **Always `await` the result**: it drives
        dense preparation — see `ToolCatalog.register`.

        Raises:
            TypeError: if an item is not an `ExecutableTool`.
            ValueError: if an id is reserved for a capability tool, or an
                ``execute`` is missing.
        """
        batch = [tools] if isinstance(tools, ExecutableTool) else list(tools)
        for tool in batch:
            _assert_native_tool(tool)
            _assert_unreserved_id(tool.id)
        return self.catalog.register(batch)

    def has(self, tool_id: str) -> bool:
        """Return whether a tool with this id is registered."""
        return self.catalog.has(tool_id)

    def get(self, tool_id: str) -> Tool | None:
        """Return the tool's searchable metadata, or `None` when unregistered."""
        return self.catalog.get(tool_id)

    def search(
        self,
        query: str,
        top_k: int,
        method: SearchMethod | None = None,
        turn_id: str | None = None,
    ) -> list[SearchHit]:
        """Rank the catalog synchronously with BM25 (origin ``"direct"``).

        Raises:
            RuntimeError: if the resolved method is not ``"bm25"`` or the
                catalog has a reranker; use `search_async` for those.
        """
        top = _clamp_top_k(top_k, _DEFAULT_TOP_K_TOOLS)
        return self.catalog.search(query, top, "direct", method, turn_id)

    async def search_async(
        self,
        query: str,
        top_k: int,
        method: SearchMethod | None = None,
        turn_id: str | None = None,
    ) -> list[SearchHit]:
        """Rank the catalog with any method off the event loop (origin ``"direct"``)."""
        top = _clamp_top_k(top_k, _DEFAULT_TOP_K_TOOLS)
        return await self.catalog.search_async(query, top, "direct", method, turn_id)

    async def invoke(self, tool_id: str, args: dict[str, Any], turn_id: str | None = None) -> Any:
        """Run a registered tool by id; ``turn_id`` pairs it with its search (ADR-0014)."""
        return await self.catalog.invoke(tool_id, args, turn_id)


class Ratel:
    """One `ratel(...)` core: shared tool, skill and fact catalogs, one event stream.

    Build it with `ratel`.
    """

    def __init__(
        self,
        tool_catalog: ToolCatalog,
        skill_catalog: SkillCatalog,
        *,
        method: SearchMethod,
        embedding: EmbeddingSpec | None,
        recall_top_k: int | None,
        facts_top_k: int | None,
        trace: TraceSinkConfig | None,
        events: RatelEventsOptions | None,
    ) -> None:
        """Compose the catalogs; `ratel` is the public constructor."""
        self.tools = ToolCollection(tool_catalog)
        self.skills = skill_catalog
        self.events = RuntimeEvents([tool_catalog, skill_catalog], **(events or {}))
        self.catalog = RuntimeCatalog(tool_catalog, skill_catalog, source_id=self.events.source_id)
        self._method: SearchMethod = method
        self._embedding = embedding
        self._recall_top_k = recall_top_k
        self._facts_top_k = facts_top_k
        self._trace = trace
        self._facts: FactCatalog | None = None

    @property
    def facts(self) -> FactCatalog:
        """⚠️ Experimental (ADR-0017). The shared fact catalog, built on first access.

        Lazy so that a host that never touches facts never sees the
        experimental-API warning.
        """
        if self._facts is None:
            from .fact_catalog import FactCatalog

            self._facts = FactCatalog(
                trace=self._trace,
                # Facts have no custom path; rank them lexically instead.
                method="bm25" if self._method == "custom" else self._method,
                embedding=self._embedding,
                facts_top_k=self._facts_top_k,
            )
        return self._facts

    def model_tools(self) -> dict[str, ExecutableTool]:
        """Return the three capability tools for a framework-free host.

        `search_capabilities`, `invoke_tool` and `get_skill_content` are
        always there, and their payload does not depend on registration order,
        so the prompt cache survives. Fresh objects per call: take it once and
        reuse it. Tools and skills registered later are still discoverable.
        """
        catalog = self.tools.catalog
        return {
            SEARCH_CAPABILITIES_ID: search_capabilities_tool(
                catalog, self.skills, advertise_skills=True
            ),
            INVOKE_TOOL_ID: invoke_tool_tool(catalog),
            GET_SKILL_CONTENT_ID: get_skill_content_tool(self.skills),
        }

    async def recall(self, query: str, turn_id: str | None = None) -> dict[str, Any] | None:
        """Rank `query` into the canonical `search_capabilities` result.

        Origin ``"direct"``, top-K from ``recall_top_k``. Returns `None` when
        nothing matched. Ranks whatever is registered now: `await` the
        registrations first.
        """
        result = await run_capabilities_search(
            self.tools.catalog,
            query,
            top_k_tools=self._recall_top_k,
            skill_catalog=self.skills,
            origin="direct",
            turn_id=turn_id,
        )
        if not result["tools"]["groups"] and not result["skills"]:
            return None
        return result

    async def ground(
        self, query: str, transcript: Sequence[str], top_k: int | None = None
    ) -> GroundingResult:
        """⚠️ Experimental (ADR-0017). Decide which facts to (re-)inject.

        See `FactCatalog.ground`.
        """
        return await self.facts.ground(query, transcript, top_k)

    async def ground_snapshot(
        self, query: str, top_k: int | None = None
    ) -> list[GroundingSnapshotItem]:
        """⚠️ Experimental (ADR-0017). The full grounding set for one model call.

        See `FactCatalog.ground_snapshot`.
        """
        return await self.facts.ground_snapshot(query, top_k)

    def turn(
        self,
        id: str | None = None,
        *,
        user_message: str | None = None,
        end_user_id: str | None = None,
    ) -> Turn:
        """Mark one user request as one turn: ``with r.turn(...):`` or ``async with``.

        See `ToolCatalog.turn`.
        """
        return self.tools.catalog.turn(id, user_message=user_message, end_user_id=end_user_id)

    def record_tool_call(
        self,
        tool_id: str,
        *,
        took_ms: float | None = None,
        error: object = None,
        turn_id: str | None = None,
    ) -> None:
        """Record a tool call your framework ran itself — see `ToolCatalog.record_tool_call`."""
        self.tools.catalog.record_tool_call(tool_id, took_ms=took_ms, error=error, turn_id=turn_id)

    def current_turn_id(self) -> str | None:
        """Return the id of the turn the caller runs inside, or `None`."""
        return current_turn_id()


def _assert_native_tool(tool: object) -> None:
    if not isinstance(tool, ExecutableTool):
        raise TypeError(
            f"ratel: tools.register() expects ExecutableTool items; got {type(tool).__name__}"
        )


def _assert_unreserved_id(tool_id: str) -> None:
    if tool_id in _RESERVED_TOOL_IDS:
        raise ValueError(f'ratel: tool id "{tool_id}" is reserved for the capability tools')
