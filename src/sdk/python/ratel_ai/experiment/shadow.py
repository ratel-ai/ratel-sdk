"""Detached shadow comparison (internal).

The Python mirror of `src/sdk/ts/src/experiment-shadow.ts`. Each admitted shadow
waits for its own completion and for the served selection, then records one
comparison or one drop with its reason. Nothing here can change the served result.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ratel_ai_telemetry import ExperimentDropReason

from .arm import ArmCompletion, ArmRun, ResultAttributesProjection
from .evaluation import compare_rankings
from .sink import (
    ExperimentArmSummary,
    ExperimentComparisonEvaluation,
    ExperimentDropEvaluation,
    ExperimentEvaluationSink,
)


@dataclass(frozen=True)
class ServedSignal:
    """What the served selection tells its peer shadows when it settles.

    ``completion`` is ``None`` when the selection failed. ``consumed_shadow`` names
    the shadow arm that the fallback reused as the served result.
    """

    selection_id: str
    effective_arm: str | None = None
    completion: ArmCompletion | None = None
    consumed_shadow: str | None = None
    k: int = 10


async def complete_peer_evaluation(
    shadow: ArmRun,
    served_signal: asyncio.Future[ServedSignal],
    sink: ExperimentEvaluationSink,
) -> None:
    """Compare one admitted shadow with the served selection, or record why not."""
    shadow_completion = await shadow.completion
    served = await served_signal
    reason = _drop_reason(shadow, shadow_completion, served)
    if reason is not None:
        _notify_drop(sink, served.selection_id, shadow, reason)
        return
    assert served.completion is not None and served.effective_arm is not None
    assert served.completion.ranking is not None and shadow_completion.ranking is not None

    try:
        served_attrs, shadow_attrs = _comparable_attributes(
            served.completion.result_attributes, shadow_completion.result_attributes
        )
        agreement = compare_rankings(
            served.completion.ranking,
            shadow_completion.ranking,
            served.k,
            served_attrs,
            shadow_attrs,
        )
        evaluation = ExperimentComparisonEvaluation(
            selection_id=served.selection_id,
            served=ExperimentArmSummary(
                arm=served.effective_arm,
                outcome=served.completion.outcome,
                duration_ms=served.completion.duration_ms,
                hit_count=len(served.completion.ranking),
            ),
            shadow=ExperimentArmSummary(
                arm=shadow.arm,
                outcome=shadow_completion.outcome,
                duration_ms=shadow_completion.duration_ms,
                hit_count=len(shadow_completion.ranking),
            ),
            agreement=agreement,
        )
    except Exception:
        _notify_drop(sink, served.selection_id, shadow, ExperimentDropReason.COMPARISON_FAILED)
        return
    try:
        sink.comparison(evaluation, shadow.telemetry)
    except Exception:
        pass  # Evaluation delivery is observational and cannot fail application work.


def _drop_reason(
    shadow: ArmRun, shadow_completion: ArmCompletion, served: ServedSignal
) -> ExperimentDropReason | None:
    if not shadow_completion.ok or shadow_completion.ranking is None:
        return ExperimentDropReason.ARM_FAILED
    if served.completion is None:
        return ExperimentDropReason.SELECTION_FAILED
    if served.consumed_shadow == shadow.arm:
        return ExperimentDropReason.FALLBACK_CONSUMED
    if served.completion.ranking is None:
        return ExperimentDropReason.SERVED_RANKING_FAILED
    return None


def _comparable_attributes(
    served: ResultAttributesProjection | None, shadow: ResultAttributesProjection | None
) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None]:
    """Both result-attribute projections, or neither when one is missing or failed."""
    if served is None or shadow is None or served.attributes is None or shadow.attributes is None:
        return None, None
    return served.attributes, shadow.attributes


def _notify_drop(
    sink: ExperimentEvaluationSink,
    selection_id: str,
    shadow: ArmRun,
    reason: ExperimentDropReason,
) -> None:
    try:
        sink.drop(
            ExperimentDropEvaluation(
                selection_id=selection_id, shadow_arm=shadow.arm, reason=reason
            ),
            shadow.telemetry,
        )
    except Exception:
        pass  # Evaluation delivery is observational and cannot fail application work.
