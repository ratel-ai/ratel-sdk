"""Public types of the experimental retrieval-experiment API.

The Python mirror of `src/sdk/ts/src/experiment-types.ts`, in snake_case.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Generic, Literal, TypedDict, TypeVar, Union

from ratel_ai_telemetry import ExperimentArmOutcome, ExperimentArmRole

ResultT = TypeVar("ResultT")

#: A scalar experiment attribute value.
ExperimentScalar = Union[str, int, float, bool, None]

#: Non-content scalar attributes copied to experiment telemetry.
ExperimentAttributes = Mapping[str, Union[str, int, float, bool]]


class _ExperimentRankedItemRequired(TypedDict):
    id: str


class ExperimentRankedItem(_ExperimentRankedItemRequired, total=False):
    """One ranked measurement projected from an arm result.

    ``id`` is a stable, non-content identifier used for rank comparison. ``score``
    is an arm-specific score: it is emitted but never compared across arms.
    ``attrs`` holds item facts used by item-level evaluation.
    """

    score: float
    attrs: Mapping[str, ExperimentScalar]


class ExperimentSplitAllocation(TypedDict):
    """One allocation of a deterministic split.

    ``arm`` is a declared arm. ``weight`` is a non-negative safe integer.
    """

    arm: str
    weight: int


#: Ordered integer-weight allocation used for deterministic arm assignment.
ExperimentSplit = Sequence[ExperimentSplitAllocation]


class ExperimentInvocationWindow(TypedDict, total=False):
    """Bounds of the in-process selection window used for attribution.

    ``turns`` keeps the last N completed selections for the unit. ``max_age_ms``
    keeps the selections no older than this elapsed duration.
    """

    turns: int
    max_age_ms: float


class _ExperimentInvocationReferenceRequired(TypedDict):
    kind: Literal["invocation"]
    window: ExperimentInvocationWindow


class ExperimentInvocationReference(_ExperimentInvocationReferenceRequired, total=False):
    """Invocation attribution as an evaluation source.

    ``attribution`` selects only the newest match (``"last-selection"``, the
    default) or every match in the window (``"all-in-window"``).
    """

    attribution: Literal["last-selection", "all-in-window"]


#: A served-vs-shadow comparison or invocation-attribution evaluation source.
ExperimentEvaluationReference = Union[Literal["peer-selection"], ExperimentInvocationReference]


@dataclass(frozen=True)
class ExperimentArmContext:
    """What an arm selector receives with the caller parameters.

    Attributes:
        role: The immutable dispatch role of this arm run.
    """

    role: ExperimentArmRole


#: An arm selector: runs one arm with the exact caller parameters.
ExperimentArmSelect = Callable[[Any, ExperimentArmContext], Awaitable[Any]]


# TypedDicts stay non-generic: generic TypedDicts need Python 3.11 at runtime and
# the floor is 3.9. Params and results are therefore typed as Any here.
class _ExperimentArmRequired(TypedDict):
    select: Callable[[Any, ExperimentArmContext], Awaitable[Any]]


class ExperimentArm(_ExperimentArmRequired, total=False):
    """One named host-supplied selector.

    ``warmup`` is an optional readiness warmup. A failure keeps the arm cold and
    permits a retry.
    """

    warmup: Callable[[], Awaitable[None]]


class _ExperimentEvaluationRequired(TypedDict):
    references: Sequence[ExperimentEvaluationReference]


class ExperimentEvaluation(_ExperimentEvaluationRequired, total=False):
    """Comparison and attribution policy.

    ``references`` is the non-empty list of evaluation sources. ``k`` is the
    default Jaccard window. ``attributes`` projects result-level facts used for
    served-vs-shadow agreement. ``outcome`` turns on delayed outcome reports.
    """

    k: int
    attributes: Callable[[Any], Mapping[str, ExperimentScalar]]
    outcome: bool


class ExperimentShadowPolicy(TypedDict, total=False):
    """Detached shadow capacity policy.

    ``concurrency`` is the per-instance skip-never-queue limit. It defaults to one.
    """

    concurrency: int


class _ExperimentConfigRequired(TypedDict):
    id: str
    arms: Mapping[str, ExperimentArm]
    ranking: Callable[[Any], Sequence[ExperimentRankedItem]]
    evaluation: ExperimentEvaluation


class ExperimentConfig(_ExperimentConfigRequired, total=False):
    """Definition-time arms, assignment, evaluation and lifecycle policy.

    ``id`` is the stable experiment identifier included in deterministic
    assignment. ``arms`` maps arm names to selectors. ``split`` is the
    deterministic allocation used when a call does not name an arm. ``ranking``
    projects a transformed result to ranked measurement items. ``fallback_arm``
    is used only when the assigned selector raises.
    """

    split: ExperimentSplit
    fallback_arm: str
    shadow_policy: ExperimentShadowPolicy


@dataclass(frozen=True)
class ExperimentSelection(Generic[ResultT]):
    """The effective result and correlation data returned by a selection.

    Attributes:
        result: The transformed result that reaches the caller.
        selection_id: Opaque identifier that joins later reports and telemetry.
        assigned_arm: The arm chosen explicitly or by the deterministic split.
        effective_arm: The arm whose result reached the caller after any fallback.
        duration_ms: The effective arm callback duration, without transform work.
    """

    result: ResultT
    selection_id: str
    assigned_arm: str
    effective_arm: str
    duration_ms: float


__all__ = [
    "ExperimentArm",
    "ExperimentArmContext",
    "ExperimentArmOutcome",
    "ExperimentArmRole",
    "ExperimentArmSelect",
    "ExperimentAttributes",
    "ExperimentConfig",
    "ExperimentEvaluation",
    "ExperimentEvaluationReference",
    "ExperimentInvocationReference",
    "ExperimentInvocationWindow",
    "ExperimentRankedItem",
    "ExperimentScalar",
    "ExperimentSelection",
    "ExperimentShadowPolicy",
    "ExperimentSplit",
    "ExperimentSplitAllocation",
]
