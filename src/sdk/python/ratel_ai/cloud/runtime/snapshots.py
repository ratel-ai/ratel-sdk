"""Fail-open publication of complete runtime catalog snapshots to ``PUT /catalog/snapshot``.

The port of ``snapshots.ts``. A snapshot publishes after a debounce. A snapshot
with the hash of the last durable one is not sent again. A durable snapshot
reconciles every 5 minutes. A transient failure stays queued and retries with
a backoff that grows across failing cycles (``Retry-After`` on 429 wins). HTTP
400, 413 and 415 drop the snapshot and report it one time. A ``202 {synced:
false}`` stays pending and retries every 30 seconds. Nothing raises into the
caller.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from ..config import resolve_cloud_config
from ..transport import CloudHttpClient, CloudResponse, CloudTransport
from .delivery_status import DeliveryResult, DeliveryStatus, classify_delivery
from .hash import hash_catalog_snapshot
from .publisher import RejectionHandler, RuntimeEventRejection
from .retry import (
    BackgroundTasks,
    Clock,
    RetryOptions,
    Sleep,
    create_retry_config,
    non_negative,
    parse_retry_after,
    positive_integer,
    request_with_retry,
    start_timer,
)
from .types import RuntimeCatalogSnapshot, RuntimeCatalogToolDefinition

__all__ = [
    "CATALOG_SNAPSHOT_MAX_BYTES",
    "CATALOG_SNAPSHOT_MAX_DESCRIPTION_LENGTH",
    "CATALOG_SNAPSHOT_MAX_ID_OR_NAME_LENGTH",
    "CATALOG_SNAPSHOT_MAX_TOOLS",
    "CatalogSnapshotsPublisher",
    "normalize_source_id",
]

#: The most bytes that one snapshot body has. Tools that do not fit are omitted.
CATALOG_SNAPSHOT_MAX_BYTES = 4_000_000

#: The most tools that one snapshot has. Later tools are omitted.
CATALOG_SNAPSHOT_MAX_TOOLS = 5_000

#: Ids, names and the source id are trimmed and truncated to this length.
CATALOG_SNAPSHOT_MAX_ID_OR_NAME_LENGTH = 512

#: Descriptions are trimmed and truncated to this length.
CATALOG_SNAPSHOT_MAX_DESCRIPTION_LENGTH = 16_384

_DEFAULT_DEBOUNCE_MS = 500.0
_DEFAULT_RECONCILE_INTERVAL_MS = 5 * 60_000
# Cloud caches its ingest-flag decision, so a deferred snapshot retries slowly.
_DEFERRED_RETRY_DELAY_MS = 30_000.0
# Deterministic payload rejects that never succeed on an identical retry.
_TERMINAL_REJECT_STATUSES = frozenset({400, 413, 415})
_ALL_STATUSES = range(100, 600)
_MISSING_KEY: DeliveryResult = {
    "kind": "auth",
    "status": None,
    "message": "no API key: pass api_key or set RATEL_API_KEY",
}


@dataclass(frozen=True)
class _Pending:
    body: dict[str, Any]
    hash: str
    source_id: str


@dataclass(frozen=True)
class _Prepared:
    body: dict[str, Any]
    snapshot: RuntimeCatalogSnapshot
    rejected: list[RuntimeEventRejection]


class CatalogSnapshotsPublisher:
    """Fail-open publication of complete runtime catalog snapshots to Ratel Cloud."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        transport: CloudTransport | None = None,
        debounce_ms: float | None = None,
        reconcile_interval_ms: int | None = None,
        retry: RetryOptions | None = None,
        on_rejected: RejectionHandler | None = None,
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
            debounce_ms: Quiet period after a publish before the send. Default 500 ms.
            reconcile_interval_ms: Maximum age of a durable confirmation before
                the snapshot is sent again. Default 5 minutes.
            retry: How one request retries.
            on_rejected: Called for omitted tools and terminal publication failures.
            sleep: The timer. Defaults to `asyncio.sleep`.
            clock: The clock, in seconds. Defaults to `time.time`.
            delivery_status: The shared health tracker.
        """
        config = resolve_cloud_config(api_key=api_key, base_url=base_url, timeout=timeout)
        self._has_key = bool(config.api_key)
        self._http = CloudHttpClient(config, transport=transport)
        self._debounce_ms = non_negative(debounce_ms, _DEFAULT_DEBOUNCE_MS)
        self._reconcile_interval_ms = positive_integer(
            reconcile_interval_ms, _DEFAULT_RECONCILE_INTERVAL_MS
        )
        self._retry = create_retry_config(retry)
        self._sleep: Sleep = sleep if sleep is not None else asyncio.sleep
        self._clock: Clock = clock if clock is not None else time.time
        self._on_rejected = on_rejected
        self._status = (
            delivery_status
            if delivery_status is not None
            else DeliveryStatus(warn_on_failure=False, clock=self._clock)
        )
        self._acknowledged: dict[str, str] = {}
        self._reconcile_at: dict[str, float] = {}
        self._in_flight: dict[str, str] = {}
        self._latest: dict[str, _Pending] = {}
        self._pending: dict[str, _Pending] = {}
        self._tasks = BackgroundTasks()
        self._flush_timer: asyncio.Task[None] | None = None
        self._reconcile_timer: asyncio.Task[None] | None = None
        self._lock: asyncio.Lock | None = None
        self._closed = False
        self._failure_streak = 0

    def publish(self, snapshot: RuntimeCatalogSnapshot) -> None:
        """Queue the latest full snapshot of its source. It never raises.

        Args:
            snapshot: The complete snapshot. It replaces the source's catalog in Cloud.
        """
        if self._closed:
            return
        try:
            prepared = _prepare_snapshot(snapshot)
            source_id = prepared.snapshot["source_id"]
            if prepared.rejected:
                self._status.record_snapshot_pending(source_id)
                self._status.record_snapshot(
                    source_id,
                    {
                        "kind": "rejected_payload",
                        "status": None,
                        "message": f"{len(prepared.rejected)} catalog tools were rejected",
                    },
                )
            self._surface(prepared.rejected)
            digest = hash_catalog_snapshot(prepared.snapshot)
            latest = _Pending(prepared.body, digest, source_id)
            self._latest[source_id] = latest
            if source_id not in self._reconcile_at:
                self._reconcile_at[source_id] = self._now_ms() + self._reconcile_interval_ms
                self._reschedule_reconcile()
            in_flight = self._in_flight.get(source_id)
            is_durable = digest == self._acknowledged.get(source_id)
            if digest == in_flight or (is_durable and in_flight is None):
                self._pending.pop(source_id, None)
                if not self._pending:
                    self._cancel_flush_timer()
                if is_durable and in_flight is None:
                    self._status.record_snapshot_settled(source_id)
                return
            self._status.record_snapshot_pending(source_id)
            self._pending[source_id] = latest
            self._schedule_flush(self._debounce_ms)
        except Exception as error:
            source_id = _safe_source_id(snapshot)
            self._status.record_snapshot_pending(source_id)
            self._status.record_snapshot(
                source_id,
                {
                    "kind": "rejected_payload",
                    "status": None,
                    "message": f"catalog snapshot cannot be prepared: {_message(error)}",
                },
            )
            self._surface(
                [
                    RuntimeEventRejection(
                        None,
                        f"catalog snapshot for {source_id} cannot be prepared: {_message(error)}",
                    )
                ]
            )

    async def flush(self) -> None:
        """Send every pending snapshot now. It never raises."""
        self._cancel_flush_timer()
        if self._lock is None:
            # Created on first use, so it binds to the running loop (Python 3.9).
            self._lock = asyncio.Lock()
        async with self._lock:
            try:
                await self._drain_pending()
            except Exception:
                pass

    async def close(self) -> None:
        """Stop accepting snapshots, send the pending ones, stop the timers."""
        self._closed = True
        self._cancel_flush_timer()
        self._cancel_reconcile_timer()
        await self.flush()
        await self._tasks.wait()

    async def _drain_pending(self) -> None:
        retry_pending: dict[str, _Pending] = {}
        attempted = False
        retry_with_backoff = False
        deferred = False
        retry_after_ms: float | None = None
        while self._pending:
            self._cancel_flush_timer()
            pending_snapshots = list(self._pending.values())
            self._pending.clear()
            for pending in pending_snapshots:
                source_id = pending.source_id
                attempted = True
                retry_pending.pop(source_id, None)
                self._in_flight[source_id] = pending.hash
                try:
                    response, result = await self._send(pending.body)
                    if response is None:
                        self._status.record_snapshot(source_id, result)
                        reason = (
                            "was not sent: no API key"
                            if result is _MISSING_KEY
                            else "failed after retries"
                        )
                        self._surface(
                            [
                                RuntimeEventRejection(
                                    None, f"catalog snapshot for {source_id} {reason}"
                                )
                            ]
                        )
                        self._restore_latest(retry_pending, pending)
                        retry_with_backoff = True
                        continue
                    outcome = _publish_outcome(response)
                    if outcome == "durable":
                        latest = self._latest.get(source_id)
                        settled = (
                            latest is not None
                            and latest.hash == pending.hash
                            and source_id not in self._pending
                        )
                        self._status.record_snapshot_durable(source_id, result, settled=settled)
                        self._acknowledged[source_id] = pending.hash
                        self._reconcile_at[source_id] = self._now_ms() + self._reconcile_interval_ms
                        self._reschedule_reconcile()
                    elif outcome == "deferred":
                        self._status.record_snapshot(source_id, result)
                        self._restore_latest(retry_pending, pending)
                        deferred = True
                    else:
                        self._status.record_snapshot(source_id, result)
                        self._surface(
                            [
                                RuntimeEventRejection(
                                    None,
                                    f"catalog snapshot for {source_id} rejected with HTTP "
                                    f"{response.status}",
                                )
                            ]
                        )
                        if response.status in _TERMINAL_REJECT_STATUSES:
                            self._drop_latest(pending)
                            continue
                        self._restore_latest(retry_pending, pending)
                        retry_with_backoff = True
                        if response.status == 429:
                            parsed = parse_retry_after(
                                response.headers.get("retry-after"), self._now_ms()
                            )
                            retry_after_ms = parsed if parsed is not None else retry_after_ms
                except Exception as error:
                    self._status.record_snapshot(source_id, classify_delivery(None))
                    self._surface(
                        [
                            RuntimeEventRejection(
                                None, f"catalog snapshot for {source_id} failed: {_message(error)}"
                            )
                        ]
                    )
                    self._restore_latest(retry_pending, pending)
                    retry_with_backoff = True
                finally:
                    if self._in_flight.get(source_id) == pending.hash:
                        del self._in_flight[source_id]
        if attempted:
            self._failure_streak = self._failure_streak + 1 if retry_with_backoff else 0
        for source_id, pending in retry_pending.items():
            self._pending.setdefault(source_id, pending)
        if self._pending:
            self._schedule_flush(self._retry_delay_ms(retry_with_backoff, deferred, retry_after_ms))

    def _retry_delay_ms(
        self, retry_with_backoff: bool, deferred: bool, retry_after_ms: float | None
    ) -> float:
        """A deferred or failing snapshot never reschedules at the churn debounce."""
        if retry_with_backoff:
            return retry_after_ms if retry_after_ms is not None else self._failure_backoff_ms()
        if deferred:
            return max(_DEFERRED_RETRY_DELAY_MS, self._debounce_ms)
        return self._debounce_ms

    def _failure_backoff_ms(self) -> float:
        """Continue the in-request exponential curve across cycles, capped at max_backoff_ms."""
        base = min(
            self._retry.initial_backoff_ms * 2.0 ** (self._retry.max_attempts - 1),
            self._retry.max_backoff_ms,
        )
        growth = 2.0 ** min(max(self._failure_streak - 1, 0), 30)
        return max(self._debounce_ms, min(base * growth, self._retry.max_backoff_ms))

    def _drop_latest(self, rejected: _Pending) -> None:
        latest = self._latest.get(rejected.source_id)
        if latest is None or latest.hash != rejected.hash:
            return
        del self._latest[rejected.source_id]
        self._reconcile_at.pop(rejected.source_id, None)

    def _restore_latest(self, retry_pending: dict[str, _Pending], failed: _Pending) -> None:
        if failed.source_id in self._pending:
            return
        latest = self._latest.get(failed.source_id)
        if latest is not None and latest.hash == failed.hash:
            retry_pending[failed.source_id] = latest

    async def _send(self, body: dict[str, Any]) -> tuple[CloudResponse | None, DeliveryResult]:
        if not self._has_key:
            # Fail-open: the request cannot authenticate, so report it as auth.
            return None, _MISSING_KEY

        async def send() -> CloudResponse:
            return await self._http.request(
                "PUT", "/catalog/snapshot", body=body, accept_statuses=_ALL_STATUSES
            )

        response = await request_with_retry(send, self._retry, self._sleep, self._clock)
        return response, classify_delivery(response)

    def _schedule_flush(self, delay_ms: float) -> None:
        if self._closed:
            return
        self._cancel_flush_timer()
        self._flush_timer = start_timer(delay_ms, self._on_flush_timer, self._sleep, self._tasks)

    def _on_flush_timer(self) -> None:
        self._flush_timer = None
        self._tasks.spawn(self.flush())

    def _schedule_reconcile(self) -> None:
        if self._closed or self._reconcile_timer is not None or not self._reconcile_at:
            return
        delay_ms = max(0.0, min(self._reconcile_at.values()) - self._now_ms())
        self._reconcile_timer = start_timer(delay_ms, self._reconcile, self._sleep, self._tasks)

    def _reconcile(self) -> None:
        self._reconcile_timer = None
        if self._closed:
            return
        now = self._now_ms()
        for source_id, latest in self._latest.items():
            if self._reconcile_at.get(source_id, float("inf")) > now:
                continue
            self._reconcile_at[source_id] = now + self._reconcile_interval_ms
            if self._in_flight.get(source_id) == latest.hash or source_id in self._pending:
                continue
            self._pending[source_id] = latest
        if self._pending:
            self._tasks.spawn(self.flush())
        self._schedule_reconcile()

    def _reschedule_reconcile(self) -> None:
        self._cancel_reconcile_timer()
        self._schedule_reconcile()

    def _cancel_flush_timer(self) -> None:
        if self._flush_timer is not None:
            self._flush_timer.cancel()
            self._flush_timer = None

    def _cancel_reconcile_timer(self) -> None:
        if self._reconcile_timer is not None:
            self._reconcile_timer.cancel()
            self._reconcile_timer = None

    def _surface(self, rejected: Sequence[RuntimeEventRejection]) -> None:
        if not rejected or self._on_rejected is None:
            return
        with contextlib.suppress(Exception):
            # User-provided observability callbacks remain fail-open.
            self._on_rejected(list(rejected))

    def _now_ms(self) -> float:
        return self._clock() * 1_000


def normalize_source_id(value: str) -> str:
    """Trim and truncate a source id exactly like the snapshot payload does.

    Args:
        value: The raw source id.

    Returns:
        The normalized source id.
    """
    return _normalize_text(value, CATALOG_SNAPSHOT_MAX_ID_OR_NAME_LENGTH)


def _prepare_snapshot(snapshot: RuntimeCatalogSnapshot) -> _Prepared:
    rejected: list[RuntimeEventRejection] = []
    source_id = normalize_source_id(snapshot["source_id"])
    envelope = _compact_json({"source_id": source_id, "tools": []})
    size = len(envelope.encode("utf-8"))
    tools: list[RuntimeCatalogToolDefinition] = []
    request_tools: list[dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in snapshot["tools"]:
        if len(tools) == CATALOG_SNAPSHOT_MAX_TOOLS:
            rejected.append(
                RuntimeEventRejection(
                    _safe_tool_id(candidate),
                    f"catalog snapshot tool limit is {CATALOG_SNAPSHOT_MAX_TOOLS}",
                )
            )
            continue
        try:
            tool = _normalize_tool(candidate)
            if tool["name"] == "":
                rejected.append(
                    RuntimeEventRejection(
                        _safe_tool_id(candidate), "catalog snapshot tool name is empty"
                    )
                )
                continue
            if tool["id"] in seen:
                rejected.append(
                    RuntimeEventRejection(
                        _safe_tool_id(candidate),
                        f"catalog snapshot tool id is duplicated after normalization: {tool['id']}",
                    )
                )
                continue
            request_tool = _to_tool_request(tool)
            serialized = _compact_json(request_tool)
        except Exception:
            rejected.append(
                RuntimeEventRejection(
                    _safe_tool_id(candidate), "catalog snapshot tool cannot be serialized"
                )
            )
            continue
        addition = len(serialized.encode("utf-8")) + (1 if tools else 0)
        if size + addition > CATALOG_SNAPSHOT_MAX_BYTES:
            rejected.append(
                RuntimeEventRejection(
                    _safe_tool_id(candidate),
                    f"catalog snapshot tool cannot fit within {CATALOG_SNAPSHOT_MAX_BYTES} bytes",
                )
            )
            continue
        tools.append(tool)
        request_tools.append(request_tool)
        seen.add(tool["id"])
        size += addition
    return _Prepared(
        body={"source_id": source_id, "tools": request_tools},
        snapshot={"source_id": source_id, "tools": tools},
        rejected=rejected,
    )


def _normalize_tool(tool: Mapping[str, Any]) -> RuntimeCatalogToolDefinition:
    name = _normalize_text(tool["name"], CATALOG_SNAPSHOT_MAX_ID_OR_NAME_LENGTH)
    searchable = _normalize_text(
        tool.get("experimental_searchable_description") or "",
        CATALOG_SNAPSHOT_MAX_DESCRIPTION_LENGTH,
    )
    normalized: RuntimeCatalogToolDefinition = {
        "id": _normalize_text(tool["id"], CATALOG_SNAPSHOT_MAX_ID_OR_NAME_LENGTH) or name,
        "name": name,
        "description": _normalize_text(
            tool.get("description") or "", CATALOG_SNAPSHOT_MAX_DESCRIPTION_LENGTH
        ),
        "input_schema": tool.get("input_schema"),
        "output_schema": tool.get("output_schema"),
        "metadata": tool.get("metadata"),
    }
    # Dropped when blank after trimming, so a publisher that never set one
    # sends a byte-identical body and keeps its catalog version.
    if searchable:
        normalized["experimental_searchable_description"] = searchable
    return normalized


def _to_tool_request(tool: RuntimeCatalogToolDefinition) -> dict[str, Any]:
    request: dict[str, Any] = {
        "id": tool["id"],
        "name": tool["name"],
        "description": tool.get("description") or "",
    }
    searchable = tool.get("experimental_searchable_description")
    if searchable is not None:
        request["searchable_description"] = searchable
    request["input_schema"] = tool.get("input_schema")
    request["output_schema"] = tool.get("output_schema")
    request["metadata"] = tool.get("metadata")
    return request


def _normalize_text(value: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"expected a string, got {type(value).__name__}")
    return value.strip()[:max_length]


def _compact_json(value: Any) -> str:
    """Serialize like the transport does. NaN and infinity are rejected."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _publish_outcome(response: CloudResponse) -> Literal["durable", "deferred", "failed"]:
    body = response.json
    if isinstance(body, Mapping) and body.get("synced") is False:
        return "deferred"
    return "durable" if response.status == 200 else "failed"


def _safe_tool_id(tool: Any) -> str | None:
    tool_id = tool.get("id") if isinstance(tool, Mapping) else None
    return tool_id if isinstance(tool_id, str) else None


def _safe_source_id(snapshot: Any) -> str:
    source_id = snapshot.get("source_id") if isinstance(snapshot, Mapping) else None
    return source_id if isinstance(source_id, str) else "unknown source"


def _message(error: BaseException) -> str:
    return str(error) or type(error).__name__
