"""⚠️ Experimental definition overrides — the Python mirror of `definition-overrides.ts`.

An external source (Ratel Cloud is the first one) can own the *retrieval*
description of live tools, skills and facts. Attach it with
`RuntimeCatalog.experimental_attach_definition_overrides(source)`. The model-facing
description, schemas, bodies, tags and executors stay local, and a snapshot always
shows the local definitions. A complete overlay replaces the previous overlay;
removing an override restores the latest local value. Opting in is one-way for the
life of the process.

These APIs may change without a major-version bump.
"""

from __future__ import annotations

import asyncio
import dataclasses
import warnings
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeVar, cast

from .exceptions import DefinitionOverlayError

if TYPE_CHECKING:
    from .catalog import ToolCatalog
    from .fact_catalog import FactCatalog
    from .skill_catalog import SkillCatalog

DefinitionOverrideKind = Literal["tool", "skill", "fact"]

_DEFINITION_OVERRIDE_KINDS = ("tool", "skill", "fact")
_MAX_ENTRY_ID_BYTES = 512
_MAX_SEARCHABLE_DESCRIPTION_BYTES = 16_384


@dataclass(frozen=True)
class ExperimentalDefinitionOverride:
    """One externally-authored retrieval-description override for a local catalog entry."""

    # Catalog containing the entry.
    kind: DefinitionOverrideKind
    # Stable local catalog entry id.
    entry_id: str
    # Replacement for the description component used by retrieval.
    searchable_description: str


class ExperimentalDefinitionOverlaySource(Protocol):
    """Pluggable boundary implemented by whichever client owns the overlay.

    ``fetch`` pulls the overlay, conditionally when a previous strong ETag is
    given. It returns a mapping: ``{"status": 200, "etag": str, "body":
    {"overrides": [{"kind", "entry_id", "searchable_description"}, ...]}}`` or
    ``{"status": 304}``. The response is untrusted and validated before any
    catalog changes.
    """

    def fetch(self, if_none_match: str | None = None) -> Awaitable[Mapping[str, Any]]:
        """Pull the overlay; ``if_none_match`` is the last accepted ETag."""
        ...


class ExperimentalDefinitionOverridesAttachment:
    """An active attachment, from `RuntimeCatalog.experimental_attach_definition_overrides`."""

    def __init__(self, source: ExperimentalDefinitionOverlaySource, overrides: _Overrides) -> None:
        """Bind a source to the catalogs; attach performs the initial pull."""
        self._source = source
        self._overrides = overrides
        self._etag: str | None = None
        self._in_flight: asyncio.Future[bool] | None = None

    async def refresh(self) -> bool:
        """Pull and apply a changed overlay, with rollback across catalogs.

        Concurrent calls share one request.

        Returns:
            ``True`` when a new overlay was applied, ``False`` on ``304``.

        Raises:
            DefinitionOverlayError: for an invalid response or a failed apply.
                The last accepted ETag and definitions stay in place.
        """
        if self._in_flight is None:
            in_flight = asyncio.ensure_future(self._refresh_once())
            self._in_flight = in_flight

            def clear(_: asyncio.Future[bool]) -> None:
                if self._in_flight is in_flight:
                    self._in_flight = None

            in_flight.add_done_callback(clear)
        return await self._in_flight

    async def _refresh_once(self) -> bool:
        response = validate_definition_overlay_response(await self._source.fetch(self._etag))
        if response is None:
            return False
        etag, overrides = response
        await self._overrides.apply(overrides)
        self._etag = etag
        return True


def validate_definition_overlay_response(
    value: object,
) -> tuple[str, list[ExperimentalDefinitionOverride]] | None:
    """Validate one untrusted source response.

    Returns:
        ``None`` for ``304``, else the ETag and the validated overrides.

    Raises:
        DefinitionOverlayError: ``invalid_status``, ``invalid_payload`` or
            ``invalid_etag``.
    """
    status = value.get("status") if isinstance(value, Mapping) else None
    if not isinstance(value, Mapping) or isinstance(status, bool) or status not in (200, 304):
        raise DefinitionOverlayError(
            "definition overlay response status must be 200 or 304", "invalid_status"
        )
    if status == 304:
        return None
    body = value.get("body")
    if not isinstance(body, Mapping):
        raise DefinitionOverlayError(
            "definition overlay response body must be an object", "invalid_payload"
        )
    overrides = validate_definition_overrides(body.get("overrides"))
    etag = value.get("etag")
    if not isinstance(etag, str) or not etag.strip():
        raise DefinitionOverlayError(
            "definition overlay response etag must be a non-empty string", "invalid_etag"
        )
    return etag, overrides


