"""Experiment telemetry: the arm span, its log events and the runtime-event stream.

One fact has one event id: the ``ratel.experiment.arm`` span and the
``experiment_selection`` runtime event share it, as in the TypeScript SDK.
"""

from __future__ import annotations

import hashlib
from typing import Any

from conftest import Otel
from experiment_helpers import raises, returns, two_arm_config
from opentelemetry import baggage
from opentelemetry.trace import StatusCode

from ratel_ai import Ratel, experimental_define_experiment, ratel
from ratel_ai.experiment import ExperimentArmContext

UNIT_HASH = hashlib.sha256(b"user-1").hexdigest()[:16]


async def _collect(runtime: Ratel) -> tuple[list[dict[str, Any]], Any]:
    received: list[dict[str, Any]] = []
    subscription = runtime.events.subscribe(lambda batch: received.extend(batch))
    return received, subscription


def _of_type(events: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    return [event for event in events if event["type"] == event_type]


async def test_a_selection_emits_the_arm_span_with_the_typescript_attributes(otel: Otel) -> None:
    experiment = experimental_define_experiment(two_arm_config())

    selection = await experiment.select({}, unit_id="user-1", attributes={"tenant": "acme"})

    [span] = otel.spans_named("ratel.experiment.arm")
    assert span.attributes["ratel.experiment.id"] == "search-v2"
    assert span.attributes["ratel.experiment.selection_id"] == selection.selection_id
    assert span.attributes["ratel.experiment.arm"] == "control"
    assert span.attributes["ratel.experiment.role"] == "serving"
    assert span.attributes["ratel.experiment.unit"] == UNIT_HASH
    assert span.attributes["ratel.experiment.cold"] is False
    assert span.attributes["ratel.experiment.outcome"] == "ok"
    assert span.attributes["ratel.experiment.hit_count"] == 2
    assert span.attributes["ratel.experiment.duration_ms"] >= 0
    assert span.attributes["ratel.event.id"]
    assert span.attributes["tenant"] == "acme"
    assert span.status.status_code == StatusCode.OK
    assert "user-1" not in span.attributes.values()


async def test_the_results_log_event_carries_the_ranked_ids(otel: Otel) -> None:
    experiment = experimental_define_experiment(two_arm_config())

    await experiment.select({}, unit_id="user-1")

    [record] = otel.logs_named("ratel.experiment.results")
    assert list(record.attributes["ratel.experiment.result_ids"]) == ["a", "b"]
    assert record.attributes["ratel.experiment.arm"] == "control"
    assert "ratel.experiment.result_scores" not in record.attributes
    [span] = otel.spans_named("ratel.experiment.arm")
    assert record.span_id == span.context.span_id


async def test_the_arm_runs_inside_the_span_with_experiment_baggage(otel: Otel) -> None:
    seen: dict[str, object] = {}

    async def select(_params: Any, _context: ExperimentArmContext) -> list[str]:
        seen.update(baggage.get_all())
        return []

    experiment = experimental_define_experiment(
        two_arm_config(arms={"control": {"select": select}, "treatment": {"select": select}})
    )

    selection = await experiment.select({}, unit_id="user-1")

    assert seen == {
        "ratel.experiment.id": "search-v2",
        "ratel.experiment.selection_id": selection.selection_id,
        "ratel.experiment.arm": "control",
        "ratel.experiment.role": "serving",
        "ratel.experiment.unit": UNIT_HASH,
    }
    [span] = otel.spans_named("ratel.experiment.arm")
    assert span.attributes["ratel.experiment.outcome"] == "empty"


async def test_the_selection_span_and_runtime_event_share_one_event_id(otel: Otel) -> None:
    runtime = ratel()
    received, subscription = await _collect(runtime)
    experiment = experimental_define_experiment(two_arm_config(), runtime.events)

    selection = await experiment.select({}, unit_id="user-1", attributes={"tenant": "acme"})
    await subscription.flush()

    [event] = _of_type(received, "experiment_selection")
    [span] = otel.spans_named("ratel.experiment.arm")
    assert event["event_id"] == span.attributes["ratel.event.id"]
    assert event["experiment_id"] == "search-v2"
    assert event["selection_id"] == selection.selection_id
    assert event["unit"] == UNIT_HASH
    assert (event["arm"], event["role"], event["cold"]) == ("control", "serving", False)
    assert event["attributes"] == {"tenant": "acme"}
    assert event["session_id"] == runtime.events.session_id
    assert event["v"] == 2


async def test_the_results_runtime_event_shares_the_results_log_event_id(otel: Otel) -> None:
    runtime = ratel()
    received, subscription = await _collect(runtime)
    experiment = experimental_define_experiment(two_arm_config(), runtime.events)

    await experiment.select({}, unit_id="user-1")
    await subscription.flush()

    [event] = _of_type(received, "experiment_results")
    [record] = otel.logs_named("ratel.experiment.results")
    assert event["event_id"] == record.attributes["ratel.event.id"]
    assert (event["outcome"], event["hit_count"], event["result_ids"]) == ("ok", 2, ["a", "b"])
    assert event["duration_ms"] >= 0


async def test_a_fallback_emits_the_failed_arm_and_the_fallback_event(otel: Otel) -> None:
    runtime = ratel()
    received, subscription = await _collect(runtime)
    experiment = experimental_define_experiment(
        two_arm_config(
            arms={
                "control": {"select": raises(RuntimeError("down"))},
                "treatment": {"select": returns(["z"])},
            },
            fallback_arm="treatment",
        ),
        runtime.events,
    )

    await experiment.select({}, unit_id="user-1")
    await subscription.flush()

    failed, served = sorted(otel.spans_named("ratel.experiment.arm"), key=lambda s: s.start_time)
    assert failed.attributes["ratel.experiment.outcome"] == "error"
    assert failed.attributes["error.type"] == "RuntimeError"
    assert failed.status.status_code == StatusCode.ERROR
    assert served.attributes["ratel.experiment.arm"] == "treatment"
    [record] = otel.logs_named("ratel.experiment.fallback")
    assert record.attributes["ratel.experiment.fallback.effective_arm"] == "treatment"
    assert record.attributes["ratel.experiment.fallback.reused_shadow"] is False
    assert record.span_id == failed.context.span_id
    [event] = _of_type(received, "experiment_fallback")
    assert event["event_id"] == record.attributes["ratel.event.id"]
    assert (event["arm"], event["effective_arm"], event["reused_shadow"]) == (
        "control",
        "treatment",
        False,
    )
    [error_result] = [e for e in _of_type(received, "experiment_results") if e["arm"] == "control"]
    assert error_result["error_class"] == "RuntimeError"


async def test_an_arm_with_a_warmup_is_cold_until_warm_completes() -> None:
    async def warmup() -> None:
        return None

    runtime = ratel()
    received, subscription = await _collect(runtime)
    experiment = experimental_define_experiment(
        two_arm_config(
            arms={
                "control": {"select": returns(["a"]), "warmup": warmup},
                "treatment": {"select": returns([])},
            }
        ),
        runtime.events,
    )

    await experiment.select({}, unit_id="user-1")
    await experiment.warm()
    await experiment.select({}, unit_id="user-1")
    await subscription.flush()

    assert [e["cold"] for e in _of_type(received, "experiment_selection")] == [True, False]


async def test_an_unsubscribed_handler_gets_no_experiment_events() -> None:
    runtime = ratel()
    received, subscription = await _collect(runtime)
    subscription.unsubscribe()
    experiment = experimental_define_experiment(two_arm_config(), runtime.events)

    await experiment.select({}, unit_id="user-1")
    await subscription.flush()

    assert _of_type(received, "experiment_selection") == []
