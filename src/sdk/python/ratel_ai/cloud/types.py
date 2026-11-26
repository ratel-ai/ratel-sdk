"""Wire types for the Ratel Cloud APIs.

The `TypedDict` shapes mirror the server serializers field for field, so their
keys are the JSON keys the API sends and receives (camelCase). The catalog wire
shapes and their ETag algorithms are frozen protocol contracts, pinned by the
shared vectors under ``protocol/v1/conformance`` and ``protocol/v2/conformance``.

Results that the SDK builds itself (not wire bodies) are dataclasses with
snake_case attributes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict, Union

__all__ = [
    "JOB_STATUSES",
    "SIGNAL_KINDS",
    "SKILL_FIELDS",
    "SKILL_STATUSES",
    "SUGGESTION_STATUSES",
    "SUGGESTION_TYPES",
    "V2_SKILL_FIELDS",
    "AnalyzeInput",
    "AnalyzeResult",
    "Catalog",
    "CatalogResponse",
    "CatalogResponseV2",
    "CatalogV2",
    "CloudSkill",
    "CloudSuggestion",
    "ConversationMessage",
    "EditSkillPatch",
    "ExtractedIntent",
    "GenerateSuggestionsResult",
    "ImportReport",
    "Job",
    "JobStatus",
    "ListIntentsResult",
    "ListSkillsResult",
    "ListSuggestionsResult",
    "MessageRole",
    "NewSkillInput",
    "NewSkillPatch",
    "QueryIntent",
    "RetrievabilityPreview",
    "RetrievabilityPreviewHit",
    "RuntimeCatalogEntryKind",
    "RuntimeCatalogOverride",
    "RuntimeCatalogOverridesFresh",
    "RuntimeCatalogOverridesNotModified",
    "RuntimeCatalogOverridesResult",
    "SignalKind",
    "SkillStatus",
    "SuggestJobReason",
    "SuggestJobResult",
    "SuggestResult",
    "SuggestionStatus",
    "SuggestionType",
    "UpdateSkillInput",
    "WireSkill",
    "WireSkillV2",
]

# — protocol/v1 and protocol/v2 catalog wire ————————————————————————————————

#: The frozen v1 content projection: exactly these fields, in this order.
SKILL_FIELDS = ("id", "name", "description", "tags", "tools", "metadata", "body")

#: The frozen v2 content projection: v1 plus the Retrieval description.
V2_SKILL_FIELDS = (
    "id",
    "name",
    "description",
    "searchableDescription",
    "tags",
    "tools",
    "metadata",
    "body",
)


class WireSkill(TypedDict):
    """One skill as it crosses the v1 wire."""

    id: str
    name: str
    description: str
    tags: list[str]
    tools: list[str]
    metadata: dict[str, list[str]]
    body: str


class WireSkillV2(WireSkill, total=False):
    """One skill on the v2 wire. An omitted or `None` override is unset."""

    searchableDescription: str | None


# ``global`` is a Python keyword, so the required layer uses the functional syntax.
_CatalogRequired = TypedDict("_CatalogRequired", {"global": list[WireSkill]})
_CatalogV2Required = TypedDict("_CatalogV2Required", {"global": list[WireSkillV2]})


class Catalog(_CatalogRequired, total=False):
    """A source-side v1 catalog: a ``global`` layer and optional per-subject layers."""

    subjects: dict[str, list[WireSkill]]


class CatalogV2(_CatalogV2Required, total=False):
    """A source-side v2 catalog: a ``global`` layer and optional per-subject layers."""

    subjects: dict[str, list[WireSkillV2]]


class CatalogResponse(TypedDict):
    """The ``GET /catalog`` 200 body."""

    catalogVersion: str
    skills: list[WireSkill]


class CatalogResponseV2(TypedDict):
    """The ``GET /v2/catalog`` 200 body."""

    catalogVersion: str
    skills: list[WireSkillV2]


# — runtime catalog overrides —————————————————————————————————————————————

RuntimeCatalogEntryKind = Literal["tool", "skill", "fact"]


class RuntimeCatalogOverride(TypedDict):
    """One operator-authored Retrieval description for a runtime catalog entry."""

    kind: RuntimeCatalogEntryKind
    entryId: str
    searchableDescription: str


@dataclass(frozen=True)
class RuntimeCatalogOverridesFresh:
    """A fresh runtime-catalog overlay and its cache validator.

    Attributes:
        etag: The ``ETag`` to send back as ``if_none_match``, or `None`.
        overrides: The current overrides.
        not_modified: Always `False`.
    """

    etag: str | None
    overrides: list[RuntimeCatalogOverride] = field(default_factory=list)
    not_modified: Literal[False] = False


@dataclass(frozen=True)
class RuntimeCatalogOverridesNotModified:
    """An unchanged runtime-catalog overlay: keep the cached overrides.

    Attributes:
        etag: The current ``ETag``, or `None`.
        not_modified: Always `True`.
    """

    etag: str | None
    not_modified: Literal[True] = True


#: The fresh and not-modified outcomes of the conditional overlay pull.
RuntimeCatalogOverridesResult = Union[
    RuntimeCatalogOverridesFresh, RuntimeCatalogOverridesNotModified
]

# — managed skills (write surface) ————————————————————————————————————————

SKILL_STATUSES = ("draft", "published", "archived")
SkillStatus = Literal["draft", "published", "archived"]


class CloudSkill(TypedDict):
    """A managed skill row as the write surface serializes it."""

    id: str
    name: str
    description: str
    searchableDescription: str | None
    tags: list[str]
    tools: list[str]
    metadata: dict[str, list[str]]
    body: str
    status: SkillStatus
    version: int
    endUserId: str | None
    model: str | None
    createdAt: str
    updatedAt: str
    publishedAt: str | None


class _NewSkillInputRequired(TypedDict):
    name: str
    description: str
    body: str


class NewSkillInput(_NewSkillInputRequired, total=False):
    """Input for creating a skill. ``name`` must be kebab-case."""

    searchableDescription: str | None
    tags: list[str]
    tools: list[str]
    metadata: dict[str, list[str]]
    status: Literal["draft", "published"]
    endUserId: str | None


class UpdateSkillInput(TypedDict, total=False):
    """Input for editing a skill.

    ``expectedVersion`` opts into the version compare-and-swap. Omitted, the edit
    applies unconditionally.
    """

    expectedVersion: int
    name: str
    description: str
    searchableDescription: str | None
    body: str
    tags: list[str]
    tools: list[str]
    metadata: dict[str, list[str]]
    endUserId: str | None


class ListSkillsResult(TypedDict):
    """The ``GET /skills`` body."""

    count: int
    skills: list[CloudSkill]


class ImportReport(TypedDict):
    """The ``POST /skills/import`` change report."""

    created: list[str]
    updated: list[str]
    unchanged: list[str]


# — suggestions ———————————————————————————————————————————————————————————

SUGGESTION_TYPES = ("edit_skill", "new_skill")
SuggestionType = Literal["edit_skill", "new_skill"]

SUGGESTION_STATUSES = ("pending", "approved", "rejected", "auto_applied", "superseded")
SuggestionStatus = Literal["pending", "approved", "rejected", "auto_applied", "superseded"]

SIGNAL_KINDS = ("coverage_gap", "surfaced_not_invoked", "tool_error")
SignalKind = Literal["coverage_gap", "surfaced_not_invoked", "tool_error"]


class EditSkillPatch(TypedDict, total=False):
    """The partial patch an ``edit_skill`` proposal carries (never ``name``)."""

    description: str
    tags: list[str]
    body: str


class NewSkillPatch(TypedDict):
    """The full drafted skill a ``new_skill`` proposal carries."""

    name: str
    description: str
    tags: list[str]
    body: str
    model: str


class RetrievabilityPreviewHit(TypedDict):
    """One representative query's rank and score in a catalog snapshot."""

    query: str
    rank: int | None
    score: float | None


