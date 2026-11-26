"""`attach`: subscribe one Ratel runtime to fail-open delivery to Ratel Platform.

The port of ``attach.ts``. One attachment owns two lanes that share one API key,
one deployment and one normalized source id:

- runtime events: only the allowlisted types leave the process
  (`RuntimeEventsPublisher`);
- catalog snapshots: published after catalog churn, behind a debounce with a
  maximum wait of four debounce windows (`CatalogSnapshotsPublisher`).

With ``use_platform_definitions=True``, Ratel Platform also owns the retrieval
descriptions of the live tools, skills and facts. No delivery failure raises
into the caller, and the API key is never logged.
"""

from __future__ import annotations

import asyncio
import contextlib
import warnings
import weakref
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal, Protocol, overload

from ..config import resolve_platform_config
from ..runtime_catalog import RuntimeCatalogClient
from ..transport import PlatformHttpClient, PlatformTransport
from .allowlist import is_remotely_publishable
from .delivery_status import (
    DeliveryResult,
    DeliveryStatus,
    RuntimeDeliveryStatus,
    StatusChangeHandler,
    classify_delivery,
)
from .platform_definitions import PlatformDefinitionsSource
from .publisher import RejectionHandler, RuntimeEventsPublisher, events_enabled
from .retry import BackgroundTasks, Clock, RetryOptions, Sleep, non_negative, start_timer
from .snapshots import CatalogSnapshotsPublisher, normalize_source_id
from .types import RuntimeCatalogToolDefinition, RuntimeEvent

__all__ = [
    "PlatformDefinitionsRuntimeAttachment",
    "RatelRuntime",
    "RatelRuntimeCatalog",
    "RatelRuntimeEvents",
    "RuntimeAttachment",
    "RuntimeEventSubscription",
    "attach",
    "platform_definitions_ready",
]

_DEFAULT_SNAPSHOT_DEBOUNCE_MS = 500.0
# Sustained churn re-arms the debounce forever; the max wait forces publication.
_SNAPSHOT_MAX_WAIT_MULTIPLIER = 4
_LOG_PREFIX = "[ratel-platform/runtime]"


class RuntimeEventSubscription(Protocol):
    """The subscription handle that `RatelRuntimeEvents.subscribe` returns."""

    async def flush(self) -> None:
        """Wait until the accepted events reached the handler."""
        ...

    def unsubscribe(self) -> None:
        """Stop the subscription."""
        ...


class RatelRuntimeEvents(Protocol):
    """The event stream of a runtime, like `ratel_ai.RuntimeEvents`."""

    @property
    def source_id(self) -> str:
        """The stable source id of the stream."""
        ...

    def subscribe(
        self, handler: Callable[[list[RuntimeEvent]], Awaitable[None]]
    ) -> RuntimeEventSubscription:
        """Deliver each event batch to ``handler``."""
        ...


class RatelRuntimeCatalog(Protocol):
    """The catalog of a runtime, like `ratel_ai.RuntimeCatalog`.

    `attach` also uses ``experimental_attach_definition_overrides`` when the
    catalog has it and ``use_platform_definitions=True``.
    """

    def snapshot(self) -> Mapping[str, Any]:
        """Return the complete serializable catalog."""
        ...


class RatelRuntime(Protocol):
    """A Ratel runtime, like the `ratel_ai.Ratel` that `ratel_ai.ratel` returns."""

    @property
    def events(self) -> RatelRuntimeEvents:
        """The event stream."""
        ...

    @property
    def catalog(self) -> RatelRuntimeCatalog:
        """The catalog."""
        ...


class RuntimeAttachment(Protocol):
    """The handle of one attached runtime."""

    def status(self) -> RuntimeDeliveryStatus:
        """Return the current JSON-serializable delivery health."""
        ...

    async def verify(self) -> DeliveryResult:
        """Probe event ingestion. The queues do not change."""
        ...

    async def flush(self) -> None:
        """Deliver the runtime work that this process already accepted."""
        ...

    async def close(self) -> None:
        """Stop accepting runtime work, deliver what was accepted, stop every task."""
        ...


class PlatformDefinitionsRuntimeAttachment(RuntimeAttachment, Protocol):
    """The handle of a runtime attached with ``use_platform_definitions=True``."""

    async def refresh_platform_definitions(self) -> bool:
        """Pull and apply changed Platform-owned retrieval descriptions.

        Returns:
            `True` when a changed overlay was applied, `False` on a 304 or a failure.
        """
        ...


_ATTACHMENTS: weakref.WeakKeyDictionary[Any, _Attachment] = weakref.WeakKeyDictionary()
_WARNED: set[str] = set()


