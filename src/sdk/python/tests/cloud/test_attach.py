"""attach(): fail-open delivery of one Ratel runtime to Ratel Cloud (port of attach.test.ts)."""

from __future__ import annotations

import asyncio
import warnings
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import pytest

from ratel_ai import ExecutableTool, ratel
from ratel_ai.cloud.runtime import (
    RUNTIME_EVENT_TYPES,
    RuntimeEventRejection,
    attach,
    is_remotely_publishable,
)
from ratel_ai.cloud.testing import ManualClock, MockCloud

MOCK_BASE_URL = "https://mock.test/api/v1"

Handler = Callable[[list[dict[str, Any]]], Awaitable[None]]


class _FakeSubscription:
    def __init__(self) -> None:
        self.unsubscribed = False

    async def flush(self) -> None:
        return None

    def unsubscribe(self) -> None:
        self.unsubscribed = True


class _FakeEvents:
    def __init__(self, source_id: str = "service-a") -> None:
        self.source_id = source_id
        self.handler: Handler | None = None
        self.subscription = _FakeSubscription()

    def subscribe(self, handler: Handler) -> _FakeSubscription:
        self.handler = handler
        return self.subscription

    async def emit(self, *events: dict[str, Any]) -> None:
        assert self.handler is not None
        await self.handler(list(events))


class _FakeCatalog:
    def __init__(self) -> None:
        self.tools: list[dict[str, Any]] = [{"id": "deploy", "name": "deploy"}]
        self.fail = False

    def snapshot(self) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("snapshot failed")
        return {"source_id": "service-a", "tools": list(self.tools), "skills": []}


class _FakeRuntime:
    def __init__(self, source_id: str = "service-a") -> None:
        self.events = _FakeEvents(source_id)
        self.catalog = _FakeCatalog()


def _event(index: int, event_type: str = "search", **fields: Any) -> dict[str, Any]:
    return {
        "v": 2,
        "event_id": f"evt-{index}",
        "ts": 1_750_000_000_000 + index,
        "session_id": "session-1",
        "source_id": "service-a",
        "type": event_type,
        **fields,
    }


class _Rejections:
    def __init__(self) -> None:
        self.calls: list[Sequence[RuntimeEventRejection]] = []

    def __call__(self, rejected: Sequence[RuntimeEventRejection]) -> None:
        self.calls.append(rejected)


async def _no_sleep(_seconds: float) -> None:
    return None


def _options(mock: MockCloud, **options: Any) -> dict[str, Any]:
    return {
        "api_key": mock.api_key,
        "base_url": MOCK_BASE_URL,
        "transport": mock,
        "warn_on_failure": False,
        "sleep": _no_sleep,
        **options,
    }


def _deploy_tool() -> ExecutableTool:
    return ExecutableTool(
        id="deploy",
        name="deploy",
        description="Prepare a production release.",
        input_schema={"type": "object"},
        output_schema={"type": "string"},
        execute=lambda _args: "deployed",
    )


async def test_a_real_runtime_delivers_its_events_and_exactly_one_snapshot(
    mock: MockCloud,
) -> None:
    runtime = ratel(events={"source_id": "service-a", "session_id": "session-1"})
    handle = attach(runtime, **_options(mock))

    await runtime.tools.register(_deploy_tool())
    runtime.tools.search("production release", 1)
    await runtime.tools.invoke("deploy", {})
    await handle.flush()

    types = [event["type"] for event in mock.runtime_events]
    assert {"index_churn", "search", "invoke_start", "invoke_end"} <= set(types)
    assert all(event["source_id"] == "service-a" for event in mock.runtime_events)
    assert mock.catalog_snapshots == [
        {
            "source_id": "service-a",
            "tools": [
                {
                    "id": "deploy",
                    "name": "deploy",
                    "description": "Prepare a production release.",
                    "input_schema": {"type": "object"},
                    "output_schema": {"type": "string"},
                    "metadata": None,
                }
            ],
        }
    ]
    assert handle.status()["overall"] == "ok"
    await handle.close()


async def test_only_allowlisted_event_types_leave_the_process(mock: MockCloud) -> None:
    runtime = _FakeRuntime()
    handle = attach(runtime, **_options(mock))

    await runtime.events.emit(
        _event(1, "embedder_load"),
        _event(2, "catalog_definition"),
        _event(3, "search"),
        _event(4, "turn_start"),
    )
    await handle.flush()

    assert [event["event_id"] for event in mock.runtime_events] == ["evt-3", "evt-4"]
    assert is_remotely_publishable("usage_boost")
    assert not is_remotely_publishable("embedder_load")
    assert "catalog_definition" not in RUNTIME_EVENT_TYPES
    await handle.close()