class RetrievabilityPreview(TypedDict):
    """The before/after retrievability preview attached to a proposal."""

    queries: list[str]
    before: list[RetrievabilityPreviewHit]
    after: list[RetrievabilityPreviewHit]


class CloudSuggestion(TypedDict):
    """A suggestion as the server serializes it."""

    id: str
    projectId: str
    type: SuggestionType
    signalKind: SignalKind
    status: SuggestionStatus
    rationale: str
    evidence: dict[str, Any]
    targetSkillId: str | None
    targetSkillExpectedVersion: int | None
    sourceQueryIntentId: str | None
    endUserId: str | None
    patch: dict[str, Any]
    retrievabilityPreview: dict[str, Any]
    createdSkillId: str | None
    model: str | None
    createdAt: str
    updatedAt: str
    reviewedAt: str | None
    appliedAt: str | None


class ListSuggestionsResult(TypedDict):
    """The ``GET /suggestions`` body."""

    count: int
    suggestions: list[CloudSuggestion]


class GenerateSuggestionsResult(TypedDict):
    """The ``POST /suggestions/generate`` body.

    ``coalesced`` is `True` when an in-flight generation run absorbed the request.
    """

    jobId: str
    coalesced: bool


# — intent analysis ———————————————————————————————————————————————————————

