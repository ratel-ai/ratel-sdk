"""attach_intent_graph_sync(): sync an IntentGraph with Ratel Platform (intent-graph-sync.test.ts).

The graph is the real native `IntentGraph`; a real `ToolCatalog` moves its ``rev``.
The event stream is a fake, so a test decides when an ``invoke_start`` arrives.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from ratel_ai import ExecutableTool, IntentGraph, ToolCatalog
from ratel_ai.platform.runtime import IntentGraphSyncError, attach_intent_graph_sync
from ratel_ai.platform.testing import ManualClock, MockPlatform, json_response

MOCK_BASE_URL = "https://mock.test/api/v1"
SOURCE = "billing-agent"

Handler = Callable[[list[dict[str, Any]]], Awaitable[None]]


class _FakeSubscription:
    def __init__(self, events: _FakeEvents) -> None:
        self._events = events

    async def flush(self) -> None:
        return None

    def unsubscribe(self) -> None:
        self._events.handler = None
        self._events.unsubscribed = True


class _FakeEvents:
    def __init__(self) -> None:
        self.source_id = SOURCE
        self.handler: Handler | None = None
        self.unsubscribed = False
        self.subscribe_count = 0

    def subscribe(self, handler: Handler) -> _FakeSubscription:
        self.subscribe_count += 1
        self.handler = handler
        return _FakeSubscription(self)

    async def emit(self, event_type: str = "invoke_start") -> None:
        if self.handler is not None:
            await self.handler([{"v": 2, "type": event_type, "source_id": SOURCE}])


class _FakeRuntime:
    def __init__(self) -> None:
        self.events = _FakeEvents()


async def _learning_catalog() -> ToolCatalog:
    catalog = ToolCatalog()
    await catalog.register(
        [
            ExecutableTool(
                id="deploy_rollback",
                name="deploy_rollback",
                description="Roll back the last deploy",
                execute=lambda _args: "rolled back",
            )
        ]
    )
    return catalog


async def _learn(catalog: ToolCatalog, graph: IntentGraph, query: str = "roll back") -> None:
    """Move ``graph.rev`` with real usage, the way an app does."""
    catalog.experimental_enable_adaptive_ranking(graph)
    rev = graph.rev
    catalog.experimental_record_baseline_turn(query=query, invoked=["deploy_rollback"])
    catalog.experimental_disable_adaptive_ranking()
    assert graph.rev > rev


async def _seeded_graph(turns: int = 1) -> IntentGraph:
    graph = IntentGraph()
    catalog = await _learning_catalog()
    for turn in range(turns):
        await _learn(catalog, graph, f"roll back the deploy number {turn}")
    return graph


def _puts(mock: MockPlatform) -> list[Any]:
    return [request for request in mock.requests if request.method == "PUT"]


def _gets(mock: MockPlatform) -> list[Any]:
    return [request for request in mock.requests if request.method == "GET"]


async def _attach(
    runtime: _FakeRuntime, mock: MockPlatform, clock: ManualClock, **options: Any
) -> Any:
    options.setdefault("on_replaced", lambda _graph: None)
    return await attach_intent_graph_sync(
        runtime,
        api_key=mock.api_key,
        base_url=MOCK_BASE_URL,
        transport=mock,
        sleep=clock.sleep,
        clock=clock.now,
        random=lambda: 1.0,
        **options,
    )


@pytest.fixture(autouse=True)
def _intent_graph_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RATEL_PLATFORM_INTENT_GRAPH", raising=False)


async def test_push_mode_saves_the_graph_to_mockplatform_after_the_debounce() -> None:
    mock, clock, runtime = MockPlatform(), ManualClock(), _FakeRuntime()
    sync = await _attach(runtime, mock, clock, debounce_ms=2_000)
    assert sync.status == "idle"

    await _learn(await _learning_catalog(), sync.graph)
    await runtime.events.emit("invoke_start")
    await clock.advance(1.999)
    assert _puts(mock) == []
    await clock.advance(0.001)

    assert len(_puts(mock)) == 1
    stored = mock.intent_graphs[SOURCE]
    assert stored.rev == sync.graph.rev
    assert IntentGraph.from_json(json.dumps(stored.graph)).rev == sync.graph.rev
    assert sync.status == "idle"
    await sync.close()


async def test_push_mode_loads_the_stored_graph_and_sends_if_match_with_its_etag() -> None:
    mock, clock, runtime = MockPlatform(), ManualClock(), _FakeRuntime()
    seeded = await _seeded_graph()
    stored = mock.seed_intent_graph(SOURCE, json.loads(seeded.to_json()))

    sync = await _attach(runtime, mock, clock, debounce_ms=100)
    assert sync.graph.rev == seeded.rev
    assert _gets(mock)[0].url == f"{MOCK_BASE_URL}/intent-graph?source={SOURCE}"

    await _learn(await _learning_catalog(), sync.graph)
    await runtime.events.emit()
    await clock.advance(0.1)

    assert _puts(mock)[0].headers["if-match"] == stored.etag
    await sync.close()


async def test_continuous_activity_saves_on_the_max_wait_instead_of_the_debounce() -> None:
    mock, clock, runtime = MockPlatform(), ManualClock(), _FakeRuntime()
    sync = await _attach(runtime, mock, clock, debounce_ms=2_000, max_wait_ms=5_000)
    learner = await _learning_catalog()

    for second in range(1, 5):
        await _learn(learner, sync.graph, f"roll back now {second}")
        await runtime.events.emit()
        await clock.advance(1.0)
        assert _puts(mock) == [], f"saved after {second} s"
    await _learn(learner, sync.graph, "roll back once more")
    await runtime.events.emit()
    await clock.advance(1.0)

    assert len(_puts(mock)) == 1
    assert mock.intent_graphs[SOURCE].rev == sync.graph.rev
    await sync.close()


async def test_a_batch_without_an_invoke_or_without_a_rev_change_sends_no_put() -> None:
    mock, clock, runtime = MockPlatform(), ManualClock(), _FakeRuntime()
    sync = await _attach(runtime, mock, clock, debounce_ms=100)

    await runtime.events.emit("invoke_start")  # rev did not move
    await _learn(await _learning_catalog(), sync.graph)
    await runtime.events.emit("search")  # not a qualifying event
    await clock.advance(120.0)

    assert _puts(mock) == []
    await sync.close()


async def test_a_409_loads_the_newer_graph_and_calls_on_replaced_one_time() -> None:
    mock, clock, runtime = MockPlatform(), ManualClock(), _FakeRuntime()
    mock.seed_intent_graph(SOURCE, json.loads((await _seeded_graph()).to_json()))
    replaced: list[IntentGraph] = []
    sync = await _attach(runtime, mock, clock, debounce_ms=100, on_replaced=replaced.append)

    # Another process writes a newer graph first.
    newer = await _seeded_graph(turns=3)
    mock.seed_intent_graph(SOURCE, json.loads(newer.to_json()))
    await _learn(await _learning_catalog(), sync.graph)
    await runtime.events.emit()
    await clock.advance(0.1)

    assert len(replaced) == 1
    assert replaced[0] is sync.graph
    assert sync.graph.rev == newer.rev
    assert sync.status == "idle"
    puts = len(_puts(mock))
    await clock.advance(120.0)
    assert len(_puts(mock)) == puts == 1, "the rejected write is never retried"
    await sync.close()


async def test_a_409_without_on_replaced_warns_one_time_without_graph_content() -> None:
    mock, clock, runtime = MockPlatform(), ManualClock(), _FakeRuntime()
    sync = await _attach(runtime, mock, clock, debounce_ms=100, on_replaced=None)
    mock.seed_intent_graph(SOURCE, json.loads((await _seeded_graph()).to_json()))

    await _learn(await _learning_catalog(), sync.graph, "SECRET-QUERY-123")
    await runtime.events.emit()
    with pytest.warns(RuntimeWarning, match="intent_graph_conflict") as caught:
        await clock.advance(0.1)

    assert len(caught) == 1
    assert "SECRET-QUERY-123" not in str(caught[0].message)
    await sync.close()


async def test_consume_mode_never_sends_a_put_and_adopts_a_newer_poll() -> None:
    mock, clock, runtime = MockPlatform(), ManualClock(), _FakeRuntime()
    first = mock.seed_intent_graph("platform", json.loads((await _seeded_graph()).to_json()))
    replaced: list[IntentGraph] = []
    sync = await _attach(
        runtime, mock, clock, mode="consume", graph_key="platform", on_replaced=replaced.append
    )
    assert runtime.events.subscribe_count == 0
    assert sync.graph.rev == first.rev

    await _learn(await _learning_catalog(), sync.graph)
    await runtime.events.emit()
    await clock.advance(300.0)
    assert _gets(mock)[-1].headers["if-none-match"] == first.etag
    assert replaced == [], "a 304 keeps the same graph"

    newer = await _seeded_graph(turns=3)
    mock.seed_intent_graph("platform", json.loads(newer.to_json()))
    await clock.advance(300.0)
    await sync.flush()

    assert [graph.rev for graph in replaced] == [newer.rev]
    assert _puts(mock) == []
    assert [request.url.rsplit("=", 1)[1] for request in _gets(mock)] == ["platform"] * 4
    await sync.close()


async def test_consume_mode_treats_a_missing_graph_as_not_built_yet() -> None:
    mock, clock, runtime = MockPlatform(), ManualClock(), _FakeRuntime()
    sync = await _attach(runtime, mock, clock, mode="consume")

    assert (sync.status, sync.graph.rev) == ("idle", 0)
    assert clock.pending == [300.0]
    await sync.close()


async def test_consume_mode_clamps_the_poll_interval_to_the_15_second_floor() -> None:
    mock, clock, runtime = MockPlatform(), ManualClock(), _FakeRuntime()
    mock.seed_intent_graph(SOURCE, json.loads(IntentGraph().to_json()))
    sync = await _attach(runtime, mock, clock, mode="consume", poll_interval_ms=1_000)

    assert clock.pending == [15.0]
    await clock.advance(15.0)
    assert len(_gets(mock)) == 2
    await sync.close()


async def test_consume_mode_backs_off_a_failed_poll_with_a_15_second_floor() -> None:
    mock, clock, runtime = MockPlatform(), ManualClock(), _FakeRuntime()
    mock.script_responses("GET", "/intent-graph", 503)
    errors: list[IntentGraphSyncError] = []
    sync = await _attach(runtime, mock, clock, mode="consume", on_error=errors.append)

    assert sync.status == "error"
    assert [error.kind for error in errors] == ["network"]
    assert clock.pending == [15.0]
    await sync.flush()
    assert len(_gets(mock)) == 1, "flush() respects the backoff wait"
    await clock.advance(15.0)
    assert sync.status == "idle"
    await sync.close()


async def test_a_failed_load_retries_the_get_with_doubling_backoff_then_saves() -> None:
    mock, clock, runtime = MockPlatform(), ManualClock(), _FakeRuntime()
    mock.script_responses("GET", "/intent-graph", 500, ConnectionError("down"), 502)
    errors: list[IntentGraphSyncError] = []
    sync = await _attach(runtime, mock, clock, debounce_ms=100, on_error=errors.append)
    assert sync.status == "error"

    delays: list[float] = []
    for _ in range(3):
        delays.append(clock.pending[0])
        await clock.advance(clock.pending[0])

    assert delays == [1.0, 2.0, 4.0]
    assert [error.kind for error in errors] == ["network"], "reported one time per failure"
    assert (sync.status, _puts(mock)) == ("idle", [])
    await _learn(await _learning_catalog(), sync.graph)
    await runtime.events.emit()
    await clock.advance(0.1)
    assert len(_puts(mock)) == 1
    await sync.close()


async def test_the_backoff_is_capped_at_30_seconds_and_floored_at_a_quarter() -> None:
    mock, clock, runtime = MockPlatform(), ManualClock(), _FakeRuntime()
    mock.script_responses("GET", "/intent-graph", *([500] * 7))
    sync = await attach_intent_graph_sync(
        runtime,
        api_key=mock.api_key,
        base_url=MOCK_BASE_URL,
        transport=mock,
        sleep=clock.sleep,
        clock=clock.now,
        random=lambda: 0.0,
    )

    delays: list[float] = []
    for _ in range(7):
        delays.append(clock.pending[0])
        await clock.advance(clock.pending[0])

    assert delays == [0.25, 0.5, 1.0, 2.0, 4.0, 7.5, 7.5]
    await sync.close()


async def test_a_429_on_a_save_honours_retry_after() -> None:
    mock, clock, runtime = MockPlatform(), ManualClock(), _FakeRuntime()
    sync = await _attach(runtime, mock, clock, debounce_ms=100)
    mock.script_responses(
        "PUT", "/intent-graph", json_response({}, status=429, headers={"Retry-After": "7"})
    )

    await _learn(await _learning_catalog(), sync.graph)
    await runtime.events.emit()
    await clock.advance(0.1)
    assert clock.pending == [7.0]
    await clock.advance(7.0)

    assert len(_puts(mock)) == 2
    assert mock.intent_graphs[SOURCE].rev == sync.graph.rev
    await sync.close()


async def test_a_401_stops_the_sync_after_one_report() -> None:
    mock, clock, runtime = MockPlatform(), ManualClock(), _FakeRuntime()
    errors: list[IntentGraphSyncError] = []
    sync = await attach_intent_graph_sync(
        runtime,
        api_key="rtl_revoked",
        base_url=MOCK_BASE_URL,
        transport=mock,
        sleep=clock.sleep,
        clock=clock.now,
        on_error=errors.append,
    )
    await clock.advance(600.0)
    await sync.flush()

    assert [(error.kind, error.status) for error in errors] == [("auth", 401)]
    assert (len(mock.requests), clock.pending) == (1, [])
    await sync.close()


async def test_a_400_invalid_graph_skips_that_rev_but_a_later_rev_saves() -> None:
    mock, clock, runtime = MockPlatform(), ManualClock(), _FakeRuntime()
    errors: list[IntentGraphSyncError] = []
    sync = await _attach(runtime, mock, clock, debounce_ms=100, on_error=errors.append)
    mock.script_responses(
        "PUT", "/intent-graph", json_response({"error": "invalid_graph"}, status=400)
    )
    learner = await _learning_catalog()

    await _learn(learner, sync.graph)
    await runtime.events.emit()
    await clock.advance(120.0)
    assert [(error.kind, error.rev) for error in errors] == [("invalid_graph", sync.graph.rev)]
    assert len(_puts(mock)) == 1

    await _learn(learner, sync.graph, "roll back again")
    await runtime.events.emit()
    await clock.advance(0.1)
    assert len(_puts(mock)) == 2
    await sync.close()


async def test_feature_disabled_goes_terminal_and_never_puts() -> None:
    mock, clock, runtime = MockPlatform(), ManualClock(), _FakeRuntime()
    mock.script_responses(
        "GET", "/intent-graph", json_response({"error": "feature_disabled"}, status=404)
    )
    with pytest.warns(RuntimeWarning, match="intent_graph_disabled"):
        sync = await _attach(runtime, mock, clock, debounce_ms=100)

    assert sync.status == "disabled"
    assert runtime.events.unsubscribed
    await sync.flush()
    assert (len(mock.requests), clock.pending) == (1, [])
    await sync.close()


async def test_ratel_platform_intent_graph_off_makes_no_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RATEL_PLATFORM_INTENT_GRAPH", " OFF ")
    mock, clock, runtime = MockPlatform(), ManualClock(), _FakeRuntime()
    sync = await _attach(runtime, mock, clock)

    await sync.flush()
    await sync.close()

    assert (sync.status, sync.graph.rev) == ("disabled", 0)
    assert mock.requests == []
    assert runtime.events.subscribe_count == 0


async def test_close_saves_a_pending_change_unsubscribes_and_leaves_no_running_task() -> None:
    import asyncio

    mock, clock, runtime = MockPlatform(), ManualClock(), _FakeRuntime()
    sync = await _attach(runtime, mock, clock, debounce_ms=10_000)
    await _learn(await _learning_catalog(), sync.graph)
    await runtime.events.emit()

    await sync.close()
    await sync.close()

    assert len(_puts(mock)) == 1
    assert runtime.events.unsubscribed
    assert asyncio.all_tasks() == {asyncio.current_task()}


async def test_a_second_attach_for_the_same_catalog_raises() -> None:
    mock, clock, runtime = MockPlatform(), ManualClock(), _FakeRuntime()
    sync = await _attach(runtime, mock, clock)

    with pytest.raises(RuntimeError, match="already called"):
        await _attach(runtime, mock, clock)
    await sync.close()


@pytest.mark.parametrize("option", [{"graph_key": "platform"}, {"poll_interval_ms": 20_000}])
async def test_consume_only_options_raise_in_push_mode(option: dict[str, Any]) -> None:
    mock, clock, runtime = MockPlatform(), ManualClock(), _FakeRuntime()

    with pytest.raises(ValueError, match="consume-mode only"):
        await _attach(runtime, mock, clock, **option)
    assert mock.requests == []


async def test_failures_never_report_a_graph_member() -> None:
    mock, clock, runtime = MockPlatform(), ManualClock(), _FakeRuntime()
    errors: list[IntentGraphSyncError] = []
    sync = await _attach(runtime, mock, clock, debounce_ms=100, on_error=errors.append)
    mock.script_responses("PUT", "/intent-graph", 500)
    mock.script_responses("PUT", "/intent-graph", json_response({}, status=413))

    await _learn(await _learning_catalog(), sync.graph, "SECRET-QUERY-123")
    await runtime.events.emit()
    await clock.advance(0.1)
    await clock.advance(clock.pending[0])

    assert [error.kind for error in errors] == ["network", "invalid_graph"]
    assert all("SECRET-QUERY-123" not in repr(error) for error in errors)
    await sync.close()


async def test_the_put_embeds_the_graph_json_byte_for_byte() -> None:
    mock, clock, runtime = MockPlatform(), ManualClock(), _FakeRuntime()
    sync = await _attach(runtime, mock, clock, debounce_ms=100)
    await _learn(await _learning_catalog(), sync.graph)

    await sync.flush()

    body = _puts(mock)[0].body
    assert body == (f'{{"source_id":"{SOURCE}","graph":{sync.graph.to_json()}}}'.encode())
    await sync.close()


async def test_a_real_ratel_runtime_round_trips_its_graph_through_mockplatform() -> None:
    from ratel_ai import ratel

    mock, clock = MockPlatform(), ManualClock()
    seeded = await _seeded_graph()
    runtime = ratel(events={"source_id": SOURCE})
    mock.seed_intent_graph(SOURCE, json.loads(seeded.to_json()))
    sync = await attach_intent_graph_sync(
        runtime,
        api_key=mock.api_key,
        base_url=MOCK_BASE_URL,
        transport=mock,
        sleep=clock.sleep,
        clock=clock.now,
        on_replaced=lambda _graph: None,
    )
    assert (sync.graph.rev, sync.graph.cluster_count) == (seeded.rev, seeded.cluster_count)

    await _learn(await _learning_catalog(), sync.graph, "roll back production right now")
    await sync.flush()

    assert IntentGraph.from_json(json.dumps(mock.intent_graphs[SOURCE].graph)).rev == sync.graph.rev
    await sync.close()
