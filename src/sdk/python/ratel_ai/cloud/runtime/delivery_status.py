"""Shared, fail-open delivery health of runtime events and catalog snapshots.

The port of ``delivery-status.ts``. `DeliveryStatus.snapshot` returns plain,
JSON-serializable dicts with Unix-millisecond timestamps. The first transition
into each failing kind warns one time (never with the API key), and the warning
comes back only after that kind recovers.
"""

from __future__ import annotations

import time
import warnings
from collections.abc import Callable, Mapping
from typing import Literal, TypedDict

from ..transport import CloudResponse
from .retry import Clock

__all__ = [
    "DeliveryKind",
    "DeliveryOverall",
    "DeliveryResult",
    "DeliveryStatus",
    "EventsDeliveryStatus",
    "RuntimeDeliveryStatus",
    "SnapshotDeliveryStatus",
    "StatusChangeHandler",
    "classify_delivery",
]

#: The outcome kind of one delivery attempt.
DeliveryKind = Literal[
    "ok", "auth", "gated", "not_deployed", "rate_limited", "rejected_payload", "network"
]

#: The overall health: ``disabled`` when ``RATEL_CLOUD_EVENTS=off``.
DeliveryOverall = Literal["ok", "pending", "degraded", "blocked", "disabled"]


class DeliveryResult(TypedDict):
    """The classified outcome of one delivery attempt."""

    kind: DeliveryKind
    status: int | None
    message: str | None


class EventsDeliveryStatus(TypedDict):
    """Event delivery health. Timestamps are Unix milliseconds."""

    last_outcome: DeliveryResult | None
    last_accepted_at: int | None
    last_attempt_at: int | None
    last_error: str | None
    accepted: int
    rejected: int
    dropped: int


class SnapshotDeliveryStatus(TypedDict):
    """Catalog snapshot health of one source. Timestamps are Unix milliseconds."""

    last_durable_at: int | None
    pending_since: int | None
    last_outcome: DeliveryResult | None


class RuntimeDeliveryStatus(TypedDict):
    """The delivery health of one attachment, as ``status()`` returns it."""

    overall: DeliveryOverall
    events: EventsDeliveryStatus
    snapshots: dict[str, SnapshotDeliveryStatus]


#: Called with the new status when an outcome kind changes.
StatusChangeHandler = Callable[[RuntimeDeliveryStatus], None]

_BLOCKED_KINDS = frozenset({"auth", "gated", "not_deployed"})
_FAILURE_MEANINGS: Mapping[str, str] = {
    "auth": "authentication failed — facts are not persisting",
    "gated": (
        "requests are intercepted by an auth gate before reaching Ratel Cloud"
        " — facts are not persisting"
    ),
    "not_deployed": "the runtime ingestion endpoint is not deployed — facts are not persisting",
    "rate_limited": "Ratel Cloud is rate limiting delivery — facts may be delayed",
    "rejected_payload": "Ratel Cloud rejected the payload — some facts are not persisting",
    "network": "Ratel Cloud cannot be reached — facts may be delayed",
}


