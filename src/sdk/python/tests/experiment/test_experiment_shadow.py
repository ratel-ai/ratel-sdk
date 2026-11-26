"""Shadow arms: detached comparison, drops with the TypeScript reasons, capacity skips.

A shadow never changes the served result: a failing or slow shadow leaves the
served hits as they are, and its outcome arrives on the runtime stream.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from conftest import Otel
from experiment_helpers import raises, returns, two_arm_config

from ratel_ai import Ratel, experimental_define_experiment, ratel
from ratel_ai.experiment import ExperimentArmContext


def _subscribe(runtime: Ratel) -> tuple[list[dict[str, Any]], Any]:
    received: list[dict[str, Any]] = []
    return received, runtime.events.subscribe(lambda batch: received.extend(batch))


def _of_type(events: list[dict[str, Any]], event_type: str) -> list[dict[str, Any]]:
    return [event for event in events if event["type"] == event_type]


async def _run(
    config: Any, *, unit_id: str = "user-1", **options: Any
) -> tuple[Any, list[dict[str, Any]]]:
    runtime = ratel()
    received, subscription = _subscribe(runtime)
    experiment = experimental_define_experiment(config, runtime.events)
    selection = await experiment.select({}, unit_id=unit_id, shadow=True, **options)
    await experiment.drain()
    await subscription.flush()
    return selection, received


async def test_a_shadow_is_compared_with_the_served_ranking(otel: Otel) -> None:
    roles: list[str] = []

    def arm(ids: list[str]) -> Any:
        async def select(_params: Any, context: ExperimentArmContext) -> list[str]:
            roles.append(context.role.value)
            return ids

        return select

    config = two_arm_config(
        arms={"control": {"select": arm(["a", "b", "c"])}, "treatment": {"select": arm(["a", "c"])}}
    )

    selection, received = await _run(config, k=2)

    assert selection.result == ["a", "b", "c"]
    assert sorted(roles) == ["serving", "shadow"]
    [event] = _of_type(received, "experiment_comparison")
    assert (event["served_arm"], event["shadow_arm"]) == ("control", "treatment")
    assert (event["arm"], event["role"]) == ("treatment", "shadow")
    assert (event["served_hit_count"], event["shadow_hit_count"]) == (3, 2)
    assert event["agreement"] == {
        "top1": True,
        "exactOrder": False,
        "overlapCount": 2,
        "jaccardAtK": 1 / 3,
        "k": 2,
        "itemAttrs": {},
    }
    [record] = otel.logs_named("ratel.experiment.comparison")
    assert record.attributes["ratel.event.id"] == event["event_id"]
    assert record.attributes["ratel.experiment.served.arm"] == "control"
    assert record.attributes["ratel.experiment.shadow.arm"] == "treatment"
    assert record.attributes["ratel.experiment.agreement.top1"] is True
    assert record.attributes["ratel.experiment.agreement.jaccard_at_k"] == pytest.approx(1 / 3)
    assert record.attributes["ratel.experiment.agreement.k"] == 2
    shadow_span = next(
        s
        for s in otel.spans_named("ratel.experiment.arm")
        if s.attributes["ratel.experiment.role"] == "shadow"
    )
    assert record.span_id == shadow_span.context.span_id


async def test_a_raising_shadow_leaves_the_served_hits_and_reports_arm_failed(otel: Otel) -> None:
    config = two_arm_config(
        arms={
            "control": {"select": returns(["a", "b"])},
            "treatment": {"select": raises(RuntimeError())},
        }
    )

    selection, received = await _run(config)

    assert (selection.effective_arm, selection.result) == ("control", ["a", "b"])
    [event] = _of_type(received, "experiment_drop")
    assert (event["shadow_arm"], event["reason"]) == ("treatment", "arm-failed")
    assert event["selection_id"] == selection.selection_id
    [record] = otel.logs_named("ratel.experiment.drop")
    assert record.attributes["ratel.experiment.drop.reason"] == "arm-failed"
    assert record.attributes["ratel.event.id"] == event["event_id"]
    assert _of_type(received, "experiment_comparison") == []


async def test_a_failed_selection_drops_its_shadow_as_selection_failed() -> None:
    runtime = ratel()
    received, subscription = _subscribe(runtime)
    experiment = experimental_define_experiment(
        two_arm_config(
            arms={
                "control": {"select": raises(RuntimeError("down"))},
                "treatment": {"select": returns(["a"])},
            }
        ),
        runtime.events,
    )

    with pytest.raises(RuntimeError):
        await experiment.select({}, unit_id="user-1", shadow=True)
    await experiment.drain()
    await subscription.flush()

    [event] = _of_type(received, "experiment_drop")
    assert event["reason"] == "selection-failed"


async def test_a_fallback_reuses_the_running_shadow_and_drops_it_as_consumed() -> None:
    config = two_arm_config(
        arms={
            "control": {"select": raises(RuntimeError("down"))},
            "treatment": {"select": returns(["z"])},
        },
        fallback_arm="treatment",
    )

    selection, received = await _run(config)

    assert (selection.effective_arm, selection.result) == ("treatment", ["z"])
    [fallback] = _of_type(received, "experiment_fallback")
    assert fallback["reused_shadow"] is True
    [drop] = _of_type(received, "experiment_drop")
    assert drop["reason"] == "fallback-consumed"
    assert len(_of_type(received, "experiment_selection")) == 2


async def test_a_failed_served_ranking_drops_the_shadow() -> None:
    def ranking(ids: list[str]) -> list[Any]:
        if ids == ["served"]:
            raise RuntimeError("bad projection")
        return [{"id": item} for item in ids]

    config = two_arm_config(
        arms={"control": {"select": returns(["served"])}, "treatment": {"select": returns(["s"])}},
        ranking=ranking,
    )

    selection, received = await _run(config)

    assert selection.result == ["served"]
    [event] = _of_type(received, "experiment_drop")
    assert event["reason"] == "served-ranking-failed"


async def test_the_selection_does_not_wait_for_a_slow_shadow() -> None:
    release = asyncio.Event()

    async def slow(_params: Any, _context: ExperimentArmContext) -> list[str]:
        await release.wait()
        return ["late"]

    experiment = experimental_define_experiment(
        two_arm_config(arms={"control": {"select": returns(["a"])}, "treatment": {"select": slow}})
    )

    selection = await asyncio.wait_for(experiment.select({}, unit_id="user-1", shadow=True), 1)
    drained = asyncio.ensure_future(experiment.drain())
    await asyncio.sleep(0)

    assert selection.result == ["a"]
    assert not drained.done()
    release.set()
    await asyncio.wait_for(drained, 1)


async def test_a_shadow_over_capacity_is_skipped_never_queued(otel: Otel) -> None:
    release = asyncio.Event()
    started: list[str] = []

    def slow(name: str) -> Any:
        async def select(_params: Any, _context: ExperimentArmContext) -> list[str]:
            started.append(name)
            await release.wait()
            return [name]

        return select

    runtime = ratel()
    received, subscription = _subscribe(runtime)
    experiment = experimental_define_experiment(
        {
            "id": "three",
            "arms": {
                "control": {"select": returns(["a"])},
                "b": {"select": slow("b")},
                "c": {"select": slow("c")},
            },
            "ranking": lambda ids: [{"id": item} for item in ids],
            "evaluation": {"references": ["peer-selection"]},
            "shadow_policy": {"concurrency": 1},
        },
        runtime.events,
    )

    await experiment.select({}, unit_id="u", arm="control", shadow=True)
    await experiment.select({}, unit_id="u", arm="control", shadow=True)
    release.set()
    await experiment.drain()
    await subscription.flush()

    assert started == ["b"]
    skips = _of_type(received, "experiment_skip")
    assert [(s["skipped_arm"], s["reason"], s["concurrency"]) for s in skips] == [
        ("c", "capacity", 1),
        ("b", "capacity", 1),
        ("c", "capacity", 1),
    ]
    assert {s["arm"] for s in skips} == {"control"}
    records = otel.logs_named("ratel.experiment.skip")
    assert [r.attributes["ratel.experiment.skip.reason"] for r in records] == ["capacity"] * 3
    assert {r.attributes["ratel.event.id"] for r in records} == {s["event_id"] for s in skips}


async def test_capacity_frees_when_the_shadow_callback_settles() -> None:
    runtime = ratel()
    received, subscription = _subscribe(runtime)
    experiment = experimental_define_experiment(two_arm_config(), runtime.events)

    for _ in range(3):
        await experiment.select({}, unit_id="user-1", shadow=True)
        await experiment.drain()
    await subscription.flush()

    assert _of_type(received, "experiment_skip") == []
    assert len(_of_type(received, "experiment_comparison")) == 3


async def test_shadow_needs_a_second_declared_arm() -> None:
    experiment = experimental_define_experiment(
        two_arm_config(arms={"control": {"select": returns([])}}, split=None)
    )

    with pytest.raises(ValueError, match="shadow requires a second declared arm"):
        await experiment.select({}, unit_id="u", arm="control", shadow=True)


async def test_result_attributes_are_compared_when_both_arms_project_them() -> None:
    config = two_arm_config(
        evaluation={
            "references": ["peer-selection"],
            "attributes": lambda ids: {"size": len(ids), "first": ids[0]},
        }
    )

    _selection, received = await _run(config)

    [event] = _of_type(received, "experiment_comparison")
    assert event["agreement"]["resultAttrs"] == {"size": True, "first": False}
