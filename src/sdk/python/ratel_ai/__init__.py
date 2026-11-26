"""Python SDK for Ratel — context engineering for AI agents.

Mirrors the public surface of the TypeScript SDK (`@ratel-ai/sdk`):
- `ratel(...)` / `Ratel` — the one construction point: shared tool, skill and
  fact catalogs, the three capability tools, recall and one event stream.
- `ToolRegistry` / `SearchHit`, `SkillRegistry` / `SkillHit` — metadata
  registries with synchronous BM25 and explicit async dense retrieval, one per
  corpus.
- `ToolCatalog` / `ExecutableTool` — registry plus executable handlers.
- `SkillCatalog` / `Skill` — the on-demand skill analogue of `ToolCatalog`.
- `search_capabilities_tool` / `invoke_tool_tool` / `get_skill_content_tool` —
  framework-neutral capability tools.
- `register_mcp_server` — ingest an upstream MCP server's tools (extra: mcp).
- `ToolCatalog.turn` / `current_turn_id`: mark one user request as one turn
  so everything inside carries its `turn_id` (ADR-0026).
- `RuntimeEvents` / `RuntimeCatalog` — subscribe to runtime facts and snapshot
  executor-free tool/skill state (ADR-0020).
- `ratel(cloud=...)` / `CloudOptions` — turn on Ratel Cloud for one core: runtime
  events, catalog snapshots, Cloud definitions, intent graph sync and the Tool
  Picker, plus the Cloud API on `Ratel.cloud`. The `ratel_ai.cloud` package
  loads only when `cloud` is set.

The facts/grounding surface (`FactCatalog` / `Fact`, the push-path grounding
analogue: constant content injected into the context, gated by the pure
`grounding` freshness planner) is **experimental** and lives in the opt-in
`ratel_ai.experimental` namespace, not here — it may change without a major
version bump.
"""

from typing import TYPE_CHECKING, Any

from ._custom_ranking import (
    RankCandidate,
    RankCandidateKind,
    RankedId,
    RankFn,
    RetrieverPlugin,
    define_plugin,
)
from ._native import SearchHit, SkillHit
from .capabilities import (
    INVOKE_TOOL_ID,
    SEARCH_CAPABILITIES_ID,
    OnUnauthorized,
    UpstreamServerInfo,
    format_upstream_line,
    invoke_tool_tool,
    search_capabilities_tool,
)
from .catalog import (
    AdaptiveRankingStatus,
    BaselineTurn,
    EmbeddingModelConfig,
    EmbeddingSpec,
    EndpointEmbeddingConfig,
    ExecutableTool,
    Executor,
    HuggingFaceEmbeddingConfig,
    IntentGraph,
    LocalEmbeddingConfig,
    OllamaEmbeddingConfig,
    OriginFilterOption,
    ProvenanceOption,
    RerankerConfig,
    SearchMethod,
    SearchOrigin,
    Tool,
    ToolCatalog,
    ToolRegistry,
    TraceSinkConfig,
)

# Deprecated pre-0.2.0 surface (see compat.py) — kept so `ratel-ai==0.1.x`
# callers keep working after upgrading to 0.2.0. Slated for removal (RAT-250).
from .compat import SEARCH_TOOLS_ID, search_tools_tool
from .embedding_artifact import (
    ExperimentalEmbeddingArtifact,
    experimental_build_embedding_artifact,
)
from .exceptions import (
    ArtifactError,
    ArtifactWarmError,
    DimensionMismatchError,
    EmbedderError,
    IncompatibleMergeError,
    RetrieverError,
)
from .intent_graph_storage import (
    IntentGraphStorage,
    LocalFileIntentGraphStorage,
    S3IntentGraphStorage,
    S3IntentGraphStorageCredentials,
    S3Request,
    S3Response,
    S3Transport,
    StaleIntentGraphError,
)
from .mcp import McpServerHandle, McpToolsListError, register_mcp_server
from .ratel import Ratel, RatelEventsOptions, ToolCollection, ratel
from .runtime_events import (
    OPTIONAL_ENVELOPE_FIELDS,
    RUNTIME_EVENT_MAX_HITS,
    RUNTIME_EVENT_MAX_PAYLOAD_BYTES,
    RUNTIME_EVENT_MAX_QUERY_BYTES,
    RUNTIME_EVENT_TYPES,
    RuntimeCatalog,
    RuntimeEvent,
    RuntimeEventHandler,
    RuntimeEvents,
    RuntimeEventSubscription,
)
from .skill_catalog import PendingReplace, ReplaceOutcome, Skill, SkillCatalog, SkillRegistry
from .skill_tools import GET_SKILL_CONTENT_ID, get_skill_content_tool