@overload
def attach(
    runtime: RatelRuntime,
    *,
    api_key: str | None = ...,
    base_url: str | None = ...,
    timeout: float | None = ...,
    transport: PlatformTransport | None = ...,
    source_id: str | None = ...,
    queue_capacity: int | None = ...,
    flush_interval_ms: float | None = ...,
    on_rejected: RejectionHandler | None = ...,
    retry: RetryOptions | None = ...,
    sleep: Sleep | None = ...,
    clock: Clock | None = ...,
    snapshot_debounce_ms: float | None = ...,
    snapshot_reconcile_interval_ms: int | None = ...,
    on_status_change: StatusChangeHandler | None = ...,
    warn_on_failure: bool = ...,
    use_platform_definitions: Literal[True],
) -> PlatformDefinitionsRuntimeAttachment: ...


@overload
def attach(
    runtime: RatelRuntime,
    *,
    api_key: str | None = ...,
    base_url: str | None = ...,
    timeout: float | None = ...,
    transport: PlatformTransport | None = ...,
    source_id: str | None = ...,
    queue_capacity: int | None = ...,
    flush_interval_ms: float | None = ...,
    on_rejected: RejectionHandler | None = ...,
    retry: RetryOptions | None = ...,
    sleep: Sleep | None = ...,
    clock: Clock | None = ...,
    snapshot_debounce_ms: float | None = ...,
    snapshot_reconcile_interval_ms: int | None = ...,
    on_status_change: StatusChangeHandler | None = ...,
    warn_on_failure: bool = ...,
    use_platform_definitions: bool = ...,
) -> RuntimeAttachment: ...


def attach(
    runtime: RatelRuntime,
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout: float | None = None,
    transport: PlatformTransport | None = None,
    source_id: str | None = None,
    queue_capacity: int | None = None,
    flush_interval_ms: float | None = None,
    on_rejected: RejectionHandler | None = None,
    retry: RetryOptions | None = None,
    sleep: Sleep | None = None,
    clock: Clock | None = None,
    snapshot_debounce_ms: float | None = None,
    snapshot_reconcile_interval_ms: int | None = None,
    on_status_change: StatusChangeHandler | None = None,
    warn_on_failure: bool = True,
    use_platform_definitions: bool = False,
) -> RuntimeAttachment | PlatformDefinitionsRuntimeAttachment:
    """Subscribe one Ratel runtime to fail-open delivery to Ratel Platform.

    Call it from a running event loop. A second call with the same runtime
    returns the same handle (and turns on Platform definitions when it asks for
    them). It never raises: when the runtime cannot be attached, it warns and
    returns a no-op handle.

    Args:
        runtime: The runtime, e.g. the result of `ratel_ai.ratel`.
        api_key: Project API key. Defaults to ``RATEL_API_KEY``.
        base_url: API root. Defaults to ``RATEL_BASE_URL``, then production.
        timeout: Per-request timeout in seconds. Default 30.
        transport: The HTTP transport. Defaults to `UrllibTransport`.
        source_id: Stable deployment source. Defaults to ``runtime.events.source_id``.
            It is trimmed and truncated to 512 characters.
        queue_capacity: Events held before the oldest is dropped. Default 1,024.
        flush_interval_ms: Delay before queued events drain on their own. Default 1 s.
        on_rejected: Called with terminal delivery rejects and omitted catalog tools.
        retry: How one request retries. Default 3 attempts, 250 ms initial
            backoff, 30 s maximum backoff.
        sleep: The timer, in seconds. Defaults to `asyncio.sleep`.
        clock: The clock, in seconds since the epoch. Defaults to `time.time`.
        snapshot_debounce_ms: Quiet period after catalog churn before a snapshot.
            Default 500 ms. The maximum wait is four times this value.
        snapshot_reconcile_interval_ms: Maximum age of a durable snapshot before it
            is sent again. Default 5 minutes.
        on_status_change: Called when a delivery outcome kind changes.
        warn_on_failure: Warn one time per failing kind until it recovers. Default `True`.
        use_platform_definitions: Give Ratel Platform ownership of the retrieval
            descriptions. The initial pull runs in the background, and
            ``flush()`` and ``close()`` wait for it. Default `False`.

    Returns:
        The handle. With ``use_platform_definitions=True`` it also has
        ``refresh_platform_definitions()``.
    """
    try:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            _warn_once("no_loop", f"{_LOG_PREFIX} attach() requires a running event loop")
            return _NoopAttachment()
        if not _has_runtime_events(runtime):
            _warn_once(
                "version_skew",
                f"{_LOG_PREFIX} version_skew: this runtime exposes no events interface"
                " — facts are not persisting",
            )
            return _NoopAttachment()
        platform = _PlatformSettings(api_key, base_url, timeout, transport)
        existing = _ATTACHMENTS.get(runtime)
        if existing is not None:
            if use_platform_definitions:
                existing.enable_platform_definitions(platform)
            return existing
        attachment = _Attachment(
            runtime,
            platform,
            source_id=source_id if source_id is not None else runtime.events.source_id,
            queue_capacity=queue_capacity,
            flush_interval_ms=flush_interval_ms,
            on_rejected=on_rejected,
            retry=retry,
            sleep=sleep,
            clock=clock,
            snapshot_debounce_ms=snapshot_debounce_ms,
            snapshot_reconcile_interval_ms=snapshot_reconcile_interval_ms,
            on_status_change=on_status_change,
            warn_on_failure=warn_on_failure,
        )
        with contextlib.suppress(TypeError):
            # A runtime that cannot be weakly referenced simply gets a new handle each call.
            _ATTACHMENTS[runtime] = attachment
        if use_platform_definitions:
            attachment.enable_platform_definitions(platform)
        return attachment
    except Exception:
        return _NoopAttachment()


