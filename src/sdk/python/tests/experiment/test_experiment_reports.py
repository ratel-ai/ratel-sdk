"""Invocation attribution and delayed outcome reports."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import Otel
from experiment_helpers import two_arm_config

from ratel_ai import experimental_define_experiment, ratel
from ratel_ai.experiment.invocation import (
    MAX_SELECTIONS_PER_UNIT,
    MAX_TRACKED_UNITS,
    ExperimentInvocationBuffer,
)

INVOCATION = {"kind": "invocation", "window": {"turns": 5}}


async def _runtime_experiment(**overrides: Any) -> tuple[Any, list[dict[str, Any]], Any]:
    runtime = ratel()
    received: list[dict[str, Any]] = []
    subscription = runtime.events.subscribe(lambda batch: received.extend(batch))
    experiment = experimental_define_experiment(two_arm_config(**overrides), runtime.events)
    return experiment, received, subscription


def _of_type(events: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    return [event for event in events if event["type"] == event_type]


async def test_an_invoke_of_a_ranked_tool_emits_the_invocation_with_its_rank(otel: Otel) -> None:
    experiment, received, subscription = await _runtime_experiment(
        evaluation={"references": [INVOCATION]}
    )
    selection = await experiment.select({}, unit_id="user-1")

    experiment.report_invocation(unit_id="user-1", tool_id="b", turn=3)
    await subscription.flush()

    [event] = _of_type(received, "experiment_invocation")
    assert event["attributed"] is True
    assert (event["tool_id"], event["rank"], event["turn"]) == ("b", 1, 3)
    assert (event["selection_id"], event["effective_arm"]) == (selection.selection_id, "control")
    assert event["age_ms"] >= 0
    [record] = otel.logs_named("ratel.experiment.invocation")
    assert record.attributes["ratel.event.id"] == event["event_id"]
    assert record.attributes["gen_ai.tool.name"] == "b"
    assert record.attributes["ratel.experiment.invocation.rank"] == 1
    assert record.attributes["ratel.experiment.invocation.attributed"] is True
    assert record.attributes["ratel.experiment.selection_id"] == selection.selection_id
    assert record.attributes["ratel.experiment.turn"] == 3


async def test_an_unranked_tool_has_rank_minus_one_and_an_unknown_unit_no_attribution() -> None:
    experiment, received, subscription = await _runtime_experiment(
        evaluation={"references": [INVOCATION]}
    )
    await experiment.select({}, unit_id="user-1")

    experiment.report_invocation(unit_id="user-1", tool_id="zzz")
    experiment.report_invocation(unit_id="someone-else", tool_id="a")
    await subscription.flush()

    unranked, unknown = _of_type(received, "experiment_invocation")
    assert unranked["rank"] == -1
    assert unknown["attributed"] is False
    assert "rank" not in unknown and "turn" not in unknown


async def test_report_invocation_does_nothing_without_an_invocation_reference() -> None:
    experiment, received, subscription = await _runtime_experiment()
    await experiment.select({}, unit_id="user-1")

    experiment.report_invocation(unit_id="user-1", tool_id="a")
    await subscription.flush()

    assert _of_type(received, "experiment_invocation") == []


async def test_report_outcome_emits_the_outcome_event(otel: Otel) -> None:
    experiment, received, subscription = await _runtime_experiment(
        evaluation={"references": ["peer-selection"], "outcome": True}
    )
    selection = await experiment.select({}, unit_id="user-1")

    experiment.report_outcome(selection_id=selection.selection_id, label="accepted", score=1)
    experiment.report_outcome(selection_id=selection.selection_id, score=0.5)
    await subscription.flush()

    first, second = _of_type(received, "experiment_outcome")
    assert (first["selection_id"], first["label"], first["score"]) == (
        selection.selection_id,
        "accepted",
        1,
    )
    assert "label" not in second
    records = otel.logs_named("ratel.experiment.outcome")
    assert [r.attributes["ratel.event.id"] for r in records] == [
        first["event_id"],
        second["event_id"],
    ]
    assert records[0].attributes["ratel.experiment.outcome.label"] == "accepted"
    assert records[1].attributes["ratel.experiment.outcome.score"] == 0.5


def test_report_outcome_needs_outcome_evaluation() -> None:
    experiment = experimental_define_experiment(two_arm_config())

    with pytest.raises(ValueError, match="outcome evaluation is not enabled"):
        experiment.report_outcome(selection_id="s", label="x")


@pytest.mark.parametrize(
    ("selection_id", "label", "score", "message"),
    [
        ("", "x", None, "selection_id must be a non-empty string"),
        ("s", None, None, "label or score is required"),
        ("s", "", None, "label must be a non-empty string"),
        ("s", None, float("nan"), "score must be finite"),
        ("s", None, True, "score must be finite"),
    ],
)
def test_report_outcome_rejects_a_malformed_report(
    selection_id: str, label: Any, score: Any, message: str
) -> None:
    experiment = experimental_define_experiment(
        two_arm_config(evaluation={"references": ["peer-selection"], "outcome": True})
    )

    with pytest.raises(ValueError, match=f"report_outcome: {message}$"):
        experiment.report_outcome(selection_id=selection_id, label=label, score=score)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_the_window_keeps_the_last_turns_and_attributes_the_newest() -> None:
    buffer = ExperimentInvocationBuffer({"kind": "invocation", "window": {"turns": 2}})
    for index in range(3):
        buffer.record_selection("u", f"s{index}", "control", ["a"])

    [attribution] = buffer.evaluate_invocation("u", "a")

    assert attribution.selection_id == "s2"


def test_all_in_window_attributes_every_selection_in_the_window() -> None:
    buffer = ExperimentInvocationBuffer(
        {"kind": "invocation", "window": {"turns": 2}, "attribution": "all-in-window"}
    )
    for index in range(3):
        buffer.record_selection("u", f"s{index}", "control", ["a"])

    assert [a.selection_id for a in buffer.evaluate_invocation("u", "a")] == ["s1", "s2"]


def test_max_age_expires_old_selections() -> None:
    clock = _Clock()
    buffer = ExperimentInvocationBuffer(
        {"kind": "invocation", "window": {"max_age_ms": 100}}, clock=clock
    )
    buffer.record_selection("u", "s0", "control", ["a"])

    clock.now = 50
    [fresh] = buffer.evaluate_invocation("u", "a")
    clock.now = 151
    [expired] = buffer.evaluate_invocation("u", "a")

    assert (fresh.attributed, fresh.age_ms) == (True, 50)
    assert expired.attributed is False


def test_the_buffer_evicts_the_oldest_selection_of_a_unit_at_its_limit() -> None:
    buffer = ExperimentInvocationBuffer(
        {"kind": "invocation", "window": {"turns": 1000}, "attribution": "all-in-window"}
    )
    for index in range(MAX_SELECTIONS_PER_UNIT + 1):
        buffer.record_selection("u", f"s{index}", "control", ["a"])

    kept = [a.selection_id for a in buffer.evaluate_invocation("u", "a")]

    assert len(kept) == MAX_SELECTIONS_PER_UNIT
    assert kept[0] == "s1"


def test_the_buffer_evicts_the_least_recent_unit_at_its_limit() -> None:
    buffer = ExperimentInvocationBuffer({"kind": "invocation", "window": {"turns": 1}})
    for index in range(MAX_TRACKED_UNITS + 1):
        buffer.record_selection(f"u{index}", f"s{index}", "control", ["a"])

    [oldest] = buffer.evaluate_invocation("u0", "a")
    [newest] = buffer.evaluate_invocation(f"u{MAX_TRACKED_UNITS}", "a")

    assert oldest.attributed is False
    assert newest.attributed is True
