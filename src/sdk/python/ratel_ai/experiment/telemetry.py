"""OpenTelemetry projection of experiment evaluation records (internal).

The Python mirror of the experiment emitters in `src/sdk/ts/src/telemetry.ts`.
Each arm run opens one ``ratel.experiment.arm`` span with experiment baggage, and
its facts become log events in that span's context. Without OpenTelemetry the sink
does nothing.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, TypeVar

from ratel_ai_telemetry import (
    GEN_AI_TOOL_NAME,
    RATEL_EXPERIMENT_AGREEMENT_EXACT_ORDER,
    RATEL_EXPERIMENT_AGREEMENT_ITEM_ATTRS,
    RATEL_EXPERIMENT_AGREEMENT_JACCARD_AT_K,
    RATEL_EXPERIMENT_AGREEMENT_K,
    RATEL_EXPERIMENT_AGREEMENT_OVERLAP_COUNT,
    RATEL_EXPERIMENT_AGREEMENT_RESULT_ATTRS,
    RATEL_EXPERIMENT_AGREEMENT_TOP1,
    RATEL_EXPERIMENT_ARM,
    RATEL_EXPERIMENT_ARM_BAGGAGE_KEY,
    RATEL_EXPERIMENT_COLD,
    RATEL_EXPERIMENT_COMPARISON,
    RATEL_EXPERIMENT_DROP,
    RATEL_EXPERIMENT_DROP_REASON,
    RATEL_EXPERIMENT_DURATION_MS,
    RATEL_EXPERIMENT_EFFECTIVE_ARM,
    RATEL_EXPERIMENT_FALLBACK,
    RATEL_EXPERIMENT_FALLBACK_EFFECTIVE_ARM,
    RATEL_EXPERIMENT_FALLBACK_REUSED_SHADOW,
    RATEL_EXPERIMENT_HIT_COUNT,
    RATEL_EXPERIMENT_ID,
    RATEL_EXPERIMENT_ID_BAGGAGE_KEY,
    RATEL_EXPERIMENT_INVOCATION,
    RATEL_EXPERIMENT_INVOCATION_AGE_MS,
    RATEL_EXPERIMENT_INVOCATION_ATTRIBUTED,
    RATEL_EXPERIMENT_INVOCATION_RANK,
    RATEL_EXPERIMENT_OUTCOME,
    RATEL_EXPERIMENT_OUTCOME_LABEL,
    RATEL_EXPERIMENT_OUTCOME_SCORE,
    RATEL_EXPERIMENT_RANKING_ERROR,
    RATEL_EXPERIMENT_RESULT_ATTRIBUTES_ERROR,
    RATEL_EXPERIMENT_RESULT_ATTRS,
    RATEL_EXPERIMENT_RESULT_ATTRS_ENCODING_ERROR,
    RATEL_EXPERIMENT_RESULT_IDS,
    RATEL_EXPERIMENT_RESULT_SCORES,
    RATEL_EXPERIMENT_RESULTS,
    RATEL_EXPERIMENT_ROLE,
    RATEL_EXPERIMENT_ROLE_BAGGAGE_KEY,
    RATEL_EXPERIMENT_SELECTION_ID,
    RATEL_EXPERIMENT_SELECTION_ID_BAGGAGE_KEY,
    RATEL_EXPERIMENT_SERVED_ARM,
    RATEL_EXPERIMENT_SERVED_DURATION_MS,
    RATEL_EXPERIMENT_SERVED_HIT_COUNT,
    RATEL_EXPERIMENT_SERVED_OUTCOME,
    RATEL_EXPERIMENT_SHADOW_ARM,
    RATEL_EXPERIMENT_SHADOW_DURATION_MS,
    RATEL_EXPERIMENT_SHADOW_HIT_COUNT,
    RATEL_EXPERIMENT_SHADOW_OUTCOME,
    RATEL_EXPERIMENT_SKIP,
    RATEL_EXPERIMENT_SKIP_ARM,
    RATEL_EXPERIMENT_SKIP_CONCURRENCY,
    RATEL_EXPERIMENT_SKIP_REASON,
    RATEL_EXPERIMENT_TURN,
    RATEL_EXPERIMENT_UNIT,
    RATEL_EXPERIMENT_UNIT_BAGGAGE_KEY,
    ExperimentArmOutcome,
    ExperimentSkipReason,
)

from .. import telemetry as _sdk_telemetry
from ..runtime_events import new_runtime_event_id
from .definitions import ExperimentAttributes, ExperimentRankedItem
from .sink import (
    ExperimentArmCompletionEvaluation,
    ExperimentArmEvaluation,
    ExperimentArmEvaluationHandle,
    ExperimentComparisonEvaluation,
    ExperimentDropEvaluation,
    ExperimentEvaluationSink,
    ExperimentFallbackEvaluation,
    ExperimentInvocationEvaluation,
    ExperimentOutcomeEvaluation,
    ExperimentSkipEvaluation,
)

T = TypeVar("T")

_ERROR_TYPE = "error.type"

#: Keys a caller's ``attributes`` must not set: the SDK owns them.
RESERVED_EXPERIMENT_ATTRIBUTES = frozenset(
    {
        _ERROR_TYPE,
        GEN_AI_TOOL_NAME,
        RATEL_EXPERIMENT_AGREEMENT_EXACT_ORDER,
        RATEL_EXPERIMENT_AGREEMENT_ITEM_ATTRS,
        RATEL_EXPERIMENT_AGREEMENT_JACCARD_AT_K,
        RATEL_EXPERIMENT_AGREEMENT_K,
        RATEL_EXPERIMENT_AGREEMENT_OVERLAP_COUNT,
        RATEL_EXPERIMENT_AGREEMENT_RESULT_ATTRS,
        RATEL_EXPERIMENT_AGREEMENT_TOP1,
        RATEL_EXPERIMENT_ARM,
        RATEL_EXPERIMENT_COLD,
        RATEL_EXPERIMENT_DROP_REASON,
        RATEL_EXPERIMENT_DURATION_MS,
        RATEL_EXPERIMENT_EFFECTIVE_ARM,
        RATEL_EXPERIMENT_FALLBACK_EFFECTIVE_ARM,
        RATEL_EXPERIMENT_FALLBACK_REUSED_SHADOW,
        RATEL_EXPERIMENT_HIT_COUNT,
        RATEL_EXPERIMENT_ID,
        RATEL_EXPERIMENT_INVOCATION_AGE_MS,
        RATEL_EXPERIMENT_INVOCATION_ATTRIBUTED,
        RATEL_EXPERIMENT_INVOCATION_RANK,
        RATEL_EXPERIMENT_OUTCOME,
        RATEL_EXPERIMENT_OUTCOME_LABEL,
        RATEL_EXPERIMENT_OUTCOME_SCORE,
        RATEL_EXPERIMENT_RANKING_ERROR,
        RATEL_EXPERIMENT_RESULT_ATTRS,
        RATEL_EXPERIMENT_RESULT_ATTRS_ENCODING_ERROR,
        RATEL_EXPERIMENT_RESULT_ATTRIBUTES_ERROR,
        RATEL_EXPERIMENT_RESULT_IDS,
        RATEL_EXPERIMENT_RESULT_SCORES,
        RATEL_EXPERIMENT_ROLE,
        RATEL_EXPERIMENT_SELECTION_ID,
        RATEL_EXPERIMENT_SERVED_ARM,
        RATEL_EXPERIMENT_SERVED_DURATION_MS,
        RATEL_EXPERIMENT_SERVED_HIT_COUNT,
        RATEL_EXPERIMENT_SERVED_OUTCOME,
        RATEL_EXPERIMENT_SHADOW_ARM,
        RATEL_EXPERIMENT_SHADOW_DURATION_MS,
        RATEL_EXPERIMENT_SHADOW_HIT_COUNT,
        RATEL_EXPERIMENT_SHADOW_OUTCOME,
        RATEL_EXPERIMENT_SKIP_ARM,
        RATEL_EXPERIMENT_SKIP_CONCURRENCY,
        RATEL_EXPERIMENT_SKIP_REASON,
        RATEL_EXPERIMENT_TURN,
        RATEL_EXPERIMENT_UNIT,
    }
)


def validate_experiment_attributes(
    attributes: ExperimentAttributes | None,
) -> dict[str, str | int | float | bool] | None:
    """Validate and copy the caller's scalar experiment attributes."""
    if attributes is None:
        return None
    for key in attributes:
        if key in RESERVED_EXPERIMENT_ATTRIBUTES:
            raise ValueError(
                f'experimental_define_experiment.select: "{key}" is a reserved telemetry attribute'
            )
    return dict(attributes)