async def test_events_and_snapshots_share_one_normalized_source_id(mock: MockCloud) -> None:
    runtime = _FakeRuntime()
    handle = attach(runtime, **_options(mock, source_id="  checkout-worker  "))

    await runtime.events.emit(_event(1))
    await handle.flush()

    assert [event["source_id"] for event in mock.runtime_events] == ["checkout-worker"]
    assert [snapshot["source_id"] for snapshot in mock.catalog_snapshots] == ["checkout-worker"]
    await handle.close()


async def test_a_503_then_200_sends_the_batch_two_times_and_becomes_healthy(
    mock: MockCloud,
) -> None:
    runtime = _FakeRuntime()
    handle = attach(runtime, **_options(mock))
    mock.script_responses("POST", "/events", 503, 200)

    await runtime.events.emit(_event(1), _event(2))
    await handle.flush()

    assert mock.received_event_batches == [[_event(1), _event(2)]] * 2
    status = handle.status()
    assert status["overall"] == "ok"
    assert status["events"]["accepted"] == 2
    assert status["events"]["last_outcome"] == {"kind": "ok", "status": 202, "message": None}
    await handle.close()


async def test_a_400_sends_the_batch_one_time_and_reports_it_one_time(mock: MockCloud) -> None:
    runtime = _FakeRuntime()
    rejections = _Rejections()
    handle = attach(runtime, **_options(mock, on_rejected=rejections))
    mock.script_responses("POST", "/events", 400)

    await runtime.events.emit(_event(1), _event(2))
    await handle.flush()
    await handle.flush()

    assert len(mock.received_event_batches) == 1
    assert len(rejections.calls) == 1
    assert [item.event_id for item in rejections.calls[0]] == ["evt-1", "evt-2"]
    assert handle.status()["overall"] == "degraded"
    await handle.close()


