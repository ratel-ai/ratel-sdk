"""The wiring of ``ratel(cloud=...)`` (the port of ``ratel-cloud.ts``).

One configuration turns on each Cloud feature for one runtime and gives the
Cloud API from the same object (``r.cloud``). `ratel_ai.ratel` imports this
module only when the caller sets ``cloud``: with no ``cloud``, nothing here runs.

`ratel_ai.ratel` is synchronous, but the Cloud features run on an asyncio event
loop. When ``ratel(cloud=...)`` runs inside a running loop, the features start
at once on that loop. When it runs before the loop starts, the handle holds the
runtime events (bounded, oldest dropped first) and the features start on the
first awaited ``r.cloud`` call (``ready()``, ``flush()``, ``verify()``,
``refresh_definitions()`` or ``close()``), which replays the held events. The
features stay on the loop they started on.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import warnings
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypedDict, Union

from .config import resolve_cloud_config
from .errors import CloudError
from .runtime.attach import (
    CloudDefinitionsRuntimeAttachment,
    RuntimeAttachment,
    attach,
    cloud_definitions_ready,
)
from .runtime.delivery_status import (
    DeliveryResult,
    DeliveryStatus,
    RuntimeDeliveryStatus,
    StatusChangeHandler,
)
from .runtime.intent_graph_sync import (
    ErrorHandler,
    IntentGraphSync,
    IntentGraphSyncMode,
    ReplacedHandler,
    attach_intent_graph_sync,
)
from .runtime.publisher import RejectionHandler, RuntimeEventsPublisher, events_enabled
from .runtime.retry import Clock, RetryOptions, Sleep
from .runtime.tool_picker import RatelCloud, ToolPicker, ToolPickerMode, ratel_cloud
from .runtime.types import RuntimeEvent
from .transport import CloudTransport

if TYPE_CHECKING:
    from .._custom_ranking import RankFn
    from .._native import IntentGraph
    from ..catalog import SearchMethod
    from ..ratel import Ratel

__all__ = [
    "CloudEventsOptions",
    "CloudIntentGraphOptions",
    "CloudOptions",
    "RatelCloudRuntime",
    "RatelCloudSetup",
    "setup_ratel_cloud",
]

#: Events held before the Cloud features start, when ``events`` sets no
#: ``queue_capacity``. The same default as the event publisher queue.
_HELD_EVENTS_CAPACITY = 1_024

_SharedKey = Literal["api_key", "base_url", "timeout", "transport"]
_SHARED_KEYS: tuple[_SharedKey, ...] = ("api_key", "base_url", "timeout", "transport")


class CloudEventsOptions(TypedDict, total=False):
    """Tuning of runtime event and catalog snapshot delivery.

    The keys are the keyword arguments of `ratel_ai.cloud.runtime.attach` that
    the features do not share.
    """

    queue_capacity: int
    flush_interval_ms: float
    on_rejected: RejectionHandler
    retry: RetryOptions
    sleep: Sleep
    clock: Clock
    snapshot_debounce_ms: float
    snapshot_reconcile_interval_ms: int
    on_status_change: StatusChangeHandler
    warn_on_failure: bool


class CloudIntentGraphOptions(TypedDict, total=False):
    """Tuning of intent graph sync.

    The keys are the keyword arguments of
    `ratel_ai.cloud.runtime.attach_intent_graph_sync` that the features do not share.
    """

    mode: IntentGraphSyncMode
    graph_key: str
    poll_interval_ms: float
    debounce_ms: float
    max_wait_ms: float
    on_replaced: ReplacedHandler
    on_error: ErrorHandler
    random: Callable[[], float]
    sleep: Sleep
    clock: Clock


class CloudOptions(TypedDict, total=False):
    """The ``cloud`` option of `ratel_ai.ratel`.

    ``api_key``, ``base_url``, ``timeout`` and ``transport`` apply to every
    feature. ``api_key`` and ``base_url`` resolve through
    `ratel_ai.cloud.resolve_cloud_config`: the option, then ``RATEL_API_KEY`` /
    ``RATEL_BASE_URL``, then the default.

    Attributes:
        api_key: Project API key. Defaults to ``RATEL_API_KEY``. Required.
        base_url: API root, including ``/api/v1``. Defaults to ``RATEL_BASE_URL``,
            then ``https://cloud.ratel.sh/api/v1``.
        timeout: Per-request timeout in seconds. Defaults to the default of each
            feature (30 s for most requests).
        transport: The HTTP transport. Defaults to `ratel_ai.cloud.UrllibTransport`.
            Tests pass a `ratel_ai.cloud.testing.MockCloud`.
        source_id: Stable deployment source. Defaults to the source id of the
            runtime's event stream.
        events: Publish runtime events and catalog snapshots. Default on. A dict
            turns it on with tuning. ``RATEL_CLOUD_EVENTS=off`` turns events off.
        definitions: Give Cloud ownership of the retrieval descriptions. Default
            off. Needs ``events``: Cloud overrides the catalog it receives.
        intent_graph: Sync the intent graph of the tool catalog with Cloud and rank
            with it (adaptive ranking). Default off. A dict turns it on with tuning.
            ``RATEL_CLOUD_INTENT_GRAPH=off`` turns it off.
        tool_picker: Rank tools with the Cloud Tool Picker (``method="custom"``).
            Default off. `True` means ``"precise"``. Do not set ``method`` or
            ``retrieve_fn`` with it.
    """

    api_key: str
    base_url: str
    timeout: float
    transport: CloudTransport
    source_id: str
    events: bool | CloudEventsOptions
    definitions: bool
    intent_graph: bool | CloudIntentGraphOptions
    tool_picker: bool | ToolPickerMode


@dataclass(frozen=True)
class RatelCloudSetup:
    """What `setup_ratel_cloud` gives `ratel_ai.ratel`.

    Attributes:
        retrieve_fn: The Tool Picker, when ``tool_picker`` is on. The catalogs then
            rank with ``method="custom"`` and this function. `None` keeps the
            caller's ranking.
        connect: Start the Cloud features for the runtime and return its ``r.cloud``.
    """

    retrieve_fn: ToolPicker | None
    connect: Callable[[Ratel[Any]], RatelCloudRuntime]


#: The ``cloud`` values that turn Cloud on.
CloudOption = Union[Literal[True], CloudOptions]


def setup_ratel_cloud(
    cloud: CloudOption, *, method: SearchMethod | None, retrieve_fn: RankFn | None
) -> RatelCloudSetup:
    """Validate the ``cloud`` option of `ratel_ai.ratel` before the catalogs exist.

    Sends no request.

    Args:
        cloud: `True` (read ``RATEL_API_KEY``) or a `CloudOptions`.
        method: The ``method`` the caller passed to `ratel_ai.ratel`, or `None`.
        retrieve_fn: The ``retrieve_fn`` the caller passed, or `None`.

    Returns:
        The ranking for the catalogs and the function that starts the features.

    Raises:
        CloudError: Code ``config``, when no API key is set or the option
            conflicts with the caller's ranking.
        ValueError: ``tool_picker`` is not a known Tool Picker mode.
    """
    options: CloudOptions = {} if cloud is True else cloud
    shared = _shared_config(options)
    if not resolve_cloud_config(api_key=options.get("api_key")).api_key:
        raise _config_error(
            "ratel(cloud=...) needs an API key: pass cloud api_key or set RATEL_API_KEY"
        )
    if options.get("events") is False and options.get("definitions") is True:
        raise _config_error("ratel(cloud=...): cloud definitions needs cloud events")
    picker_mode = _tool_picker_mode(options.get("tool_picker"))
    if picker_mode is not None and (method is not None or retrieve_fn is not None):
        raise _config_error(
            "ratel(cloud=...): cloud tool_picker sets method and retrieve_fn;"
            " remove method and retrieve_fn"
        )
    api = ratel_cloud(**shared)
    picker = api.tool_picker.with_mode(picker_mode or "precise")
    return RatelCloudSetup(
        retrieve_fn=None if picker_mode is None else picker,
        connect=lambda runtime: RatelCloudRuntime(runtime, options, shared, api, picker),
    )


class RatelCloudRuntime:
    """``r.cloud``: the Cloud API plus the lifecycle of the Cloud features of one runtime.

    Attributes:
        tool_picker: Ranks tools with the Tool Picker, in the ``tool_picker`` mode
            (``"precise"`` when it is off).
        skills: Skill catalog management: create, update, publish and import skills.
        suggestions: Review of the improvement suggestions that Cloud drafts.
        intents: Intent analysis of conversations and the recurring-ask ledger.
        jobs: Status of the asynchronous jobs that Cloud runs.
        runtime_catalog: The Cloud-owned retrieval descriptions of the runtime catalog.
    """

    def __init__(
        self,
        runtime: Ratel[Any],
        options: CloudOptions,
        shared: dict[str, Any],
        api: RatelCloud,
        picker: ToolPicker,
    ) -> None:
        """Create the handle. `ratel_ai.ratel` is the public constructor."""
        self.tool_picker = picker
        self.skills = api.skills
        self.suggestions = api.suggestions
        self.intents = api.intents
        self.jobs = api.jobs
        self.runtime_catalog = api.runtime_catalog
        self._runtime = runtime
        self._options = options
        self._shared = shared
        events_tuning = _tuning(options.get("events"))
        self._events_on = options.get("events") is not False
        self._intent_graph_on = _tuning(options.get("intent_graph")) is not None
        self._held: _HeldEvents | None = None
        if self._events_on or self._intent_graph_on:
            capacity = (events_tuning or {}).get("queue_capacity", _HELD_EVENTS_CAPACITY)
            self._held = _HeldEvents(runtime.events, capacity)
        self._host = _CloudHost(self._held, runtime)
        self._attachment: RuntimeAttachment | CloudDefinitionsRuntimeAttachment | None = None
        self._intent_graph: asyncio.Task[IntentGraphSync | None] | None = None
        self._verifier: RuntimeEventsPublisher | None = None
        self._started = False
        self._closing: asyncio.Future[None] | None = None
        with contextlib.suppress(RuntimeError):
            asyncio.get_running_loop()
            self._start()

    def status(self) -> RuntimeDeliveryStatus:
        """Return the current JSON-serializable delivery health of events and snapshots."""
        if self._attachment is not None:
            return self._attachment.status()
        return DeliveryStatus(enabled=self._events_on and events_enabled()).snapshot()

    async def verify(self) -> DeliveryResult:
        """Probe event ingestion. The publisher queues do not change."""
        self._start()
        if self._attachment is not None:
            return await self._attachment.verify()
        if self._verifier is None:
            self._verifier = RuntimeEventsPublisher(**self._shared)
        return await self._verifier.verify()

    async def ready(self) -> None:
        """Wait until the first definitions pull and the first intent graph load end.

        It ends with success or not, and never raises. Before the loop ran, this
        call also starts the Cloud features.
        """
        self._start()
        await _settled(cloud_definitions_ready(self._host), self._intent_graph_loaded())

    async def flush(self) -> None:
        """Deliver the events, snapshots and intent graph changes already accepted.

        Never raises.
        """
        self._start()
        await _settled(
            self._attachment.flush() if self._attachment is not None else None,
            self._flush_intent_graph(),
        )

    async def close(self) -> None:
        """Flush, then stop each Cloud feature of this runtime. Idempotent. Never raises."""
        self._start()
        if self._closing is None:
            self._closing = asyncio.ensure_future(self._close_once())
        await asyncio.shield(self._closing)

    async def refresh_definitions(self) -> bool:
        """Pull and apply changed Cloud definitions.

        Returns:
            `True` when a changed overlay was applied. `False` on a 304, a
            failure, or when ``definitions`` is off.
        """
        self._start()
        refresh = getattr(self._attachment, "refresh_cloud_definitions", None)
        if refresh is None:
            return False
        try:
            return bool(await refresh())
        except Exception:
            return False

    def _start(self) -> None:
        """Start each Cloud feature on the running loop. Only the first call has an effect."""
        if self._started:
            return
        self._started = True
        if self._events_on:
            options: dict[str, Any] = {
                **(_tuning(self._options.get("events")) or {}),
                **self._shared,
                **self._source_id(),
            }
            self._attachment = attach(
                self._host,
                **options,
                use_cloud_definitions=self._options.get("definitions") is True,
            )
        if self._intent_graph_on:
            self._intent_graph = asyncio.ensure_future(self._start_intent_graph())
        elif self._held is not None:
            self._held.release()

    async def _start_intent_graph(self) -> IntentGraphSync | None:
        """Start intent graph sync for the tool catalog, then rank with its graph.

        The tool catalog ranks with each graph Cloud replaces it with too. A sync
        that cannot start warns one time and leaves ranking as it was (fail-open).
        """
        tuning = _tuning(self._options.get("intent_graph")) or {}
        consume = tuning.get("mode") == "consume"
        on_replaced = tuning.get("on_replaced")
        catalog = self._runtime.tools.catalog

        def rank(graph: IntentGraph) -> None:
            if consume:
                catalog.experimental_enable_adaptive_ranking(graph, learn=False, graph_key="cloud")
            else:
                catalog.experimental_enable_adaptive_ranking(graph)

        def replaced(graph: IntentGraph) -> None:
            rank(graph)
            if on_replaced is not None:
                on_replaced(graph)

        options: dict[str, Any] = {**tuning, **self._shared, **self._source_id()}
        options["on_replaced"] = replaced
        try:
            sync = await attach_intent_graph_sync(self._host, **options)
            if sync.status != "disabled":
                rank(sync.graph)
            return sync
        except Exception as error:
            _warn_intent_graph_failure(error)
            return None
        finally:
            if self._held is not None:
                self._held.release()

    async def _intent_graph_loaded(self) -> IntentGraphSync | None:
        if self._intent_graph is None:
            return None
        return await asyncio.shield(self._intent_graph)

    async def _flush_intent_graph(self) -> None:
        sync = await self._intent_graph_loaded()
        if sync is not None:
            await sync.flush()

    async def _close_once(self) -> None:
        sync = await _settled_value(self._intent_graph_loaded())
        await _settled(
            self._attachment.close() if self._attachment is not None else None,
            sync.close() if sync is not None else None,
            self._verifier.close() if self._verifier is not None else None,
        )
        if self._held is not None:
            self._held.close()

    def _source_id(self) -> dict[str, str]:
        source_id = self._options.get("source_id")
        return {} if source_id is None else {"source_id": source_id}


class _CloudHost:
    """The runtime as the Cloud features see it: the held event stream and the catalog."""

    def __init__(self, held: _HeldEvents | None, runtime: Ratel[Any]) -> None:
        self.events: Any = held if held is not None else runtime.events
        self.catalog = runtime.catalog


class _HeldEvents:
    """The runtime event stream, held from construction until the Cloud features start.

    It subscribes to the runtime with a synchronous handler, so it needs no event
    loop. Until `release`, it keeps the events (bounded, oldest dropped first) and
    gives them to each new subscriber first. After `release`, it only forwards.
    """

    def __init__(self, events: Any, capacity: int) -> None:
        self._events = events
        self._lock = threading.Lock()
        self._kept: deque[RuntimeEvent] = deque(maxlen=max(capacity, 1))
        self._keeping = True
        self._subscribers: list[_HeldSubscription] = []
        self._native = events.subscribe(self._on_batch)

    @property
    def source_id(self) -> str:
        """The stable source id of the runtime's stream."""
        return str(self._events.source_id)

    def subscribe(
        self, handler: Callable[[list[RuntimeEvent]], Awaitable[None]]
    ) -> _HeldSubscription:
        """Deliver the kept events, then each new batch, to ``handler`` on the running loop."""
        subscription = _HeldSubscription(self, handler, asyncio.get_running_loop())
        with self._lock:
            kept = list(self._kept)
            self._subscribers.append(subscription)
        if kept:
            subscription.deliver(kept)
        return subscription

    def release(self) -> None:
        """Stop keeping events for later subscribers."""
        with self._lock:
            self._keeping = False
            self._kept.clear()

    def close(self) -> None:
        """Stop the runtime subscription."""
        self.release()
        with contextlib.suppress(Exception):
            self._native.unsubscribe()

    async def flush_runtime(self) -> None:
        """Wait until the runtime delivered its accepted events to this stream."""
        with contextlib.suppress(Exception):
            await self._native.flush()

    def remove(self, subscription: _HeldSubscription) -> None:
        """Stop forwarding to ``subscription``."""
        with self._lock, contextlib.suppress(ValueError):
            self._subscribers.remove(subscription)

    def _on_batch(self, batch: list[RuntimeEvent]) -> None:
        with self._lock:
            if self._keeping:
                self._kept.extend(batch)
            subscribers = list(self._subscribers)
        for subscription in subscribers:
            subscription.deliver(batch)


