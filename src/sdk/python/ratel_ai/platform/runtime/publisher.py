"""Fail-open, in-memory delivery of runtime event envelopes to ``POST /events``.

The port of ``publisher.ts``. The queue is bounded and drops the oldest event
(an ``events_dropped`` event reports each loss window). A drain sends the
queue in batches of at most 5,000 events and 3,900,000 bytes. A transient
failure retries with backoff. Any other failure drops the batch and reports it
one time through ``on_rejected``. Nothing raises into the caller.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from ...runtime_events import new_runtime_event_id
from ..config import platform_switch_on, resolve_platform_config
from ..transport import PlatformHttpClient, PlatformResponse, PlatformTransport
from .delivery_status import DeliveryResult, DeliveryStatus, classify_delivery
from .retry import (
    BackgroundTasks,
    Clock,
    RetryOptions,
    Sleep,
    create_retry_config,
    non_negative,
    positive_integer,
    request_with_retry,
    start_timer,
)
from .types import RuntimeEvent

__all__ = [
    "EVENTS_ENV",
    "RUNTIME_EVENT_BATCH_MAX_BYTES",
    "RUNTIME_EVENT_BATCH_MAX_EVENTS",
    "RUNTIME_EVENT_MAX_BYTES",
    "RejectionHandler",
    "RuntimeEventRejection",
    "RuntimeEventsPublisher",
    "events_enabled",
]

#: The most events that one ``POST /events`` batch carries.
RUNTIME_EVENT_BATCH_MAX_EVENTS = 5_000

#: The most bytes that one ``POST /events`` body has.
RUNTIME_EVENT_BATCH_MAX_BYTES = 3_900_000

#: The most bytes that one serialized event has. A larger event is rejected.
RUNTIME_EVENT_MAX_BYTES = 64 * 1_024

#: The kill switch: ``RATEL_PLATFORM_EVENTS=off`` disables event delivery only.
EVENTS_ENV = "RATEL_PLATFORM_EVENTS"

_DEFAULT_QUEUE_CAPACITY = 1_024
_DEFAULT_FLUSH_INTERVAL_MS = 1_000.0
# Platform caches its ingest-flag decision, so a deferred batch retries slowly.
_DEFERRED_RETRY_DELAY_MS = 30_000.0
_DEFERRED_RETRY_MAX_DELAY_MS = 5 * 60_000.0
_BATCH_ENVELOPE_BYTES = len(b'{"events":[]}')
# Every status: the publishers classify the response themselves.
_ALL_STATUSES = range(100, 600)
_MISSING_KEY: DeliveryResult = {
    "kind": "auth",
    "status": None,
    "message": "no API key: pass api_key or set RATEL_API_KEY",
}


@dataclass(frozen=True)
class RuntimeEventRejection:
    """One event (or catalog tool) that did not reach Ratel Platform, and why.

    Attributes:
        event_id: The event id, or a tool id for a catalog omission, or `None`.
        reason: Why it was rejected.
    """

    event_id: str | None
    reason: str


#: Called with the terminal rejects of one delivery. Its exceptions are suppressed.
RejectionHandler = Callable[[Sequence[RuntimeEventRejection]], None]


@dataclass
class _SerializedBatch:
    events: list[RuntimeEvent]
    event_ids: list[str | None]


@dataclass
class _DropWindow:
    count: int
    start_ts: float
    end_ts: float
    session_id: Any
    source_id: Any


class RuntimeEventsPublisher:
    """Fail-open, in-memory delivery of runtime event envelopes to Ratel Platform."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        transport: PlatformTransport | None = None,
        queue_capacity: int | None = None,
        flush_interval_ms: float | None = None,
        on_rejected: RejectionHandler | None = None,
        retry: RetryOptions | None = None,
        sleep: Sleep | None = None,
        clock: Clock | None = None,
        delivery_status: DeliveryStatus | None = None,
    ) -> None:
        """Create the publisher.

        Args:
            api_key: Project API key. Defaults to ``RATEL_API_KEY``.
            base_url: API root. Defaults to ``RATEL_BASE_URL``, then production.
            timeout: Per-request timeout in seconds.
            transport: The HTTP transport. Defaults to `UrllibTransport`.
            queue_capacity: Events held before the oldest is dropped. Default 1,024.
            flush_interval_ms: Delay before queued events drain on their own.
                Default 1 second.
            on_rejected: Called with the terminal rejects of a delivery.
            retry: How one request retries.
            sleep: The timer. Defaults to `asyncio.sleep`.
            clock: The clock, in seconds. Defaults to `time.time`.
            delivery_status: The shared health tracker.
        """
        config = resolve_platform_config(api_key=api_key, base_url=base_url, timeout=timeout)
        self._has_key = bool(config.api_key)
        self._http = PlatformHttpClient(config, transport=transport)
        self._on_rejected = on_rejected
        self._retry = create_retry_config(retry)
        self._sleep: Sleep = sleep if sleep is not None else asyncio.sleep
        self._clock: Clock = clock if clock is not None else time.time
        self._enabled = events_enabled()
        self._queue_capacity = positive_integer(queue_capacity, _DEFAULT_QUEUE_CAPACITY)
        self._flush_interval_ms = non_negative(flush_interval_ms, _DEFAULT_FLUSH_INTERVAL_MS)
        self._status = (
            delivery_status
            if delivery_status is not None
            else DeliveryStatus(enabled=self._enabled, warn_on_failure=False, clock=self._clock)
        )
        self._queue: list[RuntimeEvent] = []
        self._drop_windows: dict[tuple[Any, Any], _DropWindow] = {}
        self._overflow_window: _DropWindow | None = None
        self._tasks = BackgroundTasks()
        self._timer: asyncio.Task[None] | None = None
        self._lock: asyncio.Lock | None = None
        self._closed = False
        self._deferral_streak = 0

    def publish(self, event: RuntimeEvent) -> None:
        """Queue one event. It never raises and never blocks.

        Args:
            event: The event envelope.
        """
        if not self._enabled:
            return
        if self._closed:
            # Late native drains after close() may not schedule delivery anymore.
            self._status.record_event_drops(1)
            return
        try:
            if len(self._queue) >= self._queue_capacity:
                self._record_drop(self._queue.pop(0))
            self._queue.append(event)
            self._schedule_flush(self._flush_interval_ms)
        except Exception:
            # Malformed caller objects must not escape into the agent operation.
            pass

    async def flush(self) -> None:
        """Deliver every queued event now. It never raises."""
        self._cancel_timer()
        lock = self._drain_lock()
        async with lock:
            await self._drain_queued()

    async def close(self) -> None:
        """Stop accepting events, deliver everything already queued, stop the timers."""
        self._closed = True
        await self.flush()
        self._cancel_timer()
        await self._tasks.wait()

    async def verify(self) -> DeliveryResult:
        """Probe event ingestion with an empty batch. The queue does not change.

        Returns:
            The classified outcome. It is also recorded in the delivery status.
        """
        try:
            _, result = await self._post([])
        except Exception:
            result = classify_delivery(None)
        self._status.record_events(result)
        return result

    async def _drain_queued(self) -> None:
        try:
            events = self._queue
            self._queue = []
            events.extend(self._take_drop_events())
            batches, rejected = _create_batches(events)
            self._status.record_event_rejections(len(rejected))
            self._surface(rejected)
            deferred: list[RuntimeEvent] = []
            for batch in batches:
                if await self._send_batch(batch) == "deferred":
                    deferred.extend(batch.events)
            if batches:
                self._deferral_streak = self._deferral_streak + 1 if deferred else 0
            if deferred:
                self._requeue_deferred(deferred)
        except Exception:
            # Serialization and runtime failures are terminal but always fail-open.
            pass

    async def _send_batch(self, batch: _SerializedBatch) -> Literal["settled", "deferred"]:
        response, result = await self._post(batch.events)
        if response is not None and 200 <= response.status < 300:
            body = response.json
            if isinstance(body, Mapping) and body.get("deferred") is True:
                # Reachable and authenticated, but nothing persisted: record no counts.
                self._status.record_events(result)
                return "deferred"
            accepted, rejected = _parse_delivery_report(body, len(batch.event_ids))
            outcome: DeliveryResult = (
                result
                if not rejected
                else {
                    "kind": "rejected_payload",
                    "status": response.status,
                    "message": f"{len(rejected)} event payloads were rejected",
                }
            )
            self._status.record_events(outcome, accepted=accepted, rejected=len(rejected))
            self._surface(rejected)
            return "settled"
        self._status.record_events(result, rejected=len(batch.event_ids))
        if response is not None:
            reason = f"batch rejected with HTTP {response.status}"
        elif result is _MISSING_KEY:
            reason = "batch not sent: no API key"
        else:
            reason = "batch delivery failed after retries"
        self._surface([RuntimeEventRejection(event_id, reason) for event_id in batch.event_ids])
        return "settled"

    async def _post(
        self, events: list[RuntimeEvent]
    ) -> tuple[PlatformResponse | None, DeliveryResult]:
        if not self._has_key:
            # Fail-open: the request cannot authenticate, so report it as auth.
            return None, _MISSING_KEY

        async def send() -> PlatformResponse:
            return await self._http.request(
                "POST", "/events", body={"events": events}, accept_statuses=_ALL_STATUSES
            )

        response = await request_with_retry(send, self._retry, self._sleep, self._clock)
        return response, classify_delivery(response)

    def _requeue_deferred(self, events: list[RuntimeEvent]) -> None:
        """Platform accepted the request but its ingest flag is off: retry later, slowly."""
        if self._closed:
            # A sealed publisher cannot retry past close(); count the loss instead.
            self._status.record_event_drops(len(events))
            return
        overflow = max(len(events) + len(self._queue) - self._queue_capacity, 0)
        # Requeued events are the oldest, so overflow keeps the drop-oldest discipline.
        for dropped in events[:overflow]:
            self._record_drop(dropped)
        self._queue = events[overflow:] + self._queue
        self._cancel_timer()
        self._schedule_flush(self._deferred_delay_ms())

    def _deferred_delay_ms(self) -> float:
        """30 s floor, doubling across consecutive deferred cycles, capped at 5 minutes."""
        growth = 2.0 ** min(max(self._deferral_streak - 1, 0), 30)
        return min(_DEFERRED_RETRY_DELAY_MS * growth, _DEFERRED_RETRY_MAX_DELAY_MS)

    def _schedule_flush(self, delay_ms: float) -> None:
        if self._closed or self._timer is not None:
            return
        self._timer = start_timer(delay_ms, self._on_timer, self._sleep, self._tasks)

    def _on_timer(self) -> None:
        self._timer = None
        self._tasks.spawn(self.flush())

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _drain_lock(self) -> asyncio.Lock:
        # Created on first use, so it binds to the running loop (Python 3.9).
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _surface(self, rejected: Sequence[RuntimeEventRejection]) -> None:
        if not rejected or self._on_rejected is None:
            return
        with contextlib.suppress(Exception):
            # User-provided observability callbacks are fail-open too.
            self._on_rejected(list(rejected))

    def _record_drop(self, event: RuntimeEvent) -> None:
        self._status.record_event_drops(1)
        ts = event.get("ts")
        timestamp = ts if isinstance(ts, (int, float)) and not isinstance(ts, bool) else None
        if timestamp is None:
            timestamp = int(self._clock() * 1_000)
        key = (event.get("source_id"), event.get("session_id"))
        window = self._drop_windows.get(key)
        if window is not None:
            _extend(window, timestamp)
            return
        if len(self._drop_windows) >= self._queue_capacity:
            # Keep the total, and prefer bounded memory over exact attribution.
            if self._overflow_window is not None:
                _extend(self._overflow_window, timestamp)
            return
        window = _DropWindow(1, timestamp, timestamp, key[1], key[0])
        self._drop_windows[key] = window
        if self._overflow_window is None:
            self._overflow_window = window

    def _take_drop_events(self) -> list[RuntimeEvent]:
        windows = list(self._drop_windows.values())
        self._drop_windows.clear()
        self._overflow_window = None
        now_ms = int(self._clock() * 1_000)
        return [
            {
                "v": 2,
                "event_id": new_runtime_event_id(now_ms),
                "ts": now_ms,
                "session_id": window.session_id,
                "source_id": window.source_id,
                "type": "events_dropped",
                "dropped_count": window.count,
                "reason": "queue_overflow",
                "window_start_ts": window.start_ts,
                "window_end_ts": window.end_ts,
            }
            for window in windows
        ]