async def test_events_off_disables_events_only(
    mock: MockCloud, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RATEL_CLOUD_EVENTS", "off")
    runtime = _FakeRuntime()
    handle = attach(runtime, **_options(mock))

    await runtime.events.emit(_event(1))
    await handle.flush()

    assert mock.received_event_batches == []
    assert len(mock.catalog_snapshots) == 1
    assert handle.status()["overall"] == "disabled"
    await handle.close()


async def test_snapshot_churn_debounces_with_a_maximum_wait() -> None:
    mock = MockCloud()
    clock = ManualClock()
    runtime = _FakeRuntime()
    handle = attach(
        runtime,
        **_options(mock, sleep=clock.sleep, clock=clock.now, snapshot_debounce_ms=1_000),
    )
    await clock.advance(1)
    assert len(mock.catalog_snapshots) == 1

    # Churn every half second: the quiet period never comes, the max wait (4 s) fires.
    for index in range(10):
        runtime.catalog.tools.append({"id": f"tool-{index}", "name": f"tool-{index}"})
        await runtime.events.emit(_event(index, "index_churn"))
        await clock.advance(0.5)
    assert len(mock.catalog_snapshots) == 2
    assert len(mock.catalog_snapshots[1]["tools"]) == 9

    await clock.advance(1)
    assert len(mock.catalog_snapshots) == 3
    assert len(mock.catalog_snapshots[2]["tools"]) == 11
    await handle.close()


async def test_one_churn_publishes_after_the_quiet_period() -> None:
    mock = MockCloud()
    clock = ManualClock()
    runtime = _FakeRuntime()
    handle = attach(
        runtime,
        **_options(mock, sleep=clock.sleep, clock=clock.now, snapshot_debounce_ms=1_000),
    )
    await clock.advance(1)

    runtime.catalog.tools.append({"id": "audit", "name": "audit"})
    await runtime.events.emit(_event(1, "index_churn"))
    await clock.advance(0.5)
    assert len(mock.catalog_snapshots) == 1
    await clock.advance(0.5)

    assert len(mock.catalog_snapshots) == 2
    await handle.close()


async def test_the_same_runtime_gets_the_same_handle(mock: MockCloud) -> None:
    runtime = _FakeRuntime()

    first = attach(runtime, **_options(mock))
    second = attach(runtime, **_options(mock))

    assert first is second
    await first.close()
    assert runtime.events.subscription.unsubscribed
    assert attach(runtime, **_options(mock)) is not first
    await attach(runtime, **_options(mock)).close()


async def test_cloud_definitions_apply_the_overlay_and_a_304_changes_nothing(
    mock: MockCloud,
) -> None:
    mock.seed_runtime_catalog_override(
        {"kind": "tool", "entryId": "deploy", "searchableDescription": "quasar zephyr rollback"}
    )
    runtime = ratel(events={"source_id": "service-a"})
    await runtime.tools.register(_deploy_tool())
    handle = attach(runtime, **_options(mock, use_cloud_definitions=True))

    await handle.flush()
    assert [hit.tool_id for hit in runtime.tools.search("quasar zephyr rollback", 1)] == ["deploy"]

    assert await handle.refresh_cloud_definitions() is False
    assert [hit.tool_id for hit in runtime.tools.search("quasar zephyr rollback", 1)] == ["deploy"]
    overlay_requests = [
        request for request in mock.requests if request.url.endswith("/runtime-catalog/overrides")
    ]
    assert len(overlay_requests) == 2
    assert overlay_requests[1].headers["if-none-match"].startswith('"')
    # The snapshot keeps the local definition.
    assert "searchable_description" not in mock.catalog_snapshots[0]["tools"][0]
    await handle.close()


async def test_a_changed_overlay_is_applied_on_refresh(mock: MockCloud) -> None:
    runtime = ratel(events={"source_id": "service-a"})
    await runtime.tools.register(_deploy_tool())
    handle = attach(runtime, **_options(mock, use_cloud_definitions=True))
    await handle.flush()
    assert runtime.tools.search("nebula canary", 1) == []

    mock.seed_runtime_catalog_override(
        {"kind": "tool", "entryId": "deploy", "searchableDescription": "nebula canary"}
    )

    assert await handle.refresh_cloud_definitions() is True
    assert [hit.tool_id for hit in runtime.tools.search("nebula canary", 1)] == ["deploy"]
    await handle.close()


async def test_a_failed_overlay_pull_keeps_local_descriptions_and_never_raises(
    mock: MockCloud,
) -> None:
    runtime = ratel(events={"source_id": "service-a"})
    await runtime.tools.register(_deploy_tool())
    mock.script_responses("GET", "/runtime-catalog/overrides", 503)

    with pytest.warns(RuntimeWarning, match="cloud_definitions"):
        handle = attach(runtime, **_options(mock, use_cloud_definitions=True, warn_on_failure=True))
        await handle.flush()

    assert [hit.tool_id for hit in runtime.tools.search("production release", 1)] == ["deploy"]
    await handle.close()


async def test_no_delivery_failure_reaches_the_caller() -> None:
    class _Down:
        async def send(self, _request: object) -> object:
            raise ConnectionError("down")

    def explode(_: object) -> None:
        raise RuntimeError("callback failed")

    runtime = _FakeRuntime()
    handle = attach(
        runtime,
        api_key="rtl_test_key",
        base_url=MOCK_BASE_URL,
        transport=_Down(),  # type: ignore[arg-type]
        sleep=_no_sleep,
        warn_on_failure=False,
        on_rejected=explode,
        on_status_change=explode,
    )

    await runtime.events.emit(_event(1), {"type": "search", "bad": object()})
    runtime.catalog.fail = True
    await runtime.events.emit(_event(2, "index_churn"))
    await handle.flush()
    result = await handle.verify()
    await handle.close()

    assert result["kind"] == "network"
    assert handle.status()["overall"] == "degraded"


async def test_a_failure_warns_one_time_without_the_api_key(mock: MockCloud) -> None:
    runtime = _FakeRuntime()
    secret = "rtl_secret_key_value"

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        handle = attach(
            runtime,
            api_key=secret,
            base_url=MOCK_BASE_URL,
            transport=mock,
            sleep=_no_sleep,
        )
        await runtime.events.emit(_event(1))
        await handle.flush()
        await runtime.events.emit(_event(2))
        await handle.flush()
        await handle.close()

    messages = [str(warning.message) for warning in caught]
    assert messages == [
        "[ratel-cloud/runtime] auth: authentication failed — facts are not persisting"
    ]
    assert all(secret not in message for message in messages)
    assert all(secret not in str(request.body) for request in mock.requests)


async def test_nothing_is_sent_after_close(mock: MockCloud) -> None:
    runtime = _FakeRuntime()
    handle = attach(runtime, **_options(mock))
    await handle.close()
    sent = len(mock.requests)

    await runtime.events.emit(_event(1), _event(2, "index_churn"))
    await handle.flush()

    assert len(mock.requests) == sent
    assert handle.status()["events"]["dropped"] == 2


def test_attach_without_a_running_event_loop_returns_a_no_op_handle(mock: MockCloud) -> None:
    runtime = _FakeRuntime()

    with pytest.warns(RuntimeWarning, match="running event loop"):
        handle = attach(runtime, **_options(mock, use_cloud_definitions=True))

    assert handle.status()["overall"] == "degraded"
    assert runtime.events.handler is None


async def test_close_leaves_no_background_task(mock: MockCloud) -> None:
    runtime = ratel(events={"source_id": "service-a"})
    handle = attach(runtime, **_options(mock, sleep=None, use_cloud_definitions=True))
    await runtime.tools.register(_deploy_tool())
    runtime.tools.search("production release", 1)

    await handle.close()

    current = asyncio.current_task()
    assert [task for task in asyncio.all_tasks() if task is not current] == []
    assert len(mock.catalog_snapshots) == 1