async def platform_definitions_ready(runtime: RatelRuntime) -> None:
    """Wait until the first Platform definitions pull of ``runtime`` ends, with success or not.

    It returns at once when Platform definitions are off or the runtime is not attached.

    Args:
        runtime: The attached runtime.
    """
    attachment = _ATTACHMENTS.get(runtime)
    if attachment is not None:
        await attachment.wait_for_initial_pull()


class _PlatformSettings:
    """The Platform settings that both lanes and the definitions source share."""

    def __init__(
        self,
        api_key: str | None,
        base_url: str | None,
        timeout: float | None,
        transport: PlatformTransport | None,
    ) -> None:
        # Resolve one time, so both lanes always share one key and one deployment.
        config = resolve_platform_config(api_key=api_key, base_url=base_url, timeout=timeout)
        self.api_key = config.api_key
        self.base_url = config.base_url
        self.timeout = config.timeout
        self.transport = transport

    def definitions_source(self) -> PlatformDefinitionsSource:
        config = resolve_platform_config(
            api_key=self.api_key, base_url=self.base_url, timeout=self.timeout
        )
        return PlatformDefinitionsSource(
            RuntimeCatalogClient(PlatformHttpClient(config, transport=self.transport))
        )


class _Attachment:
    """The live handle of one attached runtime."""

    def __init__(
        self,
        runtime: RatelRuntime,
        platform: _PlatformSettings,
        *,
        source_id: str,
        queue_capacity: int | None,
        flush_interval_ms: float | None,
        on_rejected: RejectionHandler | None,
        retry: RetryOptions | None,
        sleep: Sleep | None,
        clock: Clock | None,
        snapshot_debounce_ms: float | None,
        snapshot_reconcile_interval_ms: int | None,
        on_status_change: StatusChangeHandler | None,
        warn_on_failure: bool,
    ) -> None:
        self._runtime = runtime
        # One normalization at the boundary keeps both lanes on one source identity.
        self._source_id = normalize_source_id(source_id)
        self._sleep: Sleep = sleep if sleep is not None else asyncio.sleep
        self._status = DeliveryStatus(
            enabled=events_enabled(),
            warn_on_failure=warn_on_failure,
            on_status_change=on_status_change,
            clock=clock,
        )
        shared: dict[str, Any] = {
            "api_key": platform.api_key,
            "base_url": platform.base_url,
            "timeout": platform.timeout,
            "transport": platform.transport,
            "retry": retry,
            "sleep": sleep,
            "clock": clock,
            "on_rejected": on_rejected,
            "delivery_status": self._status,
        }
        self._events = RuntimeEventsPublisher(
            queue_capacity=queue_capacity, flush_interval_ms=flush_interval_ms, **shared
        )
        self._snapshots = CatalogSnapshotsPublisher(
            debounce_ms=0, reconcile_interval_ms=snapshot_reconcile_interval_ms, **shared
        )
        self._debounce_ms = non_negative(snapshot_debounce_ms, _DEFAULT_SNAPSHOT_DEBOUNCE_MS)
        self._max_wait_ms = self._debounce_ms * _SNAPSHOT_MAX_WAIT_MULTIPLIER
        self._tasks = BackgroundTasks()
        self._dirty = False
        self._debounce_timer: asyncio.Task[None] | None = None
        self._max_wait_timer: asyncio.Task[None] | None = None
        self._closed = False
        self._closing: asyncio.Future[None] | None = None
        self._definitions = _PlatformDefinitionsController(
            runtime.catalog, warn_on_failure, self._tasks
        )
        self._subscription = runtime.events.subscribe(self._on_events)
        self._mark_snapshot_dirty()

    def status(self) -> RuntimeDeliveryStatus:
        """Return the current JSON-serializable delivery health."""
        return self._status.snapshot()

    async def verify(self) -> DeliveryResult:
        """Probe event ingestion with an empty batch. The queues do not change."""
        try:
            return await self._events.verify()
        except Exception:
            return classify_delivery(None)

    async def flush(self) -> None:
        """Deliver the runtime work that this process already accepted."""
        try:
            await self._subscription.flush()
        except Exception:
            # SDK delivery is observational and must not make host shutdown fail.
            pass
        await self._definitions.wait_for_initial_pull()
        await self._flush_publishers()

    async def close(self) -> None:
        """Stop accepting runtime work, deliver what was accepted, stop every task."""
        if self._closing is None:
            self._closing = asyncio.ensure_future(self._close_once())
        await asyncio.shield(self._closing)

    async def refresh_platform_definitions(self) -> bool:
        """Pull and apply changed Platform-owned retrieval descriptions."""
        return await self._definitions.refresh()

    def enable_platform_definitions(self, platform: _PlatformSettings) -> None:
        """Turn on Platform definitions and start the initial pull."""
        self._definitions.enable(platform.definitions_source())

    async def wait_for_initial_pull(self) -> None:
        """Wait until the first Platform definitions pull ends."""
        await self._definitions.wait_for_initial_pull()

    async def _close_once(self) -> None:
        await self.flush()
        self._closed = True
        with contextlib.suppress(Exception):
            # Detach failures cannot escape into host shutdown.
            self._subscription.unsubscribe()
        if _ATTACHMENTS.get(self._runtime) is self:
            del _ATTACHMENTS[self._runtime]
        self._cancel_snapshot_timers()
        # One more drain captures envelopes that arrived after unsubscribe.
        await self._flush_publishers()
        # Sealing both publishers guarantees no request starts after close() returns.
        await asyncio.gather(self._events.close(), self._snapshots.close(), return_exceptions=True)
        self._definitions.close()
        await self._tasks.wait()

    async def _on_events(self, batch: list[RuntimeEvent]) -> None:
        churn = False
        for event in batch:
            try:
                event_type = event.get("type")
                # Non-allowlisted types can carry local-only detail and never leave the process.
                if not is_remotely_publishable(event_type):
                    continue
                churn = churn or event_type == "index_churn"
                if event.get("source_id") != self._source_id:
                    event = {**event, "source_id": self._source_id}
                self._events.publish(event)
            except Exception:
                continue
        if churn:
            self._mark_snapshot_dirty()

    async def _flush_publishers(self) -> None:
        self._flush_dirty_snapshot()
        await asyncio.gather(self._events.flush(), self._snapshots.flush(), return_exceptions=True)

    def _mark_snapshot_dirty(self) -> None:
        if self._closed:
            return
        self._dirty = True
        if self._debounce_timer is not None:
            self._debounce_timer.cancel()
        self._debounce_timer = start_timer(
            self._debounce_ms, self._on_debounce, self._sleep, self._tasks
        )
        if self._debounce_ms == 0 or self._max_wait_timer is not None:
            return
        self._max_wait_timer = start_timer(
            self._max_wait_ms, self._on_max_wait, self._sleep, self._tasks
        )

    def _on_debounce(self) -> None:
        self._debounce_timer = None
        self._flush_dirty_snapshot()

    def _on_max_wait(self) -> None:
        self._max_wait_timer = None
        self._flush_dirty_snapshot()

    def _flush_dirty_snapshot(self) -> None:
        self._cancel_snapshot_timers()
        if not self._dirty:
            return
        self._dirty = False
        try:
            snapshot = self._runtime.catalog.snapshot()
            tools = [_to_catalog_tool(tool) for tool in snapshot["tools"]]
            self._snapshots.publish({"source_id": self._source_id, "tools": tools})
        except Exception:
            # Snapshot observation is optional and must never affect the host operation.
            pass

    def _cancel_snapshot_timers(self) -> None:
        for timer in (self._debounce_timer, self._max_wait_timer):
            if timer is not None:
                timer.cancel()
        self._debounce_timer = None
        self._max_wait_timer = None