class _HeldSubscription:
    """One subscriber of `_HeldEvents`: it runs the handler on the loop it subscribed on."""

    def __init__(
        self,
        owner: _HeldEvents,
        handler: Callable[[list[RuntimeEvent]], Awaitable[None]],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        self._owner = owner
        self._handler = handler
        self._loop = loop
        self._pending: set[asyncio.Future[None]] = set()
        self._stopped = False

    def deliver(self, batch: list[RuntimeEvent]) -> None:
        """Run the handler with ``batch`` on the subscription loop, from any thread."""
        if self._stopped:
            return
        try:
            on_loop = asyncio.get_running_loop() is self._loop
        except RuntimeError:
            on_loop = False
        if on_loop:
            self._run(list(batch))
            return
        with contextlib.suppress(RuntimeError):
            # A closed loop drops the batch: delivery is fail-open.
            self._loop.call_soon_threadsafe(self._run, list(batch))

    async def flush(self) -> None:
        """Wait until the accepted events reached the handler."""
        await self._owner.flush_runtime()
        await asyncio.sleep(0)
        while self._pending:
            await asyncio.gather(*tuple(self._pending), return_exceptions=True)
            await asyncio.sleep(0)

    def unsubscribe(self) -> None:
        """Stop the subscription."""
        self._stopped = True
        self._owner.remove(self)

    def _run(self, batch: list[RuntimeEvent]) -> None:
        if self._stopped:
            return
        try:
            future = asyncio.ensure_future(self._handler(batch))
        except Exception:
            return
        self._pending.add(future)
        future.add_done_callback(self._finish)

    def _finish(self, future: asyncio.Future[None]) -> None:
        self._pending.discard(future)
        if not future.cancelled():
            future.exception()


def _shared_config(options: CloudOptions) -> dict[str, Any]:
    """The settings that every feature shares, with no key that holds `None`."""
    return {key: options[key] for key in _SHARED_KEYS if options.get(key) is not None}


def _config_error(message: str) -> CloudError:
    return CloudError(message, status=None, code="config")


def _tool_picker_mode(option: bool | ToolPickerMode | None) -> ToolPickerMode | None:
    if option is None or option is False:
        return None
    return "precise" if option is True else option


def _tuning(option: Any) -> Any:
    """`None` when the feature is off, else its tuning dict (empty for `True`)."""
    if option is None or option is False:
        return None
    return option if isinstance(option, dict) else {}


def _warn_intent_graph_failure(error: BaseException) -> None:
    with contextlib.suppress(Exception):
        warnings.warn(
            f"[ratel-cloud] intent graph sync did not start: {error}",
            RuntimeWarning,
            stacklevel=2,
        )


async def _settled(*awaitables: Awaitable[Any] | None) -> None:
    """Wait for each awaitable to end. Ignore each result and each exception."""
    await asyncio.gather(*(a for a in awaitables if a is not None), return_exceptions=True)


async def _settled_value(awaitable: Awaitable[IntentGraphSync | None]) -> IntentGraphSync | None:
    try:
        return await awaitable
    except Exception:
        return None
