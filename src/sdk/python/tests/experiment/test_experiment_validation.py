"""Define-time and select-time validation: the TypeScript errors, in snake_case."""

from __future__ import annotations

from typing import Any

import pytest
from experiment_helpers import two_arm_config

from ratel_ai import experimental_define_experiment

PREFIX = "experimental_define_experiment"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"arms": {}}, "arms must not be empty"),
        ({"fallback_arm": "nope"}, 'fallback_arm "nope" is not a declared arm'),
        (
            {"shadow_policy": {"concurrency": 0}},
            "shadow_policy.concurrency must be a positive integer",
        ),
        (
            {"shadow_policy": {"concurrency": True}},
            "shadow_policy.concurrency must be a positive integer",
        ),
        (
            {"split": [{"arm": "control", "weight": 1}, {"arm": "control", "weight": 1}]},
            "split must allocate every declared arm exactly once",
        ),
        (
            {"split": [{"arm": "control", "weight": 1}]},
            "split must allocate every declared arm exactly once",
        ),
        (
            {"split": [{"arm": "control", "weight": 1}, {"arm": "other", "weight": 1}]},
            "split must allocate every declared arm exactly once",
        ),
        (
            {"split": [{"arm": "control", "weight": -1}, {"arm": "treatment", "weight": 1}]},
            "split weight must be a non-negative safe integer",
        ),
        (
            {"split": [{"arm": "control", "weight": 0.5}, {"arm": "treatment", "weight": 1}]},
            "split weight must be a non-negative safe integer",
        ),
        (
            {"split": [{"arm": "control", "weight": 2**53}, {"arm": "treatment", "weight": 1}]},
            "split weight must be a non-negative safe integer",
        ),
        (
            {"split": [{"arm": "control", "weight": 2**53 - 1}, {"arm": "treatment", "weight": 1}]},
            "split total must be a positive safe integer",
        ),
        (
            {"split": [{"arm": "control", "weight": 0}, {"arm": "treatment", "weight": 0}]},
            "split total must be a positive safe integer",
        ),
        ({"evaluation": {"references": []}}, "evaluation.references must not be empty"),
        (
            {"evaluation": {"references": ["peer-selection"], "k": 0}},
            "evaluation.k must be an integer >= 1",
        ),
        (
            {"evaluation": {"references": [{"kind": "other"}]}},
            'evaluation reference.kind must be "invocation"',
        ),
        (
            {
                "evaluation": {
                    "references": [
                        {"kind": "invocation", "window": {"turns": 1}, "attribution": "x"}
                    ]
                }
            },
            'invocation attribution must be "last-selection" or "all-in-window"',
        ),
        (
            {"evaluation": {"references": [{"kind": "invocation", "window": {}}]}},
            "invocation window requires at least one bound",
        ),
        (
            {"evaluation": {"references": [{"kind": "invocation", "window": {"turns": 0}}]}},
            "invocation window.turns must be a positive safe integer",
        ),
        (
            {
                "evaluation": {
                    "references": [{"kind": "invocation", "window": {"max_age_ms": float("inf")}}]
                }
            },
            "invocation window.max_age_ms must be positive and finite",
        ),
        (
            {"evaluation": {"references": ["peer-selection", "peer-selection"]}},
            "duplicate peer-selection reference",
        ),
        (
            {
                "evaluation": {
                    "references": [
                        {"kind": "invocation", "window": {"turns": 1}},
                        {"kind": "invocation", "window": {"turns": 2}},
                    ]
                }
            },
            "duplicate invocation reference",
        ),
    ],
)
def test_rejects_a_misconfigured_definition(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=f"^{PREFIX}: {_escape(message)}$"):
        experimental_define_experiment(two_arm_config(**overrides))


def test_accepts_a_definition_without_a_split() -> None:
    config = two_arm_config()
    del config["split"]  # type: ignore[misc]

    experimental_define_experiment(config)


async def test_select_rejects_an_undeclared_arm() -> None:
    experiment = experimental_define_experiment(two_arm_config())

    with pytest.raises(ValueError, match=f'^{PREFIX}.select: arm "nope" is not a declared arm$'):
        await experiment.select({}, unit_id="u", arm="nope")


async def test_select_needs_an_arm_without_a_split() -> None:
    config = two_arm_config()
    del config["split"]  # type: ignore[misc]
    experiment = experimental_define_experiment(config)

    with pytest.raises(ValueError, match=f"^{PREFIX}.select: arm or split is required$"):
        await experiment.select({}, unit_id="u")


@pytest.mark.parametrize(
    "key", ["ratel.experiment.arm", "ratel.experiment.id", "error.type", "gen_ai.tool.name"]
)
async def test_select_rejects_a_reserved_telemetry_attribute(key: str) -> None:
    experiment = experimental_define_experiment(two_arm_config())

    with pytest.raises(ValueError, match=f'"{_escape(key)}" is a reserved telemetry attribute'):
        await experiment.select({}, unit_id="u", attributes={key: "x"})


def _escape(text: str) -> str:
    import re

    return re.escape(text)