def create_experiment_telemetry_sink() -> ExperimentEvaluationSink:
    """Build the SDK-owned OpenTelemetry sink of the public experiment factory."""
    if not _sdk_telemetry._ENABLED:
        return ExperimentEvaluationSink()
    return _TelemetrySink()


class _TelemetrySink(ExperimentEvaluationSink):
    def arm(self, evaluation: ExperimentArmEvaluation) -> ExperimentArmEvaluationHandle | None:
        return _start_experiment_arm(evaluation)

    def fallback(
        self,
        evaluation: ExperimentFallbackEvaluation,
        arm: ExperimentArmEvaluationHandle | None = None,
    ) -> None:
        if arm is not None:
            arm.event(
                RATEL_EXPERIMENT_FALLBACK,
                {
                    RATEL_EXPERIMENT_FALLBACK_EFFECTIVE_ARM: evaluation.effective_arm,
                    RATEL_EXPERIMENT_FALLBACK_REUSED_SHADOW: evaluation.reused_shadow,
                },
                evaluation.event_id,
            )

    def comparison(
        self,
        evaluation: ExperimentComparisonEvaluation,
        arm: ExperimentArmEvaluationHandle | None = None,
    ) -> None:
        if arm is None:
            return
        agreement = evaluation.agreement
        attributes: dict[str, Any] = {
            RATEL_EXPERIMENT_SERVED_ARM: evaluation.served.arm,
            RATEL_EXPERIMENT_SERVED_OUTCOME: evaluation.served.outcome.value,
            RATEL_EXPERIMENT_SERVED_DURATION_MS: evaluation.served.duration_ms,
            RATEL_EXPERIMENT_SERVED_HIT_COUNT: evaluation.served.hit_count,
            RATEL_EXPERIMENT_SHADOW_ARM: evaluation.shadow.arm,
            RATEL_EXPERIMENT_SHADOW_OUTCOME: evaluation.shadow.outcome.value,
            RATEL_EXPERIMENT_SHADOW_DURATION_MS: evaluation.shadow.duration_ms,
            RATEL_EXPERIMENT_SHADOW_HIT_COUNT: evaluation.shadow.hit_count,
            RATEL_EXPERIMENT_AGREEMENT_TOP1: agreement.top1,
            RATEL_EXPERIMENT_AGREEMENT_EXACT_ORDER: agreement.exact_order,
            RATEL_EXPERIMENT_AGREEMENT_OVERLAP_COUNT: agreement.overlap_count,
            RATEL_EXPERIMENT_AGREEMENT_JACCARD_AT_K: agreement.jaccard_at_k,
            RATEL_EXPERIMENT_AGREEMENT_K: agreement.k,
        }
        if agreement.item_attrs is not None:
            attributes[RATEL_EXPERIMENT_AGREEMENT_ITEM_ATTRS] = agreement.item_attrs
        if agreement.result_attrs is not None:
            attributes[RATEL_EXPERIMENT_AGREEMENT_RESULT_ATTRS] = agreement.result_attrs
        arm.event(RATEL_EXPERIMENT_COMPARISON, attributes, evaluation.event_id)

    def skip(
        self,
        evaluation: ExperimentSkipEvaluation,
        arm: ExperimentArmEvaluationHandle | None = None,
    ) -> None:
        if arm is not None:
            arm.event(
                RATEL_EXPERIMENT_SKIP,
                {
                    RATEL_EXPERIMENT_SKIP_ARM: evaluation.skipped_arm,
                    RATEL_EXPERIMENT_SKIP_CONCURRENCY: evaluation.concurrency,
                    RATEL_EXPERIMENT_SKIP_REASON: ExperimentSkipReason.CAPACITY.value,
                },
                evaluation.event_id,
            )

    def drop(
        self,
        evaluation: ExperimentDropEvaluation,
        arm: ExperimentArmEvaluationHandle | None = None,
    ) -> None:
        if arm is not None:
            arm.event(
                RATEL_EXPERIMENT_DROP,
                {RATEL_EXPERIMENT_DROP_REASON: evaluation.reason.value},
                evaluation.event_id,
            )

    def invocation(self, evaluation: ExperimentInvocationEvaluation) -> None:
        attribution = evaluation.attribution
        attributes: dict[str, Any] = {
            _sdk_telemetry.RATEL_EVENT_ID: evaluation.event_id or new_runtime_event_id(),
            RATEL_EXPERIMENT_ID: evaluation.experiment_id,
            RATEL_EXPERIMENT_UNIT: evaluation.unit_hash,
            GEN_AI_TOOL_NAME: evaluation.tool_id,
            RATEL_EXPERIMENT_INVOCATION_ATTRIBUTED: attribution.attributed,
        }
        if evaluation.turn is not None:
            attributes[RATEL_EXPERIMENT_TURN] = evaluation.turn
        if attribution.attributed:
            attributes[RATEL_EXPERIMENT_SELECTION_ID] = attribution.selection_id
            attributes[RATEL_EXPERIMENT_EFFECTIVE_ARM] = attribution.effective_arm
            attributes[RATEL_EXPERIMENT_INVOCATION_RANK] = attribution.rank
            attributes[RATEL_EXPERIMENT_INVOCATION_AGE_MS] = attribution.age_ms
        _sdk_telemetry._logger().emit(event_name=RATEL_EXPERIMENT_INVOCATION, attributes=attributes)

    def outcome(self, evaluation: ExperimentOutcomeEvaluation) -> None:
        attributes: dict[str, Any] = {
            _sdk_telemetry.RATEL_EVENT_ID: evaluation.event_id or new_runtime_event_id(),
            RATEL_EXPERIMENT_ID: evaluation.experiment_id,
            RATEL_EXPERIMENT_SELECTION_ID: evaluation.selection_id,
        }
        if evaluation.label is not None:
            attributes[RATEL_EXPERIMENT_OUTCOME_LABEL] = evaluation.label
        if evaluation.score is not None:
            attributes[RATEL_EXPERIMENT_OUTCOME_SCORE] = evaluation.score
        _sdk_telemetry._logger().emit(event_name=RATEL_EXPERIMENT_OUTCOME, attributes=attributes)