class _PlatformDefinitionsController:
    """Attaches the Platform overlay one time and refreshes it on request."""

    def __init__(
        self, catalog: RatelRuntimeCatalog, warn_on_failure: bool, tasks: BackgroundTasks
    ) -> None:
        self._catalog = catalog
        self._warn_on_failure = warn_on_failure
        self._tasks = tasks
        self._source: PlatformDefinitionsSource | None = None
        self._attachment: Any = None
        self._attaching: asyncio.Task[Any] | None = None
        self._closed = False
        self._warning_active = False

    def enable(self, source: PlatformDefinitionsSource) -> None:
        if self._closed:
            return
        if self._source is None:
            self._source = source
        self._ensure_attaching()

    async def wait_for_initial_pull(self) -> None:
        attaching = self._attaching
        if attaching is not None:
            await asyncio.gather(attaching, return_exceptions=True)

    async def refresh(self) -> bool:
        if self._closed:
            return False
        was_attached = self._attachment is not None
        attaching = self._ensure_attaching()
        if attaching is not None:
            await asyncio.gather(attaching, return_exceptions=True)
        if self._attachment is None:
            return False
        if not was_attached:
            return True
        try:
            changed = bool(await self._attachment.refresh())
        except Exception as error:
            self._warn(error)
            return False
        self._warning_active = False
        return changed

    def close(self) -> None:
        self._closed = True

    def _ensure_attaching(self) -> asyncio.Task[Any] | None:
        if self._closed or self._source is None or self._attachment is not None:
            return None
        if self._attaching is not None:
            return self._attaching
        attach_overrides = getattr(self._catalog, "experimental_attach_definition_overrides", None)
        if attach_overrides is None:
            _warn_once(
                "definitions_version_skew",
                f"{_LOG_PREFIX} version_skew: Platform definitions need a ratel-ai runtime"
                " with definition overrides — local Retrieval descriptions remain active",
            )
            return None
        self._attaching = self._tasks.spawn(self._attach_once(attach_overrides, self._source))
        return self._attaching

    async def _attach_once(self, attach_overrides: Any, source: PlatformDefinitionsSource) -> None:
        try:
            self._attachment = await attach_overrides(source)
            self._warning_active = False
        except Exception as error:
            self._warn(error)
        finally:
            self._attaching = None

    def _warn(self, error: BaseException) -> None:
        if not self._warn_on_failure or self._warning_active:
            return
        self._warning_active = True
        active = (
            "local Retrieval descriptions"
            if self._attachment is None
            else "last successfully applied Platform Retrieval descriptions"
        )
        message = str(error) or type(error).__name__
        with contextlib.suppress(Exception):
            warnings.warn(
                f"{_LOG_PREFIX} platform_definitions: {message} — {active} remain active",
                RuntimeWarning,
                stacklevel=2,
            )


