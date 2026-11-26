"""The experimental retrieval-experiment factory.

The Python mirror of `src/sdk/ts/src/experiment.ts`.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from ratel_ai_telemetry import ExperimentArmRole

from .arm import ArmCompletion, ArmDispatch, ArmRun, start_arm
from .definitions import (
    ExperimentAttributes,
    ExperimentConfig,
    ExperimentSelection,
)
from .evaluation import resolve_evaluation_k
from .invocation import ExperimentInvocationBuffer
from .shadow import ServedSignal, complete_peer_evaluation
from .sink import (
    ExperimentArmEvaluationHandle,
    ExperimentEvaluationSink,
    ExperimentFallbackEvaluation,
    ExperimentInvocationEvaluation,
    ExperimentOutcomeEvaluation,
    ExperimentSkipEvaluation,
    compose_experiment_evaluation_sinks,
    create_experiment_runtime_event_sink,
)
from .telemetry import create_experiment_telemetry_sink, validate_experiment_attributes
from .validation import assign_arm, hash_unit_id, validate_config, validate_reported_outcome

if TYPE_CHECKING:
    from ..runtime_events import RuntimeEvents

ResultT = TypeVar("ResultT")
_Evaluation = TypeVar("_Evaluation")


def experimental_define_experiment(
    config: ExperimentConfig, events: RuntimeEvents | None = None
) -> Experiment[Any]:
    """Define an opt-in retrieval experiment over host-supplied async selectors.

    Configuration errors raise ``ValueError`` at once. Each selection serves one
    arm. Pass ``Ratel.events`` to merge the experiment facts into that runtime's
    public stream. The OpenTelemetry projection runs in parallel, and both carry
    the same event id for one fact.

    Args:
        config: The experiment arms, assignment and evaluation policy.
        events: The optional ``Ratel.events`` stream that receives experiment facts.
    """
    telemetry = create_experiment_telemetry_sink()
    sink = (
        telemetry
        if events is None
        else compose_experiment_evaluation_sinks(
            telemetry, create_experiment_runtime_event_sink(events)
        )
    )
    return Experiment(config, sink)


@dataclass
class _Warmup:
    resolved: bool = False
    task: asyncio.Future[None] | None = field(default=None)


class Experiment(Generic[ResultT]):
    """A configured retrieval experiment. Build it with `experimental_define_experiment`."""

    def __init__(
        self, config: ExperimentConfig, sink: ExperimentEvaluationSink | None = None
    ) -> None:
        """Validate ``config`` and bind it to an evaluation ``sink`` (internal)."""
        validate_config(config)
        self._config = config
        self._sink = sink or ExperimentEvaluationSink()
        self._arm_names = list(config["arms"])
        self._warmups: dict[str, _Warmup] = {}
        self._shadow_concurrency = config.get("shadow_policy", {}).get("concurrency", 1)
        self._shadows_in_flight = 0
        self._pending_shadows: set[asyncio.Future[None]] = set()
        references = config["evaluation"]["references"]
        self._compares_peers = "peer-selection" in references
        invocation = next((ref for ref in references if ref != "peer-selection"), None)
        self._invocations = None if invocation is None else ExperimentInvocationBuffer(invocation)

    async def select(
        self,
        params: Any,
        *,
        unit_id: str,
        arm: str | None = None,
        shadow: bool = False,
        k: int | None = None,
        transform: Callable[[Any], ResultT] | None = None,
        attributes: ExperimentAttributes | None = None,
    ) -> ExperimentSelection[ResultT]:
        """Select an arm for ``unit_id`` and return its transformed result.

        The served result never waits for a shadow, and a shadow failure never
        changes it.

        Args:
            params: The caller parameters, passed unchanged to the arm selector.
            unit_id: The stable assignment unit. The raw value stays in the process.
            arm: An explicit arm. Required when the experiment has no split.
            shadow: Also run every other arm as detached shadow work, in declaration
                order, up to ``shadow_policy.concurrency``. An arm over the limit is
                skipped, never queued.
            k: The Jaccard rank window of this selection. It does not cut the result.
            transform: A synchronous visibility transform applied before evaluation.
            attributes: Non-content scalar attributes copied to experiment telemetry.

        Raises:
            ValueError: The arm is not declared, ``shadow`` needs a second arm, or an
                attribute key is reserved.
        """
        assigned_arm = assign_arm(self._config, unit_id, arm)
        if shadow and len(self._arm_names) < 2:
            raise ValueError(
                "experimental_define_experiment.select: shadow requires a second declared arm"
            )
        dispatch = ArmDispatch(
            config=self._config,
            params=params,
            transform=transform,
            attributes=validate_experiment_attributes(attributes),
            selection_id=str(uuid.uuid4()),
            unit_hash=hash_unit_id(unit_id),
            sink=self._sink,
        )
        evaluation_k = resolve_evaluation_k(k, self._config["evaluation"].get("k"))
        served_signal: asyncio.Future[ServedSignal] = asyncio.get_running_loop().create_future()
        cold_at_start = {name: self._is_cold(name) for name in self._arm_names}

        def start(
            name: str, role: ExperimentArmRole, on_settled: Callable[[], None] | None = None
        ) -> ArmRun:
            self._ensure_warmup(name)
            return start_arm(dispatch, name, role, cold_at_start[name], on_settled)

        assigned = start(assigned_arm, ExperimentArmRole.SERVING)
        shadows = self._start_shadows(assigned, start, served_signal) if shadow else {}
        signal = ServedSignal(selection_id=dispatch.selection_id)
        try:
            effective_arm, completed, consumed = await self._serve(assigned, shadows, start)
            signal = ServedSignal(
                selection_id=dispatch.selection_id,
                effective_arm=effective_arm,
                completion=completed,
                consumed_shadow=consumed,
                k=evaluation_k,
            )
            self._record_for_invocations(unit_id, dispatch.selection_id, effective_arm, completed)
            return ExperimentSelection(
                result=completed.result,
                selection_id=dispatch.selection_id,
                assigned_arm=assigned_arm,
                effective_arm=effective_arm,
                duration_ms=completed.duration_ms,
            )
        finally:
            if not served_signal.done():
                served_signal.set_result(signal)

    def report_invocation(self, *, unit_id: str, tool_id: str, turn: int | None = None) -> None:
        """Attribute an invoke of ``tool_id`` to the selections of ``unit_id``.

        It does nothing unless an invocation reference is configured. It never raises.
        """
        if self._invocations is None:
            return
        try:
            unit_hash = hash_unit_id(unit_id)
            for attribution in self._invocations.evaluate_invocation(unit_id, tool_id):
                self._sink.invocation(
                    ExperimentInvocationEvaluation(
                        experiment_id=self._config["id"],
                        unit_hash=unit_hash,
                        tool_id=tool_id,
                        attribution=attribution,
                        turn=turn,
                    )
                )
        except Exception:
            pass  # Invocation attribution is observational and cannot fail application work.

    def report_outcome(
        self, *, selection_id: str, label: str | None = None, score: float | None = None
    ) -> None:
        """Append a delayed outcome to a prior selection.

        Repeated reports stay distinct.

        Raises:
            ValueError: Outcome evaluation is off, or the report has no non-empty
                label and no finite score.
        """
        if self._config["evaluation"].get("outcome") is not True:
            raise ValueError(
                "experimental_define_experiment.report_outcome: outcome evaluation is not enabled"
            )
        validate_reported_outcome(selection_id, label, score)
        try:
            self._sink.outcome(
                ExperimentOutcomeEvaluation(
                    experiment_id=self._config["id"],
                    selection_id=selection_id,
                    label=label,
                    score=score,
                )
            )
        except Exception:
            pass  # Evaluation delivery is observational and cannot fail application work.

    async def warm(self) -> None:
        """Start every unresolved arm warmup at once and wait for them.

        A failing warmup keeps its arm cold, and a later call retries it.
        """
        tasks = [task for name in self._arm_names if (task := self._ensure_warmup(name))]
        await asyncio.gather(*tasks)

    async def drain(self) -> None:
        """Wait for the shadow work started before this call. It never raises.

        Call it again for work started later. The experiment stays usable.
        """
        await asyncio.gather(*tuple(self._pending_shadows), return_exceptions=True)

    async def _serve(
        self,
        assigned: ArmRun,
        shadows: dict[str, ArmRun],
        start: Callable[[str, ExperimentArmRole], ArmRun],
    ) -> tuple[str, ArmCompletion, str | None]:
        completed = await assigned.completion
        if completed.ok:
            return assigned.arm, completed, None
        fallback_arm = self._config.get("fallback_arm")
        if completed.source != "select" or fallback_arm is None or fallback_arm == assigned.arm:
            raise _error(completed)
        reused = shadows.get(fallback_arm)
        fallback = reused or start(fallback_arm, ExperimentArmRole.SERVING)
        completed = await fallback.completion
        if not completed.ok:
            raise _error(completed)
        self._notify(
            self._sink.fallback,
            ExperimentFallbackEvaluation(effective_arm=fallback_arm, reused_shadow=bool(reused)),
            assigned,
        )
        return fallback_arm, completed, fallback_arm if reused else None

    def _start_shadows(
        self,
        assigned: ArmRun,
        start: Callable[[str, ExperimentArmRole, Callable[[], None]], ArmRun],
        served_signal: asyncio.Future[ServedSignal],
    ) -> dict[str, ArmRun]:
        shadows: dict[str, ArmRun] = {}
        for name in self._arm_names:
            if name == assigned.arm:
                continue
            if self._shadows_in_flight >= self._shadow_concurrency:
                self._notify(
                    self._sink.skip,
                    ExperimentSkipEvaluation(
                        skipped_arm=name, concurrency=self._shadow_concurrency
                    ),
                    assigned,
                )
                continue
            self._shadows_in_flight += 1
            run = start(name, ExperimentArmRole.SHADOW, self._release_shadow)
            shadows[name] = run
            pipeline = (
                complete_peer_evaluation(run, served_signal, self._sink)
                if self._compares_peers
                else _await_completion(run)
            )
            self._track(asyncio.ensure_future(pipeline))
        return shadows

    def _release_shadow(self) -> None:
        self._shadows_in_flight -= 1

    def _track(self, task: asyncio.Future[None]) -> None:
        self._pending_shadows.add(task)
        task.add_done_callback(self._pending_shadows.discard)

    def _record_for_invocations(
        self, unit_id: str, selection_id: str, effective_arm: str, completed: ArmCompletion
    ) -> None:
        if self._invocations is None or completed.ranking is None:
            return
        try:
            self._invocations.record_selection(
                unit_id, selection_id, effective_arm, [item["id"] for item in completed.ranking]
            )
        except Exception:
            pass  # Invocation attribution is observational and cannot fail a selection.

    def _notify(
        self,
        hook: Callable[[_Evaluation, ExperimentArmEvaluationHandle | None], None],
        evaluation: _Evaluation,
        run: ArmRun,
    ) -> None:
        try:
            hook(evaluation, run.telemetry)
        except Exception:
            pass  # Evaluation delivery is observational and cannot fail application work.

    def _is_cold(self, name: str) -> bool:
        if "warmup" not in self._config["arms"][name]:
            return False
        entry = self._warmups.get(name)
        return entry is None or not entry.resolved

    def _ensure_warmup(self, name: str) -> asyncio.Future[None] | None:
        warmup = self._config["arms"][name].get("warmup")
        if warmup is None:
            return None
        existing = self._warmups.get(name)
        if existing is not None:
            return existing.task
        entry = _Warmup()
        self._warmups[name] = entry

        async def run() -> None:
            try:
                await warmup()
            except Exception:
                if self._warmups.get(name) is entry:
                    del self._warmups[name]
                return
            entry.resolved = True

        entry.task = asyncio.ensure_future(run())
        return entry.task


async def _await_completion(run: ArmRun) -> None:
    await run.completion


def _error(completed: ArmCompletion) -> BaseException:
    assert completed.error is not None
    return completed.error