class _TelemetryArmHandle(ExperimentArmEvaluationHandle):
    def __init__(self, span: Any, arm_context: Any, attributes: dict[str, Any]) -> None:
        self._span = span
        self._context = arm_context
        self._attributes = attributes

    def run(self, callback: Callable[[], T]) -> T:
        with self._attached():
            return callback()

    def complete(self, evaluation: ExperimentArmCompletionEvaluation) -> None:
        try:
            self._complete(evaluation)
        finally:
            self._span.end()

    def event(
        self, event_name: str, attributes: Mapping[str, Any], event_id: str | None = None
    ) -> None:
        self._emit(event_name, {**attributes}, event_id)

    @contextmanager
    def _attached(self) -> Iterator[None]:
        from opentelemetry import context as otel_context

        token = otel_context.attach(self._context)
        try:
            yield
        finally:
            otel_context.detach(token)

    def _emit(self, event_name: str, attributes: dict[str, Any], event_id: str | None) -> None:
        with self._attached():
            _sdk_telemetry._logger().emit(
                event_name=event_name,
                attributes={
                    **self._attributes,
                    **attributes,
                    _sdk_telemetry.RATEL_EVENT_ID: event_id or new_runtime_event_id(),
                },
            )

    def _complete(self, evaluation: ExperimentArmCompletionEvaluation) -> None:
        from opentelemetry.trace import Status, StatusCode

        span = self._span
        span.set_attribute(RATEL_EXPERIMENT_OUTCOME, evaluation.outcome.value)
        span.set_attribute(RATEL_EXPERIMENT_DURATION_MS, evaluation.duration_ms)
        if evaluation.hit_count is not None:
            span.set_attribute(RATEL_EXPERIMENT_HIT_COUNT, evaluation.hit_count)
        if evaluation.failure is not None:
            span.set_attribute(_ERROR_TYPE, type(evaluation.failure).__name__)
            span.record_exception(evaluation.failure)
        if evaluation.ranking_failure is not None:
            span.set_attribute(
                RATEL_EXPERIMENT_RANKING_ERROR, type(evaluation.ranking_failure).__name__
            )
        if evaluation.result_attributes_failure is not None:
            span.set_attribute(
                RATEL_EXPERIMENT_RESULT_ATTRIBUTES_ERROR,
                type(evaluation.result_attributes_failure).__name__,
            )
            span.record_exception(evaluation.result_attributes_failure)
        ok = evaluation.outcome in (ExperimentArmOutcome.OK, ExperimentArmOutcome.EMPTY)
        if ok:
            span.set_status(Status(StatusCode.OK))
        else:
            message = None if evaluation.failure is None else str(evaluation.failure)
            span.set_status(Status(StatusCode.ERROR, message))
        if evaluation.ranking is not None:
            item_attributes = self._encode_result_attributes(evaluation.ranking)
            self._emit_results(evaluation.ranking, item_attributes, evaluation.event_id)

    def _encode_result_attributes(self, ranking: Sequence[ExperimentRankedItem]) -> Any:
        on_span = _sdk_telemetry._capture_content_on_span()
        on_event = _sdk_telemetry._capture_content_on_event()
        if not on_span and not on_event:
            return None
        try:
            encoded = json.dumps(
                [dict(item["attrs"]) if "attrs" in item else None for item in ranking]
            )
            if on_span:
                self._span.set_attribute(RATEL_EXPERIMENT_RESULT_ATTRS, encoded)
            return _sdk_telemetry._safe_log_value(json.loads(encoded)) if on_event else None
        except Exception as error:
            self._span.set_attribute(
                RATEL_EXPERIMENT_RESULT_ATTRS_ENCODING_ERROR, type(error).__name__
            )
            self._span.record_exception(error)
            return None

    def _emit_results(
        self,
        ranking: Sequence[ExperimentRankedItem],
        item_attributes: Any,
        event_id: str | None,
    ) -> None:
        attributes: dict[str, Any] = {
            RATEL_EXPERIMENT_RESULT_IDS: [item["id"] for item in ranking],
        }
        scores = [item.get("score") for item in ranking]
        if all(_is_finite_number(score) for score in scores):
            attributes[RATEL_EXPERIMENT_RESULT_SCORES] = [float(s) for s in scores]  # type: ignore[arg-type]
        if item_attributes is not None:
            attributes[RATEL_EXPERIMENT_RESULT_ATTRS] = item_attributes
        self._emit(RATEL_EXPERIMENT_RESULTS, attributes, event_id)