def validate_definition_overrides(value: object) -> list[ExperimentalDefinitionOverride]:
    """Validate and snapshot a complete untrusted override set.

    Raises:
        DefinitionOverlayError: ``invalid_payload`` for a wrong shape or size.
    """
    if not isinstance(value, (list, tuple)):
        raise DefinitionOverlayError(
            "definition overlay overrides must be an array", "invalid_payload"
        )
    return [_validate_override(index, candidate) for index, candidate in enumerate(value)]


class _Searchable(Protocol):
    id: str
    description: str
    experimental_searchable_description: str | None


_Item = TypeVar("_Item", bound=_Searchable)


class DefinitionOverrideState:
    """Per-catalog override bookkeeping: the local definitions and the active overlay.

    Internal. Each catalog keeps the definitions it last accepted from
    registration (the restore point) and the retrieval description its index
    holds now (to re-index only what changed).
    """

    def __init__(self, kind: DefinitionOverrideKind, snapshot: Callable[[_Item], _Item]) -> None:
        """Create empty state for one catalog kind; ``snapshot`` copies a local definition."""
        self._kind = kind
        self._snapshot = cast(Callable[[Any], Any], snapshot)
        self._overrides: dict[str, str] = {}
        self._warned_ids: set[str] = set()
        self._local: dict[str, Any] = {}
        self._indexed: dict[str, str] = {}

    def effective_of(self, batch: Sequence[_Item]) -> list[_Item]:
        """What the index gets for a registration batch: the overlay applied."""
        return [self._effective(item) for item in batch]

    def accept(self, batch: Sequence[_Searchable], *, replace: bool = False) -> None:
        """Record an indexed registration batch as the local restore point.

        ``replace=True`` makes the batch the whole local corpus (a reload).
        """
        if replace:
            self._local = {}
            self._indexed = {}
        for item in batch:
            self._local[item.id] = self._snapshot(item)

    def stage(self, overrides: Mapping[str, str]) -> tuple[list[Any], list[Any]]:
        """Replace the overlay; return every effective definition and the changed ones."""
        self._overrides = dict(overrides)
        effective = [self._effective(item) for item in self._local.values()]
        changed = [
            item for item in effective if self._indexed.get(item.id) != _retrieval_text(item)
        ]
        return effective, changed

    def local(self, entry_id: str) -> Any:
        """The definition last accepted from registration for ``entry_id``."""
        return self._local[entry_id]

    def effective_all(self) -> list[Any]:
        """Every local definition with the overlay applied."""
        return [self._effective(item) for item in self._local.values()]

    def set(self, overrides: Mapping[str, str]) -> None:
        """Install an overlay on an empty catalog (one created after attach)."""
        self._overrides = dict(overrides)

    def mark_indexed(self, batch: Sequence[_Searchable]) -> None:
        """Note the retrieval descriptions the index now holds."""
        for item in batch:
            self._indexed[item.id] = _retrieval_text(item)

    def _effective(self, item: _Item) -> _Item:
        searchable = self._overrides.get(item.id)
        if searchable is None:
            self._warned_ids.discard(item.id)
            return item
        if item.experimental_searchable_description is None:
            self._warned_ids.discard(item.id)
        elif item.id not in self._warned_ids:
            _warn_override_shadow(self._kind, item.id)
            self._warned_ids.add(item.id)
        return cast(
            _Item,
            dataclasses.replace(cast(Any, item), experimental_searchable_description=searchable),
        )


