"""Small builders shared by the experiment tests."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from ratel_ai import ExperimentConfig, ExperimentRankedItem
from ratel_ai.experiment import ExperimentArmContext


def returns(ids: list[str]) -> Callable[[Any, ExperimentArmContext], Awaitable[list[str]]]:
    async def select(_params: Any, _context: ExperimentArmContext) -> list[str]:
        return list(ids)

    return select


def raises(error: BaseException) -> Callable[[Any, ExperimentArmContext], Awaitable[list[str]]]:
    async def select(_params: Any, _context: ExperimentArmContext) -> list[str]:
        raise error

    return select


def ranking(ids: list[str]) -> list[ExperimentRankedItem]:
    return [{"id": item} for item in ids]


def two_arm_config(**overrides: Any) -> ExperimentConfig:
    config: dict[str, Any] = {
        "id": "search-v2",
        "arms": {
            "control": {"select": returns(["a", "b"])},
            "treatment": {"select": returns(["b", "c"])},
        },
        "split": [{"arm": "control", "weight": 50}, {"arm": "treatment", "weight": 50}],
        "ranking": ranking,
        "evaluation": {"references": ["peer-selection"]},
    }
    config.update(overrides)
    return config  # type: ignore[return-value]
