"""Arm selection: deterministic assignment, transform, fallback and warmup."""

from __future__ import annotations

from typing import Any

import pytest
from experiment_helpers import raises, ranking, returns, two_arm_config

from ratel_ai import ExperimentArmRole, ExperimentSelection, experimental_define_experiment
from ratel_ai.experiment import ExperimentArmContext


async def test_the_same_unit_gets_the_same_arm_in_every_instance() -> None:
    first = experimental_define_experiment(two_arm_config())
    second = experimental_define_experiment(two_arm_config())

    arms = {(await e.select({}, unit_id="user-1")).assigned_arm for e in (first, second, first)}

    assert arms == {"control"}


async def test_an_explicit_arm_overrides_the_split() -> None:
    experiment = experimental_define_experiment(two_arm_config())

    selection = await experiment.select({}, unit_id="user-1", arm="treatment")

    assert (selection.assigned_arm, selection.effective_arm) == ("treatment", "treatment")
    assert selection.result == ["b", "c"]


async def test_the_arm_receives_the_caller_params_and_the_serving_role() -> None:
    calls: list[tuple[Any, ExperimentArmContext]] = []

    async def select(params: Any, context: ExperimentArmContext) -> list[str]:
        calls.append((params, context))
        return ["a"]

    params = {"query": "weather"}
    experiment = experimental_define_experiment(
        two_arm_config(arms={"control": {"select": select}, "treatment": {"select": select}})
    )

    await experiment.select(params, unit_id="user-1")

    assert calls == [(params, ExperimentArmContext(role=ExperimentArmRole.SERVING))]
    assert calls[0][0] is params


async def test_returns_the_transformed_result_and_its_correlation() -> None:
    experiment = experimental_define_experiment(two_arm_config())

    selection = await experiment.select({}, unit_id="user-1", transform=lambda ids: ids[:1])

    assert isinstance(selection, ExperimentSelection)
    assert selection.result == ["a"]
    assert selection.selection_id
    assert selection.duration_ms >= 0


async def test_each_selection_gets_a_new_selection_id() -> None:
    experiment = experimental_define_experiment(two_arm_config())

    first = await experiment.select({}, unit_id="user-1")
    second = await experiment.select({}, unit_id="user-1")

    assert first.selection_id != second.selection_id


async def test_falls_back_when_the_assigned_selector_raises() -> None:
    experiment = experimental_define_experiment(
        two_arm_config(
            arms={
                "control": {"select": raises(RuntimeError("down"))},
                "treatment": {"select": returns(["z"])},
            },
            fallback_arm="treatment",
        )
    )

    selection = await experiment.select({}, unit_id="user-1")

    assert (selection.assigned_arm, selection.effective_arm) == ("control", "treatment")
    assert selection.result == ["z"]


async def test_raises_the_selector_error_without_a_fallback() -> None:
    error = RuntimeError("down")
    experiment = experimental_define_experiment(
        two_arm_config(
            arms={"control": {"select": raises(error)}, "treatment": {"select": returns([])}}
        )
    )

    with pytest.raises(RuntimeError) as raised:
        await experiment.select({}, unit_id="user-1")

    assert raised.value is error


async def test_raises_the_fallback_error_when_the_fallback_also_raises() -> None:
    experiment = experimental_define_experiment(
        two_arm_config(
            arms={
                "control": {"select": raises(RuntimeError("down"))},
                "treatment": {"select": raises(ValueError("also down"))},
            },
            fallback_arm="treatment",
        )
    )

    with pytest.raises(ValueError, match="also down"):
        await experiment.select({}, unit_id="user-1")


async def test_a_transform_error_raises_and_skips_the_fallback() -> None:
    calls: list[str] = []

    async def fallback(_params: Any, _context: ExperimentArmContext) -> list[str]:
        calls.append("fallback")
        return []

    def transform(_ids: list[str]) -> list[str]:
        raise KeyError("bad")

    experiment = experimental_define_experiment(
        two_arm_config(
            arms={"control": {"select": returns(["a"])}, "treatment": {"select": fallback}},
            fallback_arm="treatment",
        )
    )

    with pytest.raises(KeyError):
        await experiment.select({}, unit_id="user-1", transform=transform)
    assert calls == []


async def test_a_ranking_error_still_serves_the_result() -> None:
    def broken_ranking(_ids: list[str]) -> list[Any]:
        raise RuntimeError("bad ranking")

    experiment = experimental_define_experiment(two_arm_config(ranking=broken_ranking))

    selection = await experiment.select({}, unit_id="user-1")

    assert selection.result == ["a", "b"]


async def test_a_failed_warmup_keeps_the_arm_cold_and_retries() -> None:
    attempts: list[int] = []

    async def warmup() -> None:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("not ready")

    experiment = experimental_define_experiment(
        two_arm_config(
            arms={
                "control": {"select": returns([]), "warmup": warmup},
                "treatment": {"select": returns([])},
            }
        )
    )

    await experiment.warm()
    await experiment.warm()
    await experiment.warm()

    assert len(attempts) == 2


def test_ranking_helper_projects_ids() -> None:
    assert ranking(["a"]) == [{"id": "a"}]