# OpenTelemetry export of the ratel.*/gen_ai.* funnel (ADR-0007). The SDK always
# emits spans to the active OTel provider (a no-op until one is wired);
# configure_telemetry is optional sugar that installs a Ratel-owned OTLP exporter
# (needs the [otlp] extra).
from .telemetry import configure_telemetry
from .turns import TURN_USER_MESSAGE_MAX_BYTES, Turn, current_turn_id

if TYPE_CHECKING:
    from .cloud.ratel_cloud import (
        CloudEventsOptions,
        CloudIntentGraphOptions,
        CloudOptions,
        RatelCloudRuntime,
    )

# The `ratel(cloud=...)` types live in `ratel_ai.cloud`. They load on first access,
# so `import ratel_ai` never loads the Cloud package.
_CLOUD_EXPORTS = frozenset(
    {"CloudEventsOptions", "CloudIntentGraphOptions", "CloudOptions", "RatelCloudRuntime"}
)

__all__ = [
    "AdaptiveRankingStatus",
    "ArtifactError",
    "ArtifactWarmError",
    "ExperimentalEmbeddingArtifact",
    "IntentGraph",
    "IncompatibleMergeError",
    "GET_SKILL_CONTENT_ID",
    "INVOKE_TOOL_ID",
    "SEARCH_CAPABILITIES_ID",
    "SEARCH_TOOLS_ID",
    "DimensionMismatchError",
    "EmbedderError",
    "EmbeddingModelConfig",
    "EmbeddingSpec",
    "EndpointEmbeddingConfig",
    "ExecutableTool",
    "Executor",
    "IntentGraphStorage",
    "LocalFileIntentGraphStorage",
    "S3IntentGraphStorage",
    "S3IntentGraphStorageCredentials",
    "HuggingFaceEmbeddingConfig",
    "LocalEmbeddingConfig",
    "McpServerHandle",
    "McpToolsListError",
    "OnUnauthorized",
    "OllamaEmbeddingConfig",
    "PendingReplace",
    "ReplaceOutcome",
    "RankCandidate",
    "RankCandidateKind",
    "RankedId",
    "RankFn",
    "Ratel",
    "RatelEventsOptions",
    "RerankerConfig",
    "RetrieverError",
    "RetrieverPlugin",
    "RuntimeCatalog",
    "RuntimeEvent",
    "RuntimeEventHandler",
    "RuntimeEventSubscription",
    "RuntimeEvents",
    "OPTIONAL_ENVELOPE_FIELDS",
    "RUNTIME_EVENT_MAX_HITS",
    "RUNTIME_EVENT_MAX_PAYLOAD_BYTES",
    "RUNTIME_EVENT_MAX_QUERY_BYTES",
    "RUNTIME_EVENT_TYPES",
    "S3Request",
    "S3Response",
    "S3Transport",
    "SearchHit",
    "OriginFilterOption",
    "ProvenanceOption",
    "SearchMethod",
    "SearchOrigin",
    "Skill",
    "SkillCatalog",
    "SkillHit",
    "SkillRegistry",
    "StaleIntentGraphError",
    "Tool",
    "BaselineTurn",
    "CloudEventsOptions",
    "CloudIntentGraphOptions",
    "CloudOptions",
    "RatelCloudRuntime",
    "ToolCatalog",
    "ToolCollection",
    "ToolRegistry",
    "TraceSinkConfig",
    "Turn",
    "TURN_USER_MESSAGE_MAX_BYTES",
    "UpstreamServerInfo",
    "configure_telemetry",
    "current_turn_id",
    "experimental_build_embedding_artifact",
    "define_plugin",
    "format_upstream_line",
    "get_skill_content_tool",
    "invoke_tool_tool",
    "ratel",
    "register_mcp_server",
    "search_capabilities_tool",
    "search_tools_tool",
]


def __getattr__(name: str) -> Any:
    """Load the `ratel(cloud=...)` types from `ratel_ai.cloud` on first access."""
    if name in _CLOUD_EXPORTS:
        from .cloud import ratel_cloud

        return getattr(ratel_cloud, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