def _start_experiment_arm(evaluation: ExperimentArmEvaluation) -> ExperimentArmEvaluationHandle:
    from opentelemetry import baggage, trace
    from opentelemetry import context as otel_context
    from opentelemetry.trace import SpanKind

    stamp = {
        RATEL_EXPERIMENT_ID: evaluation.experiment_id,
        RATEL_EXPERIMENT_SELECTION_ID: evaluation.selection_id,
        RATEL_EXPERIMENT_ARM: evaluation.arm,
        RATEL_EXPERIMENT_ROLE: evaluation.role.value,
        RATEL_EXPERIMENT_UNIT: evaluation.unit_hash,
    }
    attributes: dict[str, Any] = {**(evaluation.attributes or {}), **stamp}
    parent = otel_context.get_current()
    for key, value in (
        (RATEL_EXPERIMENT_ID_BAGGAGE_KEY, evaluation.experiment_id),
        (RATEL_EXPERIMENT_SELECTION_ID_BAGGAGE_KEY, evaluation.selection_id),
        (RATEL_EXPERIMENT_ARM_BAGGAGE_KEY, evaluation.arm),
        (RATEL_EXPERIMENT_ROLE_BAGGAGE_KEY, evaluation.role.value),
        (RATEL_EXPERIMENT_UNIT_BAGGAGE_KEY, evaluation.unit_hash),
    ):
        parent = baggage.set_baggage(key, value, context=parent)
    span = _sdk_telemetry._tracer().start_span(
        RATEL_EXPERIMENT_ARM,
        context=parent,
        kind=SpanKind.INTERNAL,
        attributes={
            **attributes,
            _sdk_telemetry.RATEL_EVENT_ID: evaluation.event_id or new_runtime_event_id(),
            RATEL_EXPERIMENT_COLD: evaluation.cold,
        },
    )
    return _TelemetryArmHandle(span, trace.set_span_in_context(span, parent), attributes)


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