class _NoopAttachment:
    """The handle of a runtime that could not be attached. Every call is a no-op."""

    def status(self) -> RuntimeDeliveryStatus:
        """Return a degraded status that names the cause."""
        return {
            "overall": "degraded",
            "events": {
                "last_outcome": classify_delivery(None),
                "last_accepted_at": None,
                "last_attempt_at": None,
                "last_error": "runtime attachment could not be created",
                "accepted": 0,
                "rejected": 0,
                "dropped": 0,
            },
            "snapshots": {},
        }

    async def verify(self) -> DeliveryResult:
        """Return a network outcome: nothing can be sent."""
        return classify_delivery(None)

    async def flush(self) -> None:
        """Do nothing."""

    async def close(self) -> None:
        """Do nothing."""

    async def refresh_platform_definitions(self) -> bool:
        """Return `False`: nothing can be pulled."""
        return False


def _has_runtime_events(runtime: Any) -> bool:
    try:
        events = runtime.events
        return callable(getattr(events, "subscribe", None)) and isinstance(events.source_id, str)
    except Exception:
        return False


def _to_catalog_tool(tool: Mapping[str, Any]) -> RuntimeCatalogToolDefinition:
    converted: RuntimeCatalogToolDefinition = {"id": tool["id"], "name": tool["name"]}
    description = tool.get("description")
    if isinstance(description, str):
        converted["description"] = description
    searchable = tool.get("experimental_searchable_description")
    if isinstance(searchable, str):
        converted["experimental_searchable_description"] = searchable
    for key in ("input_schema", "output_schema", "metadata"):
        value = tool.get(key)
        if value is None or isinstance(value, Mapping):
            converted[key] = None if value is None else dict(value)
    return converted


def _warn_once(key: str, message: str) -> None:
    if key in _WARNED:
        return
    _WARNED.add(key)
    with contextlib.suppress(Exception):
        warnings.warn(message, RuntimeWarning, stacklevel=3)
