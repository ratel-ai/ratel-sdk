"""Define-time validation and deterministic arm assignment.

The Python mirror of `src/sdk/ts/src/experiment-validation.ts`. The messages are
the TypeScript messages with the snake_case names of this SDK.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from .definitions import ExperimentConfig, ExperimentEvaluation

_MAX_SAFE_INTEGER = 2**53 - 1
_PREFIX = "experimental_define_experiment"


def validate_config(config: ExperimentConfig) -> None:
    """Reject a misconfigured experiment definition at define time (internal)."""
    arms = list(config["arms"])
    if not arms:
        raise ValueError(f"{_PREFIX}: arms must not be empty")
    fallback_arm = config.get("fallback_arm")
    if fallback_arm is not None and fallback_arm not in arms:
        raise ValueError(f'{_PREFIX}: fallback_arm "{fallback_arm}" is not a declared arm')
    concurrency = config.get("shadow_policy", {}).get("concurrency")
    if concurrency is not None and (not _is_int(concurrency) or concurrency < 1):
        raise ValueError(f"{_PREFIX}: shadow_policy.concurrency must be a positive integer")
    _validate_evaluation(config["evaluation"])
    split = config.get("split")
    if split is None:
        return

    allocations = {allocation["arm"] for allocation in split}
    if (
        len(allocations) != len(split)
        or len(allocations) != len(arms)
        or any(arm not in allocations for arm in arms)
    ):
        raise ValueError(f"{_PREFIX}: split must allocate every declared arm exactly once")

    total = 0
    for allocation in split:
        weight = allocation["weight"]
        if not _is_safe_int(weight) or weight < 0:
            raise ValueError(f"{_PREFIX}: split weight must be a non-negative safe integer")
        total += weight
        if not _is_safe_int(total):
            raise ValueError(f"{_PREFIX}: split total must be a positive safe integer")
    if total == 0:
        raise ValueError(f"{_PREFIX}: split total must be a positive safe integer")


def assign_arm(config: ExperimentConfig, unit_id: str, arm: str | None) -> str:
    """Resolve the arm of one selection: the explicit override or the split (internal).

    The bucket is the SHA-256 of the UTF-8 JSON array ``[experiment id, unit id]``
    (compact, as ``JSON.stringify`` writes it), read as a big-endian integer, modulo
    the split total. The vector ``protocol/v1/conformance/experiment-split-vectors.json``
    pins it for both SDKs.
    """
    if arm is not None:
        if arm not in config["arms"]:
            raise ValueError(f'{_PREFIX}.select: arm "{arm}" is not a declared arm')
        return arm
    split = config.get("split")
    if split is None:
        raise ValueError(f"{_PREFIX}.select: arm or split is required")

    total = sum(allocation["weight"] for allocation in split)
    key = json.dumps([config["id"], unit_id], ensure_ascii=False, separators=(",", ":"))
    bucket = int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16) % total
    cumulative = 0
    for allocation in split:
        cumulative += allocation["weight"]
        if bucket < cumulative:
            return allocation["arm"]
    raise ValueError(f"{_PREFIX}: split has no positive allocation")


def validate_reported_outcome(selection_id: Any, label: Any = None, score: Any = None) -> None:
    """Reject a malformed delayed outcome report (internal)."""
    if not isinstance(selection_id, str) or not selection_id:
        raise ValueError(f"{_PREFIX}.report_outcome: selection_id must be a non-empty string")
    if label is None and score is None:
        raise ValueError(f"{_PREFIX}.report_outcome: label or score is required")
    if label is not None and (not isinstance(label, str) or not label):
        raise ValueError(f"{_PREFIX}.report_outcome: label must be a non-empty string")
    if score is not None and (
        isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score)
    ):
        raise ValueError(f"{_PREFIX}.report_outcome: score must be finite")


def _validate_evaluation(evaluation: ExperimentEvaluation) -> None:
    references: Sequence[Any] = evaluation["references"]
    if len(references) == 0:
        raise ValueError(f"{_PREFIX}: evaluation.references must not be empty")
    k = evaluation.get("k")
    if k is not None and (not _is_int(k) or k < 1):
        raise ValueError(f"{_PREFIX}: evaluation.k must be an integer >= 1")

    peer_references = 0
    invocation_references = 0
    for reference in references:
        if reference == "peer-selection":
            peer_references += 1
            continue
        if not isinstance(reference, Mapping) or reference.get("kind") != "invocation":
            raise ValueError(f'{_PREFIX}: evaluation reference.kind must be "invocation"')
        attribution = reference.get("attribution")
        if attribution is not None and attribution not in ("last-selection", "all-in-window"):
            raise ValueError(
                f'{_PREFIX}: invocation attribution must be "last-selection" or "all-in-window"'
            )
        invocation_references += 1
        window = reference.get("window", {})
        turns = window.get("turns")
        max_age_ms = window.get("max_age_ms")
        if turns is None and max_age_ms is None:
            raise ValueError(f"{_PREFIX}: invocation window requires at least one bound")
        if turns is not None and (not _is_safe_int(turns) or turns < 1):
            raise ValueError(f"{_PREFIX}: invocation window.turns must be a positive safe integer")
        if max_age_ms is not None and (
            isinstance(max_age_ms, bool)
            or not isinstance(max_age_ms, (int, float))
            or not math.isfinite(max_age_ms)
            or max_age_ms <= 0
        ):
            raise ValueError(f"{_PREFIX}: invocation window.max_age_ms must be positive and finite")

    if peer_references > 1:
        raise ValueError(f"{_PREFIX}: duplicate peer-selection reference")
    if invocation_references > 1:
        raise ValueError(f"{_PREFIX}: duplicate invocation reference")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_safe_int(value: Any) -> bool:
    return _is_int(value) and -_MAX_SAFE_INTEGER <= value <= _MAX_SAFE_INTEGER


def hash_unit_id(unit_id: str) -> str:
    """Hash a raw assignment unit to its pseudonymous join key (internal).

    The raw unit id never leaves the process: telemetry and runtime events carry
    the first 16 hex characters of its SHA-256.
    """
    return hashlib.sha256(unit_id.encode("utf-8")).hexdigest()[:16]
