"""Served-vs-shadow agreement: reproduce every shared agreement vector.

`protocol/v1/conformance/experiment-agreement-vectors.json` comes from the TypeScript
`compareRankings`, and `experiment-agreement-vectors.test.ts` reads the same file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ratel_ai.experiment.evaluation import compare_rankings, resolve_evaluation_k
from ratel_ai.experiment.sink import agreement_fields

VECTORS = json.loads(
    (
        Path(__file__).resolve().parents[5]
        / "protocol/v1/conformance/experiment-agreement-vectors.json"
    ).read_text(encoding="utf-8")
)


@pytest.mark.parametrize("vector", VECTORS["vectors"], ids=lambda v: v["name"])
def test_scores_the_typescript_agreement(vector: dict[str, Any]) -> None:
    options = vector["options"]

    agreement = compare_rankings(
        vector["served"],
        vector["shadow"],
        options["k"],
        options.get("servedResultAttrs"),
        options.get("shadowResultAttrs"),
    )

    assert agreement_fields(agreement) == vector["agreement"]


def test_boolean_and_number_attributes_never_agree() -> None:
    agreement = compare_rankings(
        [{"id": "a", "attrs": {"flag": True}}], [{"id": "a", "attrs": {"flag": 1}}], 10
    )

    assert agreement.item_attrs == {"flag": False}


@pytest.mark.parametrize(
    ("request_k", "configured_k", "expected"),
    [(None, None, 10), (None, 3, 3), (2, 3, 2), (0, 3, 3), (True, 4, 4)],
)
def test_resolves_the_rank_window(request_k: Any, configured_k: Any, expected: int) -> None:
    assert resolve_evaluation_k(request_k, configured_k) == expected
