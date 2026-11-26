"""Deterministic arm assignment: reproduce every shared split vector.

`protocol/v1/conformance/experiment-split-vectors.json` comes from the TypeScript
SDK, whose `experiment-split-vectors.test.ts` reads the same file. A failure here
means the two SDKs put the same unit in different arms.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from experiment_helpers import ranking, returns

from ratel_ai import experimental_define_experiment

VECTORS = json.loads(
    (
        Path(__file__).resolve().parents[5]
        / "protocol/v1/conformance/experiment-split-vectors.json"
    ).read_text(encoding="utf-8")
)


@pytest.mark.parametrize(
    "vector", VECTORS["vectors"], ids=lambda v: f"{v['experiment_id']}/{v['unit_id']}"
)
async def test_assigns_the_typescript_arm(vector: dict[str, Any]) -> None:
    split = VECTORS["splits"][vector["split"]]
    experiment = experimental_define_experiment(
        {
            "id": vector["experiment_id"],
            "arms": {allocation["arm"]: {"select": returns([])} for allocation in split},
            "split": split,
            "ranking": ranking,
            "evaluation": {"references": ["peer-selection"]},
        }
    )

    selection = await experiment.select({}, unit_id=vector["unit_id"])

    assert selection.assigned_arm == vector["arm"]


def test_the_vectors_cover_every_arm_of_every_split() -> None:
    for name, split in VECTORS["splits"].items():
        assigned = {v["arm"] for v in VECTORS["vectors"] if v["split"] == name}
        positive = {a["arm"] for a in split if a["weight"] > 0}
        assert assigned <= positive
    assert len(VECTORS["vectors"]) >= 20
