"""`attach_intent_graph_sync`: the courier between an `IntentGraph` and Ratel Platform.

The port of ``intent-graph-sync.ts``. It moves an app's in-memory
`ratel_ai.IntentGraph` (ADR-0014, adaptive usage ranking) to and from the
per-project, per-source-id graph storage of Ratel Platform. It never learns,
clusters, ranks or edits the graph, and it never calls
``experimental_enable_adaptive_ranking`` or ``experimental_disable_adaptive_ranking``:
that stays the job of the caller.

Two modes:

- ``"push"`` (default): load the stored graph, then save local usage back behind
  a debounce and a maximum wait. A 409 loads the newer graph and calls
  ``on_replaced``.
- ``"consume"``: read a graph that Platform owns and poll for newer revisions.
  It never sends a ``PUT``.

The graph JSON is opaque: it is embedded byte for byte and never logged, because
its cluster members are raw user queries. Every failure is fail-open.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
import random as _random
import time
import warnings
import weakref
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Callable, Literal, Protocol, Union
from urllib.parse import quote

from ..._native import IntentGraph
from ..config import platform_switch_on, resolve_platform_config
from ..transport import PlatformTransport, TransportRequest, TransportResponse, UrllibTransport
from .attach import RatelRuntimeEvents, RuntimeEventSubscription
from .retry import BackgroundTasks, Clock, Sleep, non_negative, parse_retry_after, start_timer
from .snapshots import normalize_source_id
from .types import RuntimeEvent

__all__ = [
    "INTENT_GRAPH_ENV",
    "IntentGraphSync",
    "IntentGraphSyncCatalog",
    "IntentGraphSyncError",
    "IntentGraphSyncErrorKind",
    "IntentGraphSyncMode",
    "IntentGraphSyncStatus",
    "attach_intent_graph_sync",
]

#: Env variable that turns the sync off when it is ``off`` (any case, trimmed).
INTENT_GRAPH_ENV = "RATEL_PLATFORM_INTENT_GRAPH"

#: ``"push"`` syncs the runtime's own graph to Platform; ``"consume"`` reads a graph
#: that Platform owns and never writes.
IntentGraphSyncMode = Literal["push", "consume"]

#: The state of the sync, as `IntentGraphSync.status` returns it.
IntentGraphSyncStatus = Literal["idle", "syncing", "conflict", "disabled", "error"]

#: The kind of an `IntentGraphSyncError`.
IntentGraphSyncErrorKind = Literal[
    "network", "auth", "rate_limited", "invalid_graph", "feature_disabled"
]

_DEFAULT_DEBOUNCE_MS = 15_000.0
_DEFAULT_MAX_WAIT_MS = 60_000.0
_INITIAL_BACKOFF_MS = 1_000.0
_MAX_BACKOFF_MS = 30_000.0
_DEFAULT_POLL_INTERVAL_MS = 300_000.0
_MIN_POLL_INTERVAL_MS = 15_000.0
_DEFAULT_TIMEOUT = 30.0
_QUALIFYING_EVENT_TYPES = frozenset({"invoke_start", "skill_invoke"})
_LOG_PREFIX = "[ratel-platform/runtime]"
# Same safe set as `encodeURIComponent`.
_URI_COMPONENT_SAFE = "-_.!~*'()"


@dataclass(frozen=True)
class IntentGraphSyncError:
    """One sync failure, as ``on_error`` receives it. It never carries graph content.

    Attributes:
        kind: The failure category.
        status: The HTTP status, or `None` when no status applies.
        message: A human-readable reason.
        rev: The rejected ``rev``, for ``invalid_graph`` only.
    """

    kind: IntentGraphSyncErrorKind
    status: int | None
    message: str
    rev: int | None = None


class IntentGraphSyncCatalog(Protocol):
    """The one thing the sync needs from a runtime: its event stream, like `ratel_ai.Ratel`."""

    @property
    def events(self) -> RatelRuntimeEvents:
        """The event stream."""
        ...


#: Called with the newer graph that replaced the in-memory one.
ReplacedHandler = Callable[[IntentGraph], None]

#: Called with each reported failure.
ErrorHandler = Callable[[IntentGraphSyncError], None]


@dataclass(frozen=True)
class _GetOk:
    graph: IntentGraph
    etag: str | None


@dataclass(frozen=True)
class _RateLimited:
    retry_after_ms: float | None


@dataclass(frozen=True)
class _PutOk:
    etag: str | None


_GetFailureKind = Literal["not_modified", "not_found", "feature_disabled", "auth", "network"]
_GetOutcome = Union[_GetOk, _RateLimited, _GetFailureKind]
_PutFailureKind = Literal[
    "conflict", "invalid_graph", "rejected", "feature_disabled", "auth", "network"
]
_PutOutcome = Union[_PutOk, _RateLimited, _PutFailureKind]

_ATTACHED: weakref.WeakSet[Any] = weakref.WeakSet()
_ATTACHED_IDS: set[int] = set()


async def attach_intent_graph_sync(
    catalog: IntentGraphSyncCatalog,
    *,
    source_id: str | None = None,
    mode: IntentGraphSyncMode = "push",
    graph_key: str | None = None,
    poll_interval_ms: float | None = None,
    endpoint: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    debounce_ms: float | None = None,
    max_wait_ms: float | None = None,
    on_replaced: ReplacedHandler | None = None,
    on_error: ErrorHandler | None = None,
    transport: PlatformTransport | None = None,
    timeout: float | None = None,
    random: Callable[[], float] | None = None,
    sleep: Sleep | None = None,
    clock: Clock | None = None,
) -> IntentGraphSync:
    """Load the intent graph of one source from Ratel Platform and keep it in sync.

    Call it from a running event loop. It returns after the first load attempt;
    a failed load retries in the background and never raises. Pass the returned
    ``graph`` to ``experimental_enable_adaptive_ranking`` yourself, and re-arm the
    ranking in ``on_replaced``.

    Args:
        catalog: The runtime whose ``events`` stream reports usage, e.g. `ratel_ai.ratel()`.
        source_id: Stable deployment source. Defaults to ``catalog.events.source_id``.
        mode: ``"push"`` (default) or ``"consume"``.
        graph_key: Consume mode only: which stored graph to fetch. Defaults to ``source_id``.
        poll_interval_ms: Consume mode only: how often to poll with a conditional
            ``GET``. Default 300000 ms (5 minutes), minimum 15000 ms.
        endpoint: Full intent graph URL. Defaults to ``{base_url}/intent-graph``.
        base_url: API root, including ``/api/v1``. Defaults to ``RATEL_BASE_URL``,
            then production.
        api_key: Project API key. Defaults to ``RATEL_API_KEY``.
        debounce_ms: Quiet period after a qualifying invoke before a save. Default 15000 ms.
        max_wait_ms: Upper bound of how long a pending change waits under continuous
            activity. Default 60000 ms. ``0`` saves on the next tick.
        on_replaced: Called with the newer graph after a 409 in push mode, or after
            each adopted poll (but the first load) in consume mode.
        on_error: Called with each reported failure. It never receives graph content.
        transport: The HTTP transport. Defaults to `UrllibTransport`.
        timeout: Per-request timeout in seconds. Default 30.
        random: The source of the backoff jitter, in [0, 1]. Defaults to `random.random`.
        sleep: The timer, in seconds. Defaults to `asyncio.sleep`.
        clock: The clock, in seconds since the epoch. Defaults to `time.time`.

    Returns:
        The `IntentGraphSync` handle.

    Raises:
        RuntimeError: The sync was already attached to this ``catalog``.
        ValueError: ``graph_key`` or ``poll_interval_ms`` was passed in push mode.
    """
    _claim(catalog)
    # Like TypeScript, this check runs after the claim: fix the options and attach
    # a fresh catalog, or the retry itself raises "already attached".
    if mode == "push" and (graph_key is not None or poll_interval_ms is not None):
        raise ValueError(
            f'{_LOG_PREFIX} "graph_key"/"poll_interval_ms" are consume-mode only options '
            '(mode="consume")'
        )
    if not platform_switch_on("intent_graph"):
        return IntentGraphSync._disabled()

    resolved_source = normalize_source_id(
        source_id if source_id is not None else catalog.events.source_id
    )
    config = resolve_platform_config(api_key=api_key, base_url=base_url, timeout=timeout)
    sync = IntentGraphSync(
        _Settings(
            mode=mode,
            source_id=resolved_source,
            graph_key=(
                normalize_source_id(graph_key)
                if mode == "consume" and graph_key is not None
                else resolved_source
            ),
            poll_interval_ms=max(
                non_negative(poll_interval_ms, _DEFAULT_POLL_INTERVAL_MS), _MIN_POLL_INTERVAL_MS
            ),
            endpoint=endpoint if endpoint is not None else f"{config.base_url}/intent-graph",
            api_key=config.api_key or "",
            debounce_ms=non_negative(debounce_ms, _DEFAULT_DEBOUNCE_MS),
            max_wait_ms=non_negative(max_wait_ms, _DEFAULT_MAX_WAIT_MS),
            timeout=non_negative(timeout, _DEFAULT_TIMEOUT),
            on_replaced=on_replaced,
            on_error=on_error,
            transport=transport if transport is not None else UrllibTransport(),
            random=random if random is not None else _random.random,
            sleep=sleep if sleep is not None else asyncio.sleep,
            clock=clock if clock is not None else time.time,
        )
    )
    await sync._start(catalog.events)
    return sync


class IntentGraphSync:
    """The handle of one intent graph sync. `attach_intent_graph_sync` returns it."""

    def __init__(self, settings: _Settings | None) -> None:
        """Create the handle. Use `attach_intent_graph_sync` instead."""
        self._s = settings
        self._graph = IntentGraph()
        self._status: IntentGraphSyncStatus = "syncing" if settings is not None else "disabled"
        self._etag: str | None = None
        self._saved_rev = -1
        # One-way ratchet: true once a baseline (200, or 404 not_found) exists. No PUT
        # runs before it, so local usage never races past a stored graph it never saw.
        self._loaded = False
        self._skipped_until_rev: int | None = None
        self._backoff_ms = _INITIAL_BACKOFF_MS
        self._tasks = BackgroundTasks()
        self._next_attempt_timer: asyncio.Task[None] | None = None
        self._max_wait_timer: asyncio.Task[None] | None = None
        self._awaiting_backoff = False
        self._dirty = False
        self._in_flight: asyncio.Task[None] | None = None
        self._closed = False
        self._closing: asyncio.Future[None] | None = None
        self._no_handler_warned = False
        self._error_reported = False
        # False only during the attempt that `attach_intent_graph_sync` awaits: a load
        # then replaces nothing, because the app holds no graph yet.
        self._first_attempt_done = False
        # One-way ratchet: a revoked key never becomes valid by waiting.
        self._auth_failed = False
        self._subscription: RuntimeEventSubscription | None = None

    @property
    def graph(self) -> IntentGraph:
        """The current graph. It changes after a 409 or an adopted poll."""
        return self._graph

    @property
    def status(self) -> IntentGraphSyncStatus:
        """``"idle"``, ``"syncing"``, ``"conflict"``, ``"disabled"`` or ``"error"``."""
        return self._status

    async def flush(self) -> None:
        """Save now if ``graph.rev`` moved (push), or poll now (consume).

        In push mode it skips a pending debounce or backoff wait. In consume
        mode it respects an active backoff wait.
        """
        if self._s is None or self._status == "disabled" or self._auth_failed:
            return
        if self._in_flight is not None:
            await _settled(self._in_flight)
            return
        if self._s.mode == "consume":
            if self._awaiting_backoff:
                return
            await _settled(self._trigger_consume_attempt())
            return
        if self._loaded and not self._has_unsaved_change() and not self._dirty:
            return
        await _settled(self._trigger_attempt())

    async def close(self) -> None:
        """Flush one time (push), unsubscribe and stop every timer and task. Idempotent."""
        if self._closing is None:
            self._closing = asyncio.ensure_future(self._close_once())
        await asyncio.shield(self._closing)

    @classmethod
    def _disabled(cls) -> IntentGraphSync:
        return cls(None)

    # — lifecycle ——————————————————————————————————————————————————————————————

    async def _start(self, events: RatelRuntimeEvents) -> None:
        s = self._require()
        if s.mode == "push":
            self._subscription = events.subscribe(self._on_events)
            await _settled(self._trigger_attempt())
        else:
            await _settled(self._trigger_consume_attempt())
        self._first_attempt_done = True

    async def _close_once(self) -> None:
        s = self._s
        if s is not None and s.mode == "push":
            with contextlib.suppress(Exception):
                # The sync lifecycle is fail-open too.
                await self.flush()
        self._closed = True
        self._clear_next_attempt_timer()
        self._clear_max_wait_timer()
        self._unsubscribe()
        await self._tasks.wait()

    def _require(self) -> _Settings:
        assert self._s is not None
        return self._s

    # — push ———————————————————————————————————————————————————————————————————

    async def _on_events(self, batch: list[RuntimeEvent]) -> None:
        if self._closed or self._status == "disabled" or self._auth_failed or not self._loaded:
            return
        qualifies = any(event.get("type") in _QUALIFYING_EVENT_TYPES for event in batch)
        if not qualifies or not self._has_unsaved_change():
            return
        self._dirty = True
        if self._in_flight is not None:
            return  # The completion of the in-flight attempt schedules the next one.
        if self._awaiting_backoff:
            return  # A retry is scheduled already; it reads rev when it fires.
        self._schedule_attempt(self._require().debounce_ms)
        self._arm_max_wait_if_needed()

    def _trigger_attempt(self) -> asyncio.Task[None] | None:
        if self._closed or self._status == "disabled" or self._auth_failed:
            return None
        if self._in_flight is not None:
            self._dirty = True
            return self._in_flight
        self._clear_next_attempt_timer()
        self._clear_max_wait_timer()
        self._awaiting_backoff = False
        self._in_flight = self._tasks.spawn(self._run_push_attempt())
        return self._in_flight

    async def _run_push_attempt(self) -> None:
        try:
            if self._loaded:
                await self._run_attempt_once()
            else:
                await self._run_load_retry_once()
        except Exception:
            self._status = "error"
        finally:
            self._in_flight = None
        # Do not replace a backoff or Retry-After timer that the failure path armed:
        # that retry reads graph.rev when it fires and picks up a mid-flight batch.
        if (
            self._loaded
            and self._dirty
            and not self._closed
            and self._status != "disabled"
            and not self._awaiting_backoff
        ):
            self._schedule_attempt(self._require().debounce_ms)
            self._arm_max_wait_if_needed()

    async def _run_load_retry_once(self) -> None:
        self._status = "syncing"
        outcome = await self._get(None)
        if isinstance(outcome, _GetOk):
            if self._first_attempt_done:
                # A retry after a failure: the app holds `graph` and may have changed
                # it, so this is a real replacement.
                self._adopt(outcome)
            else:
                self._graph = outcome.graph
                self._etag = outcome.etag
                self._saved_rev = outcome.graph.rev
                self._loaded = True
                self._status = "idle"
            return
        if outcome == "not_found":
            self._loaded = True
            # What the graph learned while Platform was unreachable is the first version
            # to persist: nothing is stored to conflict with.
            self._saved_rev = self._graph.rev
            self._recovered()
            return
        self._handle_get_failure(outcome, "load the intent graph from Ratel Platform")

    async def _run_attempt_once(self) -> None:
        self._dirty = False
        self._status = "syncing"
        rev_at_send = self._graph.rev
        outcome = await self._put(self._graph.to_json(), self._etag)
        await self._handle_put_outcome(outcome, rev_at_send)

    async def _handle_put_outcome(self, outcome: _PutOutcome, rev_at_send: int) -> None:
        if isinstance(outcome, _PutOk):
            self._etag = outcome.etag if outcome.etag is not None else self._etag
            self._saved_rev = rev_at_send
            self._recovered()
        elif isinstance(outcome, _RateLimited):
            self._status = "error"
            self._report_once(
                "rate_limited", 429, "Ratel Platform is rate limiting intent graph sync"
            )
            self._schedule_retry_on_failure(outcome.retry_after_ms)
        elif outcome == "conflict":
            self._status = "conflict"
            await self._handle_conflict()
        elif outcome in ("invalid_graph", "rejected"):
            self._skipped_until_rev = rev_at_send
            self._status = "idle"
            self._report(
                IntentGraphSyncError(
                    kind="invalid_graph",
                    status=400 if outcome == "invalid_graph" else None,
                    message=(
                        "Ratel Platform rejected the graph as invalid"
                        if outcome == "invalid_graph"
                        else "Ratel Platform rejected the graph payload (size or content type)"
                    ),
                    rev=rev_at_send,
                )
            )
        elif outcome == "feature_disabled":
            self._go_terminal_disabled()
        elif outcome == "auth":
            self._status = "error"
            self._auth_failed = True
            # No retry: waiting never fixes a revoked key.
            self._report_once(
                "auth", 401, "Ratel Platform rejected the API key — intent graph sync has stopped"
            )
        else:
            self._status = "error"
            self._report_once("network", None, "Ratel Platform intent graph sync failed")
            self._schedule_retry_on_failure(None)

    async def _handle_conflict(self) -> None:
        outcome = await self._get(None)
        if isinstance(outcome, _GetOk):
            self._adopt(outcome)
            return
        if outcome == "not_found":
            # Platform just rejected the PUT as stale but now stores nothing: an
            # inconsistent answer, so retry it as transient.
            self._status = "error"
            self._report_once("network", None, "failed to fetch the newer graph after a conflict")
            self._schedule_retry_on_failure(None)
            return
        self._handle_get_failure(outcome, "fetch the newer graph after a conflict")

    def _has_unsaved_change(self) -> bool:
        rev = self._graph.rev
        if rev == self._saved_rev:
            return False
        return self._skipped_until_rev is None or rev > self._skipped_until_rev

    def _arm_max_wait_if_needed(self) -> None:
        """Arm one time per pending episode, so new batches never push it further out."""
        if self._max_wait_timer is not None:
            return
        self._max_wait_timer = start_timer(
            self._require().max_wait_ms, self._on_max_wait, self._require().sleep, self._tasks
        )

    def _on_max_wait(self) -> None:
        self._max_wait_timer = None
        # The guards of `_on_events`: never fire into a terminal state, and never
        # preempt an in-flight attempt or a scheduled backoff retry.
        if self._closed or self._status == "disabled" or self._auth_failed or not self._loaded:
            return
        if self._in_flight is not None or self._awaiting_backoff:
            return
        self._trigger_attempt()

    # — consume ————————————————————————————————————————————————————————————————

    def _trigger_consume_attempt(self) -> asyncio.Task[None] | None:
        if self._closed or self._status == "disabled" or self._auth_failed:
            return None
        if self._in_flight is not None:
            return self._in_flight
        self._clear_next_attempt_timer()
        self._awaiting_backoff = False
        self._in_flight = self._tasks.spawn(self._run_consume_attempt())
        return self._in_flight

    async def _run_consume_attempt(self) -> None:
        try:
            await self._consume_once()
        except Exception:
            self._status = "error"
        finally:
            self._in_flight = None

    async def _consume_once(self) -> None:
        s = self._require()
        self._status = "syncing"
        outcome = await self._get(self._etag)
        if isinstance(outcome, _GetOk):
            if self._first_attempt_done:
                self._adopt(outcome)
            else:
                self._graph = outcome.graph
                self._etag = outcome.etag
                self._loaded = True
                self._recovered()
            self._schedule_attempt(s.poll_interval_ms)
        elif isinstance(outcome, _RateLimited):
            self._status = "error"
            self._report_once(
                "rate_limited", 429, "Ratel Platform is rate limiting intent graph sync"
            )
            self._awaiting_backoff = True
            self._schedule_attempt(max(outcome.retry_after_ms or 0.0, s.poll_interval_ms))
        elif outcome in ("not_modified", "not_found"):
            # not_found: Platform has built no graph under this key yet. A normal state.
            if outcome == "not_found" and not self._loaded:
                self._graph = IntentGraph()
                self._loaded = True
            self._recovered()
            self._schedule_attempt(s.poll_interval_ms)
        elif outcome == "feature_disabled":
            self._go_terminal_disabled()
        elif outcome == "auth":
            self._status = "error"
            self._auth_failed = True
            # No re-arm: waiting never fixes a revoked key.
            self._report_once(
                "auth", 401, "Ratel Platform rejected the API key — intent graph sync has stopped"
            )
        else:
            self._status = "error"
            self._report_once(
                "network", None, "failed to poll the intent graph from Ratel Platform"
            )
            self._awaiting_backoff = True
            self._schedule_attempt(max(self._next_backoff_delay(), _MIN_POLL_INTERVAL_MS))

    # — shared state transitions ———————————————————————————————————————————————

    def _adopt(self, outcome: _GetOk) -> None:
        """Adopt a graph that Platform holds: after a 409, or a late load that found one."""
        self._graph = outcome.graph
        self._etag = outcome.etag
        self._saved_rev = outcome.graph.rev
        self._loaded = True
        on_replaced = self._require().on_replaced
        if on_replaced is not None:
            with contextlib.suppress(Exception):
                # User callbacks stay fail-open.
                on_replaced(outcome.graph)
            self._no_handler_warned = False
        elif not self._no_handler_warned:
            self._no_handler_warned = True
            _warn(
                f"{_LOG_PREFIX} intent_graph_conflict: no on_replaced handler was provided — "
                f"the in-memory graph for source {json.dumps(self._require().source_id)} "
                "is now behind Ratel Platform"
            )
        self._recovered()

    def _recovered(self) -> None:
        self._status = "idle"
        self._backoff_ms = _INITIAL_BACKOFF_MS
        self._error_reported = False

    def _handle_get_failure(self, outcome: _GetOutcome, action: str) -> None:
        """Handle a failed GET. ``not_found`` means a different thing at each call site."""
        if outcome == "feature_disabled":
            self._go_terminal_disabled()
            return
        self._status = "error"
        if outcome == "auth":
            self._auth_failed = True
            self._report_once(
                "auth", 401, f"Ratel Platform rejected the API key while trying to {action}"
            )
            return
        if isinstance(outcome, _RateLimited):
            self._report_once("rate_limited", 429, f"failed to {action}")
            self._schedule_retry_on_failure(outcome.retry_after_ms)
            return
        self._report_once("network", None, f"failed to {action}")
        self._schedule_retry_on_failure(None)

    def _go_terminal_disabled(self) -> None:
        """Platform turned intent graph sync off for this project. Waiting never recovers it."""
        self._status = "disabled"
        _warn(
            f"{_LOG_PREFIX} intent_graph_disabled: Ratel Platform has intent graph sync disabled "
            "for this project — sync is inactive for source "
            f"{json.dumps(self._require().source_id)}"
        )
        self._unsubscribe()

    def _unsubscribe(self) -> None:
        if self._subscription is None:
            return
        with contextlib.suppress(Exception):
            # Detach failures cannot escape into host shutdown.
            self._subscription.unsubscribe()
        self._subscription = None

    # — timers and backoff —————————————————————————————————————————————————————

    def _schedule_attempt(self, delay_ms: float) -> None:
        if self._closed:
            return
        self._clear_next_attempt_timer()
        self._next_attempt_timer = start_timer(
            delay_ms, self._on_attempt_timer, self._require().sleep, self._tasks
        )

    def _on_attempt_timer(self) -> None:
        self._next_attempt_timer = None
        if self._require().mode == "push":
            self._trigger_attempt()
        else:
            self._trigger_consume_attempt()

    def _schedule_retry_on_failure(self, retry_after_ms: float | None) -> None:
        self._awaiting_backoff = True
        self._schedule_attempt(
            retry_after_ms if retry_after_ms is not None else self._next_backoff_delay()
        )

    def _next_backoff_delay(self) -> float:
        sample = self._require().random()
        ratio = min(max(sample, 0.0), 1.0) if math.isfinite(sample) else 0.0
        # A floor of a quarter of the backoff keeps the spread of full jitter
        # without a near-zero-delay loop against Platform.
        floor = self._backoff_ms / 4
        delay = floor + (self._backoff_ms - floor) * ratio
        self._backoff_ms = min(self._backoff_ms * 2, _MAX_BACKOFF_MS)
        return delay

    def _clear_next_attempt_timer(self) -> None:
        if self._next_attempt_timer is not None:
            self._next_attempt_timer.cancel()
            self._next_attempt_timer = None

    def _clear_max_wait_timer(self) -> None:
        if self._max_wait_timer is not None:
            self._max_wait_timer.cancel()
            self._max_wait_timer = None

    # — error reporting ————————————————————————————————————————————————————————

    def _report_once(
        self, kind: IntentGraphSyncErrorKind, status: int | None, message: str
    ) -> None:
        if self._error_reported:
            return
        self._error_reported = True
        self._report(IntentGraphSyncError(kind=kind, status=status, message=message))

    def _report(self, error: IntentGraphSyncError) -> None:
        on_error = self._require().on_error
        if on_error is None:
            return
        with contextlib.suppress(Exception):
            # User observability callbacks stay fail-open.
            on_error(error)

    # — HTTP ———————————————————————————————————————————————————————————————————

    async def _get(self, if_none_match: str | None) -> _GetOutcome:
        s = self._require()
        headers = {"authorization": f"Bearer {s.api_key}"}
        if if_none_match:
            headers["if-none-match"] = if_none_match
        url = f"{s.endpoint}?source={quote(s.graph_key, safe=_URI_COMPONENT_SAFE)}"
        response = await self._send("GET", url, headers, None)
        if response is None:
            return "network"
        if response.status == 304:
            return "not_modified"
        body = _json_object(response.body)
        if response.status == 200:
            if body is None or "graph" not in body:
                return "network"
            try:
                graph = IntentGraph.from_json(_compact_json(body["graph"]))
            except (TypeError, ValueError):
                # A graph this build cannot read is a failed load: it retries.
                return "network"
            return _GetOk(graph=graph, etag=_header(response, "etag"))
        error_code = body.get("error") if body is not None else None
        if response.status == 404:
            return "feature_disabled" if error_code == "feature_disabled" else "not_found"
        if response.status == 401:
            return "auth"
        if response.status == 429:
            return _RateLimited(self._retry_after_ms(response))
        return "network"

    async def _put(self, graph_json: str, if_match: str | None) -> _PutOutcome:
        s = self._require()
        # Concatenation, not a parse and a dump, embeds the opaque graph byte for byte.
        body = f'{{"source_id":{_compact_json(s.source_id)},"graph":{graph_json}}}'
        headers = {"authorization": f"Bearer {s.api_key}", "content-type": "application/json"}
        if if_match:
            headers["if-match"] = if_match
        response = await self._send("PUT", s.endpoint, headers, body.encode("utf-8"))
        if response is None:
            return "network"
        if response.status == 200:
            return _PutOk(etag=_header(response, "etag"))
        parsed = _json_object(response.body)
        error_code = parsed.get("error") if parsed is not None else None
        if response.status == 409 and error_code == "stale_graph":
            return "conflict"
        if response.status == 400 and error_code == "invalid_graph":
            return "invalid_graph"
        if response.status == 404 and error_code == "feature_disabled":
            return "feature_disabled"
        if response.status == 401:
            return "auth"
        if response.status in (413, 415):
            return "rejected"
        if response.status == 429:
            return _RateLimited(self._retry_after_ms(response))
        return "network"

    async def _send(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> TransportResponse | None:
        s = self._require()
        request = TransportRequest(
            method=method, url=url, headers=headers, body=body, timeout=s.timeout
        )
        try:
            return await asyncio.wait_for(s.transport.send(request), s.timeout)
        except Exception:
            return None

    def _retry_after_ms(self, response: TransportResponse) -> float | None:
        return parse_retry_after(_header(response, "retry-after"), self._require().clock() * 1_000)


@dataclass(frozen=True)
class _Settings:
    mode: IntentGraphSyncMode
    source_id: str
    graph_key: str
    poll_interval_ms: float
    endpoint: str
    api_key: str
    debounce_ms: float
    max_wait_ms: float
    timeout: float
    on_replaced: ReplacedHandler | None
    on_error: ErrorHandler | None
    transport: PlatformTransport
    random: Callable[[], float]
    sleep: Sleep
    clock: Clock


def _claim(catalog: Any) -> None:
    """Allow one sync per catalog: two loops that race to PUT one source are a bug."""
    try:
        attached = catalog in _ATTACHED
    except TypeError:
        attached = id(catalog) in _ATTACHED_IDS
    if attached:
        raise RuntimeError(
            f"{_LOG_PREFIX} attach_intent_graph_sync was already called for this catalog"
        )
    try:
        _ATTACHED.add(catalog)
    except TypeError:
        _ATTACHED_IDS.add(id(catalog))


async def _settled(task: asyncio.Task[None] | None) -> None:
    if task is None:
        return
    with contextlib.suppress(Exception):
        await asyncio.shield(task)


def _warn(message: str) -> None:
    with contextlib.suppress(Exception):
        warnings.warn(message, RuntimeWarning, stacklevel=3)


def _header(response: TransportResponse, name: str) -> str | None:
    for key, value in response.headers.items():
        if key.lower() == name:
            return value
    return None


def _json_object(raw: bytes) -> Mapping[str, Any] | None:
    try:
        value = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _compact_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)
