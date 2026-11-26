"""RuntimeEventsPublisher: bounded, batched, fail-open delivery (port of publisher.test.ts)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest

from ratel_ai.platform.runtime import (
    RUNTIME_EVENT_BATCH_MAX_EVENTS,
    DeliveryStatus,
    RetryOptions,
    RuntimeEventRejection,
    RuntimeEventsPublisher,
)
from ratel_ai.platform.testing import ManualClock, MockPlatform, json_response

MOCK_BASE_URL = "https://mock.test/api/v1"


def _event(index: int, **fields: Any) -> dict[str, Any]:
    return {
        "v": 2,
        "event_id": f"evt-{index}",
        "ts": 1_750_000_000_000 + index,
        "session_id": "session-1",
        "source_id": "service-a",
        "type": "search",
        **fields,
    }


class _Rejections:
    def __init__(self) -> None:
        self.calls: list[Sequence[RuntimeEventRejection]] = []

    def __call__(self, rejected: Sequence[RuntimeEventRejection]) -> None:
        self.calls.append(rejected)


class _Sleeps:
    def __init__(self) -> None:
        self.seconds: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.seconds.append(seconds)


@pytest.fixture
async def clock() -> AsyncIterator[ManualClock]:
    yield ManualClock()


def _publisher(mock: MockPlatform, **options: Any) -> RuntimeEventsPublisher:
    options.setdefault("sleep", _Sleeps())
    return RuntimeEventsPublisher(
        api_key=mock.api_key, base_url=MOCK_BASE_URL, transport=mock, **options
    )


async def test_flush_delivers_queued_events_as_one_batch(mock: MockPlatform) -> None:
    status = DeliveryStatus(warn_on_failure=False)
    publisher = _publisher(mock, delivery_status=status)

    publisher.publish(_event(1))
    publisher.publish(_event(2))
    await publisher.flush()

    assert mock.received_event_batches == [[_event(1), _event(2)]]
    assert mock.requests[0].headers["authorization"] == f"Bearer {mock.api_key}"
    events = status.snapshot()["events"]
    assert events["accepted"] == 2
    assert events["last_outcome"] == {"kind": "ok", "status": 202, "message": None}
    await publisher.close()


async def test_a_background_flush_runs_after_the_flush_interval(
    mock: MockPlatform, clock: ManualClock
) -> None:
    publisher = _publisher(mock, flush_interval_ms=1_000, sleep=clock.sleep, clock=clock.now)

    publisher.publish(_event(1))
    await clock.advance(0.5)
    assert mock.received_event_batches == []
    await clock.advance(0.5)

    assert mock.received_event_batches == [[_event(1)]]
    await publisher.close()


async def test_a_full_queue_drops_the_oldest_event_and_reports_the_drop(mock: MockPlatform) -> None:
    status = DeliveryStatus(warn_on_failure=False)
    publisher = _publisher(mock, queue_capacity=2, delivery_status=status)

    for index in (1, 2, 3):
        publisher.publish(_event(index))
    await publisher.flush()

    [batch] = mock.received_event_batches
    assert [event["event_id"] for event in batch[:2]] == ["evt-2", "evt-3"]
    dropped = batch[2]
    assert dropped["type"] == "events_dropped"
    assert dropped["dropped_count"] == 1
    assert dropped["reason"] == "queue_overflow"
    assert dropped["window_start_ts"] == dropped["window_end_ts"] == _event(1)["ts"]
    assert dropped["session_id"] == "session-1" and dropped["source_id"] == "service-a"
    assert status.snapshot()["events"]["dropped"] == 1
    await publisher.close()


async def test_batches_obey_the_event_count_limit(mock: MockPlatform) -> None:
    publisher = _publisher(mock, queue_capacity=RUNTIME_EVENT_BATCH_MAX_EVENTS + 10)

    for index in range(RUNTIME_EVENT_BATCH_MAX_EVENTS + 1):
        publisher.publish(_event(index))
    await publisher.flush()

    assert [len(batch) for batch in mock.received_event_batches] == [
        RUNTIME_EVENT_BATCH_MAX_EVENTS,
        1,
    ]
    await publisher.close()


async def test_batches_obey_the_byte_limit_and_reject_an_oversized_event(
    mock: MockPlatform,
) -> None:
    rejections = _Rejections()
    publisher = _publisher(mock, on_rejected=rejections)
    padding = "x" * 60_000

    publisher.publish(_event(0, query="y" * 70_000))
    for index in range(1, 71):
        publisher.publish(_event(index, query=padding))
    await publisher.flush()

    sizes = [len(batch) for batch in mock.received_event_batches]
    assert sum(sizes) == 70
    assert len(sizes) == 2
    bodies = [request.body for request in mock.requests]
    assert all(body is not None and len(body) <= 3_900_000 for body in bodies)
    assert len(rejections.calls) == 1
    [rejection] = rejections.calls[0]
    assert rejection.event_id == "evt-0"
    assert "65536 bytes" in rejection.reason
    await publisher.close()


async def test_a_400_drops_the_batch_and_reports_it_one_time(mock: MockPlatform) -> None:
    rejections = _Rejections()
    status = DeliveryStatus(warn_on_failure=False)
    publisher = _publisher(mock, on_rejected=rejections, delivery_status=status)
    mock.script_responses("POST", "/events", 400)

    publisher.publish(_event(1))
    publisher.publish(_event(2))
    await publisher.flush()
    await publisher.flush()

    assert len(mock.received_event_batches) == 1
    assert len(rejections.calls) == 1
    assert [(item.event_id, item.reason) for item in rejections.calls[0]] == [
        ("evt-1", "batch rejected with HTTP 400"),
        ("evt-2", "batch rejected with HTTP 400"),
    ]
    events = status.snapshot()["events"]
    assert events["rejected"] == 2
    assert events["last_outcome"] == {
        "kind": "rejected_payload",
        "status": 400,
        "message": "payload rejected with HTTP 400",
    }
    await publisher.close()


async def test_a_transient_failure_retries_and_honors_retry_after(mock: MockPlatform) -> None:
    sleeps = _Sleeps()
    publisher = _publisher(mock, sleep=sleeps)
    mock.script_responses(
        "POST",
        "/events",
        json_response({"error": "rate_limited"}, status=429, headers={"Retry-After": "3"}),
        503,
    )

    publisher.publish(_event(1))
    await publisher.flush()

    assert mock.received_event_batches == [[_event(1)]] * 3
    assert sleeps.seconds[0] == 3.0
    assert mock.runtime_events == [_event(1)]
    await publisher.close()


async def test_exhausted_retries_drop_the_batch_and_report_it(mock: MockPlatform) -> None:
    rejections = _Rejections()
    status = DeliveryStatus(warn_on_failure=False)
    publisher = _publisher(
        mock,
        on_rejected=rejections,
        delivery_status=status,
        retry=RetryOptions(max_attempts=2),
    )
    mock.script_responses("POST", "/events", ConnectionError("down"), ConnectionError("down"))

    publisher.publish(_event(1))
    await publisher.flush()

    assert len(mock.received_event_batches) == 2
    assert [item.reason for item in rejections.calls[0]] == ["batch delivery failed after retries"]
    assert status.snapshot()["events"]["last_outcome"] == {
        "kind": "network",
        "status": None,
        "message": "request failed before reaching Ratel Platform",
    }
    await publisher.close()


async def test_platform_rejects_in_an_accepted_body_reach_on_rejected(mock: MockPlatform) -> None:
    rejections = _Rejections()
    status = DeliveryStatus(warn_on_failure=False)
    publisher = _publisher(mock, on_rejected=rejections, delivery_status=status)
    mock.script_responses(
        "POST",
        "/events",
        json_response(
            {"accepted": 1, "duplicates": 0, "rejected": [{"event_id": "evt-2", "reason": "bad"}]},
            status=202,
        ),
    )

    publisher.publish(_event(1))
    publisher.publish(_event(2))
    await publisher.flush()

    assert [(item.event_id, item.reason) for item in rejections.calls[0]] == [("evt-2", "bad")]
    events = status.snapshot()["events"]
    assert (events["accepted"], events["rejected"]) == (1, 1)
    await publisher.close()


async def test_a_deferred_batch_is_requeued_and_retried_slowly(
    mock: MockPlatform, clock: ManualClock
) -> None:
    publisher = _publisher(mock, sleep=clock.sleep, clock=clock.now)
    mock.script_responses(
        "POST", "/events", json_response({"accepted": 0, "deferred": True}, status=202)
    )

    publisher.publish(_event(1))
    await publisher.flush()
    assert mock.runtime_events == []
    await clock.advance(29)
    assert len(mock.received_event_batches) == 1
    await clock.advance(1)

    assert mock.runtime_events == [_event(1)]
    await publisher.close()


async def test_events_off_disables_delivery(
    mock: MockPlatform, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RATEL_PLATFORM_EVENTS", " OFF ")
    publisher = _publisher(mock)

    publisher.publish(_event(1))
    await publisher.flush()

    assert mock.requests == []
    await publisher.close()


async def test_events_published_after_close_are_counted_as_dropped(mock: MockPlatform) -> None:
    status = DeliveryStatus(warn_on_failure=False)
    publisher = _publisher(mock, delivery_status=status)
    await publisher.close()

    publisher.publish(_event(1))
    await publisher.flush()

    assert mock.requests == []
    assert status.snapshot()["events"]["dropped"] == 1


async def test_verify_probes_with_an_empty_batch_and_classifies_the_answer(
    mock: MockPlatform,
) -> None:
    status = DeliveryStatus(warn_on_failure=False)
    publisher = RuntimeEventsPublisher(
        api_key="rtl_wrong", base_url=MOCK_BASE_URL, transport=mock, delivery_status=status
    )
    publisher.publish(_event(1))

    result = await publisher.verify()

    assert result == {"kind": "auth", "status": 401, "message": "authentication failed"}
    assert mock.requests[0].body == b'{"events":[]}'
    assert status.snapshot()["overall"] == "blocked"
    await publisher.close()


async def test_no_api_key_reports_auth_without_a_request(
    mock: MockPlatform, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RATEL_API_KEY", raising=False)
    status = DeliveryStatus(warn_on_failure=False)
    publisher = RuntimeEventsPublisher(
        base_url=MOCK_BASE_URL, transport=mock, delivery_status=status
    )

    publisher.publish(_event(1))
    await publisher.flush()

    assert mock.requests == []
    assert status.snapshot()["events"]["last_outcome"]["kind"] == "auth"  # type: ignore[index]
    await publisher.close()


async def test_failing_callbacks_never_reach_the_caller(mock: MockPlatform) -> None:
    def explode(_: object) -> None:
        raise RuntimeError("callback failed")

    status = DeliveryStatus(warn_on_failure=False, on_status_change=explode)
    publisher = _publisher(mock, on_rejected=explode, delivery_status=status)
    mock.script_responses("POST", "/events", 400)

    publisher.publish(_event(1))
    publisher.publish({"v": 2, "event_id": "bad", "value": object()})
    await publisher.flush()

    assert len(mock.received_event_batches) == 1
    await publisher.close()