def events_enabled() -> bool:
    """Return `False` when ``RATEL_PLATFORM_EVENTS`` is ``off`` (any case, trimmed).

    The deprecated ``RATEL_CLOUD_EVENTS`` applies when ``RATEL_PLATFORM_EVENTS`` is not set.
    """
    return platform_switch_on("events")


def _create_batches(
    events: list[RuntimeEvent],
) -> tuple[list[_SerializedBatch], list[RuntimeEventRejection]]:
    batches: list[_SerializedBatch] = []
    rejected: list[RuntimeEventRejection] = []
    current = _SerializedBatch([], [])
    size = _BATCH_ENVELOPE_BYTES
    for event in events:
        try:
            event_bytes = len(_compact_json(event).encode("utf-8"))
        except (TypeError, ValueError):
            rejected.append(
                RuntimeEventRejection(_safe_event_id(event), "event cannot be serialized")
            )
            continue
        if event_bytes > RUNTIME_EVENT_MAX_BYTES:
            rejected.append(
                RuntimeEventRejection(
                    _safe_event_id(event),
                    f"serialized event exceeds {RUNTIME_EVENT_MAX_BYTES} bytes",
                )
            )
            continue
        addition = event_bytes + (1 if current.events else 0)
        if len(current.events) == RUNTIME_EVENT_BATCH_MAX_EVENTS or (
            current.events and size + addition > RUNTIME_EVENT_BATCH_MAX_BYTES
        ):
            batches.append(current)
            current = _SerializedBatch([], [])
            size = _BATCH_ENVELOPE_BYTES
            addition = event_bytes
        current.events.append(event)
        current.event_ids.append(_safe_event_id(event))
        size += addition
    if current.events:
        batches.append(current)
    return batches, rejected


def _compact_json(value: Any) -> str:
    """Serialize like the transport does. NaN and infinity are rejected."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _safe_event_id(event: Any) -> str | None:
    event_id = event.get("event_id") if isinstance(event, Mapping) else None
    return event_id if isinstance(event_id, str) else None


def _parse_delivery_report(body: Any, batch_size: int) -> tuple[int, list[RuntimeEventRejection]]:
    if not isinstance(body, Mapping) or not isinstance(body.get("rejected"), list):
        return batch_size, []
    rejected = [
        RuntimeEventRejection(
            item["event_id"] if isinstance(item.get("event_id"), str) else None, item["reason"]
        )
        for item in body["rejected"]
        if isinstance(item, Mapping) and isinstance(item.get("reason"), str)
    ]
    reported = _is_number(body.get("accepted")) or _is_number(body.get("duplicates"))
    if reported:
        accepted = _non_negative_count(body.get("accepted")) + _non_negative_count(
            body.get("duplicates")
        )
    else:
        accepted = max(0, batch_size - len(rejected))
    return accepted, rejected


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _non_negative_count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _extend(window: _DropWindow, timestamp: float) -> None:
    window.count += 1
    window.end_ts = timestamp