class _Overrides:
    """Cross-catalog overlay application with rollback. Internal to `RuntimeCatalog`."""

    def __init__(
        self,
        tools: ToolCatalog,
        skills: SkillCatalog,
        facts: Callable[[], FactCatalog | None],
    ) -> None:
        self._tools = tools
        self._skills = skills
        self._facts = facts
        self._by_kind: dict[str, dict[str, str]] = {kind: {} for kind in _DEFINITION_OVERRIDE_KINDS}
        self.enabled = False

    async def apply(self, overrides: Sequence[ExperimentalDefinitionOverride]) -> None:
        next_by_kind: dict[str, dict[str, str]] = {kind: {} for kind in _DEFINITION_OVERRIDE_KINDS}
        for override in overrides:
            next_by_kind[override.kind][override.entry_id] = override.searchable_description
        facts_at_start = self._facts()
        failures = await self._stage(next_by_kind, facts_at_start)
        late_facts = self._facts()
        if not failures and late_facts is not None and late_facts is not facts_at_start:
            failures = await _settle(
                [lambda: late_facts._experimental_apply_definition_overrides(next_by_kind["fact"])]
            )
        if failures:
            rollback_failures = await self._stage(self._by_kind, self._facts())
            message = (
                "definition overlay apply failed; previous definitions restored"
                if not rollback_failures
                else "definition overlay apply failed and rollback did not complete"
            )
            causes = [*failures, *rollback_failures]
            raise DefinitionOverlayError(message, "apply_failed") from causes[0]
        self._by_kind = next_by_kind
        self.enabled = True
        self._tools._experimental_enable_definition_overrides()
        self._skills._experimental_enable_definition_overrides()
        facts = self._facts()
        if facts is not None:
            facts._experimental_enable_definition_overrides()

    def prepare_fact_catalog(self, facts: FactCatalog) -> None:
        """Give a fact catalog created after attach the active overlay."""
        if self.enabled:
            facts._experimental_set_definition_overrides(self._by_kind["fact"])

    async def _stage(
        self, by_kind: Mapping[str, Mapping[str, str]], facts: FactCatalog | None
    ) -> list[Exception]:
        staged: list[Callable[[], Awaitable[None]]] = [
            lambda: self._tools._experimental_apply_definition_overrides(
                by_kind["tool"], adopt=False, emit_definitions=False
            ),
            lambda: self._skills._experimental_apply_definition_overrides(
                by_kind["skill"], adopt=False, emit_definitions=False
            ),
        ]
        if facts is not None:
            staged.append(
                lambda: facts._experimental_apply_definition_overrides(
                    by_kind["fact"], adopt=False, emit_definitions=False
                )
            )
        return await _settle(staged)


async def no_dense_work() -> None:
    """The awaitable an overlay apply returns when no entry needs re-indexing."""


async def _settle(steps: Sequence[Callable[[], Awaitable[None]]]) -> list[Exception]:
    async def run(step: Callable[[], Awaitable[None]]) -> None:
        await step()

    results = await asyncio.gather(*(run(step) for step in steps), return_exceptions=True)
    failures: list[Exception] = []
    for result in results:
        if isinstance(result, Exception):
            failures.append(result)
        elif isinstance(result, BaseException):
            raise result
    return failures


def _validate_override(index: int, candidate: object) -> ExperimentalDefinitionOverride:
    if not isinstance(candidate, Mapping):
        raise _invalid_override(index, "must be an object")
    kind = candidate.get("kind")
    if kind not in _DEFINITION_OVERRIDE_KINDS:
        raise _invalid_override(index, "kind must be tool, skill, or fact")
    entry_id = candidate.get("entry_id")
    if not isinstance(entry_id, str):
        raise _invalid_override(index, "entry_id must be a string")
    if len(entry_id.encode()) > _MAX_ENTRY_ID_BYTES:
        raise _invalid_override(index, f"entry_id exceeds {_MAX_ENTRY_ID_BYTES} UTF-8 bytes")
    searchable = candidate.get("searchable_description")
    if not isinstance(searchable, str):
        raise _invalid_override(index, "searchable_description must be a string")
    if len(searchable.encode()) > _MAX_SEARCHABLE_DESCRIPTION_BYTES:
        raise _invalid_override(
            index,
            f"searchable_description exceeds {_MAX_SEARCHABLE_DESCRIPTION_BYTES} UTF-8 bytes",
        )
    return ExperimentalDefinitionOverride(cast(DefinitionOverrideKind, kind), entry_id, searchable)


def _invalid_override(index: int, message: str) -> DefinitionOverlayError:
    return DefinitionOverlayError(
        f"definition overlay override {index} {message}", "invalid_payload"
    )


def _retrieval_text(item: _Searchable) -> str:
    override = item.experimental_searchable_description
    return item.description if override is None else override


def _warn_override_shadow(kind: str, entry_id: str) -> None:
    warnings.warn(
        f'ratel: definition override for {kind} "{entry_id}" shadows the local '
        "experimental_searchable_description while definition overrides are enabled",
        RuntimeWarning,
        stacklevel=2,
    )
