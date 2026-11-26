"""The runtime event types that may leave the process toward Ratel Cloud.

The port of ``allowlist.ts``: the frozen ADR-0020 v1 set, plus the additive
``turn_start`` (ADR-0026) and the adaptive-ranking usage and status family
(2026-09-24 ADR-0020 amendment). Local-only types such as ``embedder_load`` and
the experimental ``catalog_definition`` never leave the process.
"""

from __future__ import annotations

__all__ = ["RUNTIME_EVENT_TYPES", "is_remotely_publishable"]

#: The remotely publishable event types.
RUNTIME_EVENT_TYPES = (
    "turn_start",
    "search",
    "skill_search",
    "gateway_search",
    "invoke_start",
    "invoke_end",
    "invoke_error",
    "gateway_invoke",
    "gateway_error",
    "skill_invoke",
    "index_churn",
    "skill_churn",
    "upstream_register",
    "upstream_invoke",
    "upstream_error",
    "auth_refresh",
    "auth_needs",
    "auth_flow_start",
    "auth_flow_end",
    "experiment_selection",
    "experiment_results",
    "experiment_comparison",
    "experiment_skip",
    "experiment_fallback",
    "experiment_drop",
    "experiment_invocation",
    "experiment_outcome",
    "events_dropped",
    "usage_boost",
    "usage_model_mismatch",
    "usage_cluster_policy_changed",
    "usage_ranking_status",
)

_RUNTIME_EVENT_TYPE_SET = frozenset(RUNTIME_EVENT_TYPES)


def is_remotely_publishable(event_type: object) -> bool:
    """Return whether one runtime event type may leave the process toward Ratel Cloud.

    Args:
        event_type: The ``type`` of the event.

    Returns:
        `True` for a type of `RUNTIME_EVENT_TYPES`.
    """
    return isinstance(event_type, str) and event_type in _RUNTIME_EVENT_TYPE_SET
