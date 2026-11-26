"""Served-vs-shadow ranking agreement (internal).

The Python mirror of `src/sdk/ts/src/experiment-evaluation.ts`. Comparison uses
ids only: scores are arm-specific and never compared across arms. The vector
``protocol/v1/conformance/experiment-agreement-vectors.json`` pins the values for
both SDKs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .definitions import ExperimentRankedItem

DEFAULT_EVALUATION_K = 10


@dataclass(frozen=True)
class ExperimentRankingAgreement:
    """Agreement of one served ranking with one shadow ranking.

    Attributes:
        top1: Both rankings have the same first id.
        exact_order: Both rankings have the same ids in the same order.
        overlap_count: The number of ids in both rankings.
        jaccard_at_k: The Jaccard index of the first ``k`` ids of each ranking.
        k: The rank window of ``jaccard_at_k``.
        item_attrs: For the attributes the two first items share, whether they agree.
        result_attrs: For each result-level attribute, whether both arms agree.
    """

    top1: bool
    exact_order: bool
    overlap_count: int
    jaccard_at_k: float
    k: int
    item_attrs: dict[str, bool] | None = None
    result_attrs: dict[str, bool] | None = None


def compare_rankings(
    served: Sequence[ExperimentRankedItem],
    shadow: Sequence[ExperimentRankedItem],
    k: int,
    served_result_attrs: Mapping[str, Any] | None = None,
    shadow_result_attrs: Mapping[str, Any] | None = None,
) -> ExperimentRankingAgreement:
    """Compare two ordered rankings by id."""
    served_ids = [item["id"] for item in served]
    shadow_ids = [item["id"] for item in shadow]
    item_attrs = None
    if served and shadow:
        item_attrs = _compare_shared_attributes(
            served[0].get("attrs", {}), shadow[0].get("attrs", {})
        )
    result_attrs = None
    if served_result_attrs is not None and shadow_result_attrs is not None:
        result_attrs = _compare_result_attributes(served_result_attrs, shadow_result_attrs)
    return ExperimentRankingAgreement(
        top1=(served_ids[0] if served_ids else None) == (shadow_ids[0] if shadow_ids else None),
        exact_order=served_ids == shadow_ids,
        overlap_count=len(set(served_ids) & set(shadow_ids)),
        jaccard_at_k=_jaccard(set(served_ids[:k]), set(shadow_ids[:k])),
        k=k,
        item_attrs=item_attrs,
        result_attrs=result_attrs,
    )


def resolve_evaluation_k(request_k: int | None, configured_k: int | None) -> int:
    """Resolve the Jaccard rank window of one selection."""
    if isinstance(request_k, int) and not isinstance(request_k, bool) and request_k >= 1:
        return request_k
    return DEFAULT_EVALUATION_K if configured_k is None else configured_k


def _jaccard(served: set[str], shadow: set[str]) -> float:
    if not served and not shadow:
        return 1
    intersection = len(served & shadow)
    return intersection / (len(served) + len(shadow) - intersection)


def _compare_shared_attributes(
    served: Mapping[str, Any], shadow: Mapping[str, Any]
) -> dict[str, bool]:
    return {key: _same(served[key], shadow[key]) for key in served if key in shadow}


def _compare_result_attributes(
    served: Mapping[str, Any], shadow: Mapping[str, Any]
) -> dict[str, bool]:
    keys = list(dict.fromkeys([*served, *shadow]))
    return {
        key: key in served and key in shadow and _same(served[key], shadow[key]) for key in keys
    }


def _same(left: Any, right: Any) -> bool:
    # JavaScript `===` semantics: `true !== 1`, which Python's `==` treats as equal.
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    return bool(left == right)
