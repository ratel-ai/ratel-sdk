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
from .definitions import ExperimentAttributes, ExperimentConfig, ExperimentSelection
from .sink import (
    ExperimentEvaluationSink,
    ExperimentFallbackEvaluation,
    compose_experiment_evaluation_sinks,
    create_experiment_runtime_event_sink,
)
from .telemetry import create_experiment_telemetry_sink, validate_experiment_attributes
from .validation import assign_arm, hash_unit_id, validate_config

if TYPE_CHECKING:
    from ..runtime_events import RuntimeEvents

ResultT = TypeVar("ResultT")


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

    async def select(
        self,
        params: Any,
        *,
        unit_id: str,
        arm: str | None = None,
        transform: Callable[[Any], ResultT] | None = None,
        attributes: ExperimentAttributes | None = None,
    ) -> ExperimentSelection[ResultT]:
        """Select an arm for ``unit_id`` and return its transformed result.

        Args:
            params: The caller parameters, passed unchanged to the arm selector.
            unit_id: The stable assignment unit. The raw value stays in the process.
            arm: An explicit arm. Required when the experiment has no split.
            transform: A synchronous visibility transform applied before evaluation.
            attributes: Non-content scalar attributes copied to experiment telemetry.

        Raises:
            ValueError: The arm is not declared, or an attribute key is reserved.
        """
        assigned_arm = assign_arm(self._config, unit_id, arm)
        dispatch = ArmDispatch(
            config=self._config,
            params=params,
            transform=transform,
            attributes=validate_experiment_attributes(attributes),
            selection_id=str(uuid.uuid4()),
            unit_hash=hash_unit_id(unit_id),
            sink=self._sink,
        )
        cold_at_start = {name: self._is_cold(name) for name in self._arm_names}

        def start(name: str, role: ExperimentArmRole) -> ArmRun:
            self._ensure_warmup(name)
            return start_arm(dispatch, name, role, cold_at_start[name])

        assigned = start(assigned_arm, ExperimentArmRole.SERVING)
        effective_arm = assigned_arm
        completed = await assigned.completion
        if not completed.ok:
            fallback_arm = self._config.get("fallback_arm")
            if completed.source != "select" or fallback_arm is None or fallback_arm == assigned_arm:
                raise _error(completed)
            effective_arm = fallback_arm
            completed = await start(fallback_arm, ExperimentArmRole.SERVING).completion
            if not completed.ok:
                raise _error(completed)
            self._notify_fallback(
                ExperimentFallbackEvaluation(effective_arm=effective_arm, reused_shadow=False),
                assigned,
            )

        return ExperimentSelection(
            result=completed.result,
            selection_id=dispatch.selection_id,
            assigned_arm=assigned_arm,
            effective_arm=effective_arm,
            duration_ms=completed.duration_ms,
        )

    async def warm(self) -> None:
        """Start every unresolved arm warmup at once and wait for them.

        A failing warmup keeps its arm cold, and a later call retries it.
        """
        tasks = [task for name in self._arm_names if (task := self._ensure_warmup(name))]
        await asyncio.gather(*tasks)

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

    def _notify_fallback(self, evaluation: ExperimentFallbackEvaluation, run: ArmRun) -> None:
        try:
            self._sink.fallback(evaluation, run.telemetry)
        except Exception:
            pass  # Evaluation delivery is observational and cannot fail application work.


def _error(completed: ArmCompletion) -> BaseException:
    assert completed.error is not None
    return completed.error
