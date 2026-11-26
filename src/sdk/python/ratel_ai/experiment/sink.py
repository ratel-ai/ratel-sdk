"""Evaluation records and the sinks that project them (internal).

The Python mirror of `src/sdk/ts/src/experiment-sink.ts`. One experiment feeds
one sink. `experimental_define_experiment` composes the OpenTelemetry sink with
the runtime-event sink, and the composite mints each event id once, so the span
or log record and the runtime event of one fact carry the same id.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, cast

from ratel_ai_telemetry import ExperimentArmOutcome, ExperimentArmRole

from ..runtime_events import new_runtime_event_id
from .definitions import ExperimentRankedItem

if TYPE_CHECKING:
    from ..runtime_events import RuntimeEvents

T = TypeVar("T")


class _Identified(Protocol):
    @property
    def event_id(self) -> str | None: ...


_Record = TypeVar("_Record", bound=_Identified)


@dataclass(frozen=True)
class ExperimentArmEvaluation:
    """Identity and dispatch facts of one experiment arm."""

    experiment_id: str
    selection_id: str
    unit_hash: str
    arm: str
    role: ExperimentArmRole
    cold: bool
    attributes: Mapping[str, Any] | None = None
    event_id: str | None = None


@dataclass(frozen=True)
class ExperimentArmCompletionEvaluation:
    """Observable completion facts of one experiment arm."""

    outcome: ExperimentArmOutcome
    duration_ms: float
    hit_count: int | None = None
    ranking: Sequence[ExperimentRankedItem] | None = None
    failure: BaseException | None = None
    ranking_failure: BaseException | None = None
    result_attributes_failure: BaseException | None = None
    event_id: str | None = None


@dataclass(frozen=True)
class ExperimentFallbackEvaluation:
    """One successful fallback decision."""

    effective_arm: str
    reused_shadow: bool
    event_id: str | None = None


class ExperimentArmEvaluationHandle:
    """Keeps the explicit context of one arm and its span together."""

    def run(self, callback: Callable[[], T]) -> T:
        """Run ``callback`` inside the arm context."""
        return callback()

    def complete(self, evaluation: ExperimentArmCompletionEvaluation) -> None:
        """Record the completion of the arm."""

    def event(
        self, event_name: str, attributes: Mapping[str, Any], event_id: str | None = None
    ) -> None:
        """Emit one event in the arm context."""


class ExperimentEvaluationSink:
    """Receives the evaluation records of one experiment. Every hook is optional."""

    def arm(self, evaluation: ExperimentArmEvaluation) -> ExperimentArmEvaluationHandle | None:
        """Start one arm and return its handle."""
        return None

    def fallback(
        self,
        evaluation: ExperimentFallbackEvaluation,
        arm: ExperimentArmEvaluationHandle | None = None,
    ) -> None:
        """Record one fallback decision."""


def compose_experiment_evaluation_sinks(
    first: ExperimentEvaluationSink, second: ExperimentEvaluationSink
) -> ExperimentEvaluationSink:
    """Fan records out to two sinks and mint each event id once."""
    return _ComposedSink(first, second)


def create_experiment_runtime_event_sink(events: RuntimeEvents) -> ExperimentEvaluationSink:
    """Project evaluation records into the public runtime-event vocabulary."""
    return _RuntimeEventSink(events)


def _stamp(evaluation: _Record) -> _Record:
    if evaluation.event_id is not None:
        return evaluation
    stamped = dataclasses.replace(cast(Any, evaluation), event_id=new_runtime_event_id())
    return cast(_Record, stamped)


class _ComposedHandle(ExperimentArmEvaluationHandle):
    def __init__(
        self,
        first: ExperimentArmEvaluationHandle | None,
        second: ExperimentArmEvaluationHandle | None,
    ) -> None:
        self.first = first
        self.second = second

    def run(self, callback: Callable[[], T]) -> T:
        second = self.second

        def run_second() -> T:
            return callback() if second is None else second.run(callback)

        return run_second() if self.first is None else self.first.run(run_second)

    def complete(self, evaluation: ExperimentArmCompletionEvaluation) -> None:
        stamped = _stamp(evaluation)
        for handle in (self.first, self.second):
            if handle is not None:
                handle.complete(stamped)

    def event(
        self, event_name: str, attributes: Mapping[str, Any], event_id: str | None = None
    ) -> None:
        event_id = event_id or new_runtime_event_id()
        for handle in (self.first, self.second):
            if handle is not None:
                handle.event(event_name, attributes, event_id)


class _ComposedSink(ExperimentEvaluationSink):
    def __init__(self, first: ExperimentEvaluationSink, second: ExperimentEvaluationSink) -> None:
        self._first = first
        self._second = second

    def arm(self, evaluation: ExperimentArmEvaluation) -> ExperimentArmEvaluationHandle | None:
        stamped = _stamp(evaluation)
        return _ComposedHandle(self._first.arm(stamped), self._second.arm(stamped))

    def fallback(
        self,
        evaluation: ExperimentFallbackEvaluation,
        arm: ExperimentArmEvaluationHandle | None = None,
    ) -> None:
        stamped = _stamp(evaluation)
        first, second = _split(arm)
        self._first.fallback(stamped, first)
        self._second.fallback(stamped, second)


def _split(
    handle: ExperimentArmEvaluationHandle | None,
) -> tuple[ExperimentArmEvaluationHandle | None, ExperimentArmEvaluationHandle | None]:
    if isinstance(handle, _ComposedHandle):
        return handle.first, handle.second
    return handle, handle


class _RuntimeEventArmHandle(ExperimentArmEvaluationHandle):
    def __init__(self, sink: _RuntimeEventSink, evaluation: ExperimentArmEvaluation) -> None:
        self.sink = sink
        self.evaluation = evaluation

    def complete(self, evaluation: ExperimentArmCompletionEvaluation) -> None:
        fields: dict[str, Any] = {
            **_arm_fields(self.evaluation),
            "outcome": evaluation.outcome.value,
            "duration_ms": evaluation.duration_ms,
        }
        if evaluation.hit_count is not None:
            fields["hit_count"] = evaluation.hit_count
        if evaluation.ranking is not None:
            fields["result_ids"] = [item["id"] for item in evaluation.ranking]
            if all("score" in item for item in evaluation.ranking):
                fields["result_scores"] = [item["score"] for item in evaluation.ranking]
        if evaluation.failure is not None:
            fields["error_class"] = type(evaluation.failure).__name__
        self.sink.emit("experiment_results", evaluation.event_id, fields)


class _RuntimeEventSink(ExperimentEvaluationSink):
    def __init__(self, events: RuntimeEvents) -> None:
        self._events = events

    def emit(self, event_type: str, event_id: str | None, fields: Mapping[str, Any]) -> None:
        event: dict[str, Any] = {"type": event_type, **fields}
        if event_id is not None:
            event["event_id"] = event_id
        self._events.emit(event)

    def arm(self, evaluation: ExperimentArmEvaluation) -> ExperimentArmEvaluationHandle | None:
        fields: dict[str, Any] = {**_arm_fields(evaluation), "cold": evaluation.cold}
        if evaluation.attributes is not None:
            fields["attributes"] = dict(evaluation.attributes)
        self.emit("experiment_selection", evaluation.event_id, fields)
        return _RuntimeEventArmHandle(self, evaluation)

    def fallback(
        self,
        evaluation: ExperimentFallbackEvaluation,
        arm: ExperimentArmEvaluationHandle | None = None,
    ) -> None:
        self.emit(
            "experiment_fallback",
            evaluation.event_id,
            {
                **_handle_fields(arm),
                "effective_arm": evaluation.effective_arm,
                "reused_shadow": evaluation.reused_shadow,
            },
        )


def _arm_fields(evaluation: ExperimentArmEvaluation) -> dict[str, Any]:
    return {
        "experiment_id": evaluation.experiment_id,
        "selection_id": evaluation.selection_id,
        "unit": evaluation.unit_hash,
        "arm": evaluation.arm,
        "role": evaluation.role.value,
    }


def _handle_fields(handle: ExperimentArmEvaluationHandle | None) -> dict[str, Any]:
    if isinstance(handle, _RuntimeEventArmHandle):
        return _arm_fields(handle.evaluation)
    return {}