class DeliveryStatus:
    """Shared, fail-open delivery health for runtime events and catalog snapshots."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        warn_on_failure: bool = True,
        on_status_change: StatusChangeHandler | None = None,
        clock: Clock | None = None,
    ) -> None:
        """Create the tracker.

        Args:
            enabled: `False` makes ``overall`` always ``disabled``.
            warn_on_failure: Warn one time per failing kind until it recovers.
            on_status_change: Called when an outcome kind changes. Its
                exceptions are suppressed.
            clock: The clock of the timestamps. Defaults to `time.time`.
        """
        self._enabled = enabled
        self._warn_on_failure = warn_on_failure
        self._on_status_change = on_status_change
        self._clock = clock if clock is not None else time.time
        self._events: EventsDeliveryStatus = {
            "last_outcome": None,
            "last_accepted_at": None,
            "last_attempt_at": None,
            "last_error": None,
            "accepted": 0,
            "rejected": 0,
            "dropped": 0,
        }
        self._snapshots: dict[str, SnapshotDeliveryStatus] = {}
        self._target_kinds: dict[str, DeliveryKind] = {}
        self._warned_kinds: set[DeliveryKind] = set()

    def snapshot(self) -> RuntimeDeliveryStatus:
        """Return a copy of the current health."""
        snapshots: dict[str, SnapshotDeliveryStatus] = {
            source_id: {
                "last_durable_at": status["last_durable_at"],
                "pending_since": status["pending_since"],
                "last_outcome": _copy_result(status["last_outcome"]),
            }
            for source_id, status in self._snapshots.items()
        }
        events: EventsDeliveryStatus = {
            **self._events,
            "last_outcome": _copy_result(self._events["last_outcome"]),
        }
        return {"overall": self._overall(), "events": events, "snapshots": snapshots}

    def record_events(
        self, result: DeliveryResult, *, accepted: int = 0, rejected: int = 0
    ) -> None:
        """Record the outcome of one event delivery attempt.

        Args:
            result: The classified outcome.
            accepted: Events that Cloud accepted.
            rejected: Events that were dropped as rejected.
        """
        try:
            now = self._now_ms()
            accepted_count = _safe_count(accepted)
            self._events["last_outcome"] = result
            self._events["last_attempt_at"] = now
            self._events["last_error"] = result["message"]
            self._events["accepted"] += accepted_count
            self._events["rejected"] += _safe_count(rejected)
            if result["kind"] == "ok" or accepted_count > 0:
                self._events["last_accepted_at"] = now
            self._transition("events", result["kind"])
        except Exception:
            # Observability cannot affect agent delivery.
            pass

    def record_event_rejections(self, count: int) -> None:
        """Record events that could not be serialized for delivery."""
        if count <= 0:
            return
        self.record_events(
            {
                "kind": "rejected_payload",
                "status": None,
                "message": "one or more events could not be serialized for delivery",
            },
            rejected=count,
        )

    def record_event_drops(self, count: int) -> None:
        """Record events lost to the bounded queue or to a closed publisher."""
        self._events["dropped"] += _safe_count(count)

    def record_snapshot_pending(self, source_id: str) -> None:
        """Mark that ``source_id`` has a snapshot that is not durable yet."""
        status = self._snapshot_for(source_id)
        if status["pending_since"] is None:
            status["pending_since"] = self._now_ms()

    def record_snapshot(self, source_id: str, result: DeliveryResult) -> None:
        """Record a snapshot attempt that did not make the snapshot durable."""
        try:
            self._snapshot_for(source_id)["last_outcome"] = result
            self._transition(f"snapshot:{source_id}", result["kind"])
        except Exception:
            pass

    def record_snapshot_durable(
        self, source_id: str, result: DeliveryResult, *, settled: bool
    ) -> None:
        """Record a durable snapshot.

        Args:
            source_id: The source.
            result: The classified outcome.
            settled: Whether the durable snapshot is the latest one of the source.
        """
        try:
            status = self._snapshot_for(source_id)
            status["last_outcome"] = result
            status["last_durable_at"] = self._now_ms()
            if settled:
                status["pending_since"] = None
            self._transition(f"snapshot:{source_id}", result["kind"])
        except Exception:
            pass

    def record_snapshot_settled(self, source_id: str) -> None:
        """Record that the latest snapshot of ``source_id`` is already durable."""
        try:
            status = self._snapshot_for(source_id)
            if status["pending_since"] is None:
                return
            status["last_outcome"] = {"kind": "ok", "status": None, "message": None}
            status["pending_since"] = None
            self._transition(f"snapshot:{source_id}", "ok")
        except Exception:
            pass

    def _overall(self) -> DeliveryOverall:
        if not self._enabled:
            return "disabled"
        outcomes = [self._events["last_outcome"]] + [
            status["last_outcome"] for status in self._snapshots.values()
        ]
        kinds = [outcome["kind"] for outcome in outcomes if outcome is not None]
        if any(kind in _BLOCKED_KINDS for kind in kinds):
            return "blocked"
        if any(kind != "ok" for kind in kinds):
            return "degraded"
        if self._events["last_outcome"] is None or any(
            status["pending_since"] is not None for status in self._snapshots.values()
        ):
            return "pending"
        return "ok"

    def _snapshot_for(self, source_id: str) -> SnapshotDeliveryStatus:
        existing = self._snapshots.get(source_id)
        if existing is not None:
            return existing
        created: SnapshotDeliveryStatus = {
            "last_durable_at": None,
            "pending_since": None,
            "last_outcome": None,
        }
        self._snapshots[source_id] = created
        return created

    def _transition(self, target: str, kind: DeliveryKind) -> None:
        previous = self._target_kinds.get(target)
        if previous == kind:
            return
        self._target_kinds[target] = kind
        if previous is not None and previous not in self._target_kinds.values():
            self._warned_kinds.discard(previous)
        if kind != "ok" and kind not in self._warned_kinds:
            self._warned_kinds.add(kind)
            if self._warn_on_failure:
                _warn(kind)
        if self._on_status_change is not None:
            try:
                self._on_status_change(self.snapshot())
            except Exception:
                # User callbacks remain fail-open.
                pass

    def _now_ms(self) -> int:
        return int(self._clock() * 1_000)


def classify_delivery(response: CloudResponse | None) -> DeliveryResult:
    """Classify one delivery response.

    Args:
        response: The response, or `None` when no response arrived.

    Returns:
        The classified outcome.
    """
    if response is None:
        return {
            "kind": "network",
            "status": None,
            "message": "request failed before reaching Ratel Cloud",
        }
    status = response.status
    if 200 <= status < 300:
        return {"kind": "ok", "status": status, "message": None}
    if 300 <= status < 400:
        return {
            "kind": "gated",
            "status": status,
            "message": f"request intercepted with HTTP {status}",
        }
    if status == 401:
        return {"kind": "auth", "status": status, "message": "authentication failed"}
    if status == 404:
        return {
            "kind": "not_deployed",
            "status": status,
            "message": "runtime ingestion endpoint not found",
        }
    if status == 429:
        return {"kind": "rate_limited", "status": status, "message": "rate limited"}
    if 400 <= status < 500:
        return {
            "kind": "rejected_payload",
            "status": status,
            "message": f"payload rejected with HTTP {status}",
        }
    return {"kind": "network", "status": status, "message": f"delivery failed with HTTP {status}"}


def _warn(kind: DeliveryKind) -> None:
    try:
        warnings.warn(
            f"[ratel-cloud/runtime] {kind}: {_FAILURE_MEANINGS[kind]}", RuntimeWarning, stacklevel=2
        )
    except Exception:
        # Diagnostics remain fail-open, also under a warnings filter set to "error".
        pass


def _copy_result(result: DeliveryResult | None) -> DeliveryResult | None:
    return None if result is None else {**result}


def _safe_count(value: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0
