"""Dispatch of one experiment arm (internal).

The Python mirror of `src/sdk/ts/src/experiment-arm.ts`: run the selector under
the arm's telemetry context, apply the caller's transform, project the ranking and
record the completion. Telemetry failures never fail the arm.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from ratel_ai_telemetry import ExperimentArmOutcome, ExperimentArmRole

from .definitions import ExperimentArmContext, ExperimentConfig, ExperimentRankedItem
from .sink import (
    ExperimentArmCompletionEvaluation,
    ExperimentArmEvaluation,
    ExperimentArmEvaluationHandle,
    ExperimentEvaluationSink,
)


@dataclass(frozen=True)
class ResultAttributesProjection:
    """Result-level attributes captured for served-vs-shadow agreement."""

    attributes: Mapping[str, Any] | None = None
    error: BaseException | None = None


@dataclass(frozen=True)
class ArmCompletion:
    """Terminal outcome of one arm callback, transform and ranking projection.

    ``ok`` is false when the selector or the transform raised. Then ``error`` and
    ``source`` say what raised. A ranking failure keeps ``ok`` true: the result can
    still serve the caller.
    """

    ok: bool
    duration_ms: float
    outcome: ExperimentArmOutcome
    result: Any = None
    ranking: Sequence[ExperimentRankedItem] | None = None
    ranking_failure: BaseException | None = None
    result_attributes: ResultAttributesProjection | None = None
    error: BaseException | None = None
    source: Literal["select", "transform"] | None = None


@dataclass(frozen=True)
class ArmRun:
    """A dispatched arm and its pending completion."""

    arm: str
    cold: bool
    role: ExperimentArmRole
    telemetry: ExperimentArmEvaluationHandle | None
    completion: asyncio.Future[ArmCompletion]


@dataclass(frozen=True)
class ArmDispatch:
    """The per-selection facts every arm run of one selection shares."""

    config: ExperimentConfig
    params: Any
    transform: Callable[[Any], Any] | None
    attributes: Mapping[str, Any] | None
    selection_id: str
    unit_hash: str
    sink: ExperimentEvaluationSink


def start_arm(
    dispatch: ArmDispatch,
    arm: str,
    role: ExperimentArmRole,
    cold: bool,
    on_callback_settled: Callable[[], None] | None = None,
) -> ArmRun:
    """Start one arm as a task inside its telemetry context."""
    telemetry = _start_arm_telemetry(
        dispatch.sink,
        ExperimentArmEvaluation(
            experiment_id=dispatch.config["id"],
            selection_id=dispatch.selection_id,
            unit_hash=dispatch.unit_hash,
            arm=arm,
            role=role,
            cold=cold,
            attributes=dispatch.attributes,
        ),
    )

    async def complete() -> ArmCompletion:
        completed = await _run_arm(dispatch, arm, role, on_callback_settled)
        _complete_arm_telemetry(telemetry, completed)
        return completed

    completion = _run_in_telemetry(telemetry, lambda: asyncio.ensure_future(complete()))
    return ArmRun(arm=arm, cold=cold, role=role, telemetry=telemetry, completion=completion)


async def _run_arm(
    dispatch: ArmDispatch,
    arm: str,
    role: ExperimentArmRole,
    on_callback_settled: Callable[[], None] | None,
) -> ArmCompletion:
    started = time.perf_counter()
    try:
        raw = await dispatch.config["arms"][arm]["select"](
            dispatch.params, ExperimentArmContext(role=role)
        )
    except Exception as error:
        duration_ms = _elapsed_ms(started)
        _settled(on_callback_settled)
        outcome = ExperimentArmOutcome.TIMEOUT if _is_timeout(error) else ExperimentArmOutcome.ERROR
        return ArmCompletion(
            ok=False, duration_ms=duration_ms, outcome=outcome, error=error, source="select"
        )
    duration_ms = _elapsed_ms(started)
    _settled(on_callback_settled)

    try:
        result = raw if dispatch.transform is None else dispatch.transform(raw)
    except Exception as error:
        return ArmCompletion(
            ok=False,
            duration_ms=duration_ms,
            outcome=ExperimentArmOutcome.ERROR,
            error=error,
            source="transform",
        )

    try:
        ranking = _snapshot_ranking(dispatch.config["ranking"](result))
    except Exception as error:
        return ArmCompletion(
            ok=True,
            duration_ms=duration_ms,
            outcome=ExperimentArmOutcome.ERROR,
            result=result,
            ranking_failure=error,
        )
    return ArmCompletion(
        ok=True,
        duration_ms=duration_ms,
        outcome=ExperimentArmOutcome.EMPTY if not ranking else ExperimentArmOutcome.OK,
        result=result,
        ranking=ranking,
        result_attributes=_project_result_attributes(dispatch.config, result),
    )


def _start_arm_telemetry(
    sink: ExperimentEvaluationSink, evaluation: ExperimentArmEvaluation
) -> ExperimentArmEvaluationHandle | None:
    try:
        return sink.arm(evaluation)
    except Exception:
        return None


def _complete_arm_telemetry(
    telemetry: ExperimentArmEvaluationHandle | None, completed: ArmCompletion
) -> None:
    if telemetry is None:
        return
    failure = completed.error if not completed.ok else completed.ranking_failure
    projection = completed.result_attributes
    try:
        telemetry.complete(
            ExperimentArmCompletionEvaluation(
                outcome=completed.outcome,
                duration_ms=completed.duration_ms,
                hit_count=None if completed.ranking is None else len(completed.ranking),
                ranking=completed.ranking,
                failure=failure,
                ranking_failure=completed.ranking_failure,
                result_attributes_failure=None if projection is None else projection.error,
            )
        )
    except Exception:
        pass  # Telemetry delivery is observational and cannot fail application work.


def _run_in_telemetry(
    telemetry: ExperimentArmEvaluationHandle | None,
    start: Callable[[], asyncio.Future[ArmCompletion]],
) -> asyncio.Future[ArmCompletion]:
    if telemetry is None:
        return start()
    started: list[asyncio.Future[ArmCompletion]] = []

    def start_once() -> asyncio.Future[ArmCompletion]:
        if not started:
            started.append(start())
        return started[0]

    try:
        telemetry.run(start_once)
    except Exception:
        pass  # A failing telemetry context still starts the arm, outside it.
    return start_once()


def _snapshot_ranking(ranking: Sequence[ExperimentRankedItem]) -> list[ExperimentRankedItem]:
    snapshot: list[ExperimentRankedItem] = []
    for item in ranking:
        copy: ExperimentRankedItem = {"id": item["id"]}
        if "score" in item:
            copy["score"] = item["score"]
        if "attrs" in item:
            copy["attrs"] = dict(item["attrs"])
        snapshot.append(copy)
    return snapshot


def _project_result_attributes(
    config: ExperimentConfig, result: Any
) -> ResultAttributesProjection | None:
    project = config["evaluation"].get("attributes")
    if project is None or "peer-selection" not in config["evaluation"]["references"]:
        return None
    try:
        return ResultAttributesProjection(attributes=dict(project(result)))
    except Exception as error:
        return ResultAttributesProjection(error=error)


def _is_timeout(error: BaseException) -> bool:
    return isinstance(error, (TimeoutError, asyncio.TimeoutError)) or (
        type(error).__name__ == "TimeoutError"
    )


def _settled(callback: Callable[[], None] | None) -> None:
    if callback is not None:
        callback()


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1_000