MessageRole = Literal["user", "assistant", "system", "tool"]


class ConversationMessage(TypedDict):
    """One conversation turn submitted for analysis."""

    role: MessageRole
    content: str


class AnalyzeInput(TypedDict, total=False):
    """The ``POST /intents/analyze`` body.

    ``noCache`` skips the server's stored-run cache and replaces the stored run.
    Use it for tests and debugging only.
    """

    messages: list[ConversationMessage]
    endUserId: str
    conversationId: str
    noCache: bool


class ExtractedIntent(TypedDict):
    """One extracted intent with its skill-coverage verdict."""

    id: str
    text: str
    covered: bool
    matchedSkillId: str | None
    score: float | None


class AnalyzeResult(TypedDict):
    """The ``POST /intents/analyze`` result. Analysis does not draft skills."""

    runId: str
    cached: bool
    catalogVersion: str | None
    intents: list[ExtractedIntent]


class QueryIntent(TypedDict):
    """One recurring ask in the project's intent ledger (``GET /intents``)."""

    id: str
    text: str
    occurrences: int
    firstSeenAt: str
    lastSeenAt: str


class ListIntentsResult(TypedDict):
    """The ``GET /intents`` page."""

    count: int
    page: int
    pageSize: int
    total: int
    intents: list[QueryIntent]


# — async jobs (drafting) —————————————————————————————————————————————————


class _SuggestResultRequired(TypedDict):
    jobId: str


class SuggestResult(_SuggestResultRequired, total=False):
    """The ``POST /intents/{id}/suggest`` body: a drafting job to poll."""

    coalesced: bool


JOB_STATUSES = ("queued", "running", "done", "error")
JobStatus = Literal["queued", "running", "done", "error"]

#: Why a ``suggest_skill`` job finished without a suggestion.
SuggestJobReason = Literal["not_configured", "exists"]


class _SuggestJobResultRequired(TypedDict):
    suggestionId: str | None


class SuggestJobResult(_SuggestJobResultRequired, total=False):
    """The ``result`` of a completed ``suggest_skill`` job."""

    reason: SuggestJobReason


class Job(TypedDict):
    """An async job as ``GET /jobs/{id}`` serializes it.

    ``result`` and ``error`` are set once ``status`` is terminal (``done`` or
    ``error``). For ``suggest_skill`` jobs, ``result`` is a `SuggestJobResult`.
    """

    id: str
    kind: str
    status: JobStatus
    result: Any
    error: str | None
