"""CatalogSnapshotsPublisher: debounced, deduplicated, reconciled snapshots (snapshots.test.ts)."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ratel_ai.cloud.runtime import (
    CatalogSnapshotsPublisher,
    DeliveryStatus,
    RetryOptions,
    RuntimeCatalogSnapshot,
    RuntimeEventRejection,
)
from ratel_ai.cloud.testing import ManualClock, MockCloud, json_response

MOCK_BASE_URL = "https://mock.test/api/v1"


class _Rejections:
    def __init__(self) -> None:
        self.calls: list[Sequence[RuntimeEventRejection]] = []

    def __call__(self, rejected: Sequence[RuntimeEventRejection]) -> None:
        self.calls.append(rejected)


def _snapshot(*tools: dict[str, Any], source_id: str = "service-a") -> RuntimeCatalogSnapshot:
    return {"source_id": source_id, "tools": list(tools)}  # type: ignore[typeddict-item]


def _tool(tool_id: str, **fields: Any) -> dict[str, Any]:
    return {"id": tool_id, "name": tool_id, "description": f"{tool_id} tool", **fields}


def _publisher(mock: MockCloud, clock: ManualClock, **options: Any) -> CatalogSnapshotsPublisher:
    options.setdefault("debounce_ms", 0)
    return CatalogSnapshotsPublisher(
        api_key=mock.api_key,
        base_url=MOCK_BASE_URL,
        transport=mock,
        sleep=clock.sleep,
        clock=clock.now,
        **options,
    )


async def test_publishes_the_wire_body_and_records_a_durable_snapshot(mock: MockCloud) -> None:
    clock = ManualClock()
    status = DeliveryStatus(warn_on_failure=False, clock=clock.now)
    publisher = _publisher(mock, clock, delivery_status=status)

    publisher.publish(
        _snapshot(
            _tool(
                " deploy ",
                input_schema={"type": "object"},
                experimental_searchable_description=" ship release ",
            ),
            _tool("audit", metadata={"team": "ops"}),
        )
    )
    await publisher.flush()

    assert mock.catalog_snapshots == [
        {
            "source_id": "service-a",
            "tools": [
                {
                    "id": "deploy",
                    "name": "deploy",
                    "description": "deploy  tool",
                    "searchable_description": "ship release",
                    "input_schema": {"type": "object"},
                    "output_schema": None,
                    "metadata": None,
                },
                {
                    "id": "audit",
                    "name": "audit",
                    "description": "audit tool",
                    "input_schema": None,
                    "output_schema": None,
                    "metadata": {"team": "ops"},
                },
            ],
        }
    ]
    assert mock.requests[0].method == "PUT"
    snapshot_status = status.snapshot()["snapshots"]["service-a"]
    assert snapshot_status["pending_since"] is None
    assert snapshot_status["last_durable_at"] == int(clock.now() * 1_000)
    await publisher.close()


async def test_an_unchanged_snapshot_is_not_sent_again(mock: MockCloud) -> None:
    clock = ManualClock()
    publisher = _publisher(mock, clock)

    publisher.publish(_snapshot(_tool("deploy")))
    await publisher.flush()
    publisher.publish(_snapshot(_tool("deploy")))
    await publisher.flush()
    publisher.publish(_snapshot(_tool("deploy"), _tool("audit")))
    await publisher.flush()

    assert [len(snapshot["tools"]) for snapshot in mock.received_snapshots] == [1, 2]
    await publisher.close()


async def test_the_debounce_waits_for_a_quiet_period(mock: MockCloud) -> None:
    clock = ManualClock()
    publisher = _publisher(mock, clock, debounce_ms=500)

    publisher.publish(_snapshot(_tool("deploy")))
    await clock.advance(0.25)
    publisher.publish(_snapshot(_tool("deploy"), _tool("audit")))
    await clock.advance(0.25)
    assert mock.received_snapshots == []
    await clock.advance(0.25)

    assert [len(snapshot["tools"]) for snapshot in mock.received_snapshots] == [2]
    await publisher.close()


async def test_a_durable_snapshot_reconciles_every_five_minutes(mock: MockCloud) -> None:
    clock = ManualClock()
    publisher = _publisher(mock, clock)

    publisher.publish(_snapshot(_tool("deploy")))
    await publisher.flush()
    await clock.advance(299)
    assert len(mock.received_snapshots) == 1
    await clock.advance(1)

    assert len(mock.received_snapshots) == 2
    assert mock.received_snapshots[1] == mock.received_snapshots[0]
    await publisher.close()


async def test_a_400_drops_the_snapshot_and_reports_it_one_time(mock: MockCloud) -> None:
    clock = ManualClock()
    rejections = _Rejections()
    publisher = _publisher(mock, clock, on_rejected=rejections)
    mock.script_responses("PUT", "/catalog/snapshot", 400)

    publisher.publish(_snapshot(_tool("deploy")))
    await publisher.flush()
    await clock.advance(600)

    assert len(mock.received_snapshots) == 1
    assert len(rejections.calls) == 1
    assert [item.reason for item in rejections.calls[0]] == [
        "catalog snapshot for service-a rejected with HTTP 400"
    ]
    await publisher.close()


async def test_a_transient_failure_stays_queued_and_retries_with_backoff(mock: MockCloud) -> None:
    clock = ManualClock()
    status = DeliveryStatus(warn_on_failure=False, clock=clock.now)
    publisher = _publisher(
        mock,
        clock,
        delivery_status=status,
        retry=RetryOptions(max_attempts=1, initial_backoff_ms=1_000, max_backoff_ms=60_000),
    )
    mock.script_responses("PUT", "/catalog/snapshot", 503, 503)

    publisher.publish(_snapshot(_tool("deploy")))
    await publisher.flush()
    assert status.snapshot()["overall"] == "degraded"
    await clock.advance(1)
    assert len(mock.received_snapshots) == 2
    # The backoff keeps growing across failing cycles.
    await clock.advance(1.5)
    assert len(mock.received_snapshots) == 2
    await clock.advance(0.5)

    assert len(mock.received_snapshots) == 3
    assert len(mock.catalog_snapshots) == 1
    assert status.snapshot()["snapshots"]["service-a"]["last_outcome"] == {
        "kind": "ok",
        "status": 200,
        "message": None,
    }
    await publisher.close()


async def test_a_429_retries_after_its_retry_after(mock: MockCloud) -> None:
    clock = ManualClock()
    publisher = _publisher(mock, clock, retry=RetryOptions(max_attempts=1))
    mock.script_responses(
        "PUT",
        "/catalog/snapshot",
        json_response({"error": "rate_limited"}, status=429, headers={"Retry-After": "7"}),
    )

    publisher.publish(_snapshot(_tool("deploy")))
    await publisher.flush()
    await clock.advance(6.5)
    assert len(mock.received_snapshots) == 1
    await clock.advance(0.5)

    assert len(mock.catalog_snapshots) == 1
    await publisher.close()


async def test_a_deferred_snapshot_stays_pending_and_retries_slowly(mock: MockCloud) -> None:
    clock = ManualClock()
    status = DeliveryStatus(warn_on_failure=False, clock=clock.now)
    publisher = _publisher(mock, clock, delivery_status=status)
    mock.script_responses("PUT", "/catalog/snapshot", json_response({"synced": False}, status=202))

    publisher.publish(_snapshot(_tool("deploy")))
    await publisher.flush()
    assert status.snapshot()["snapshots"]["service-a"]["pending_since"] is not None
    await clock.advance(30)

    assert len(mock.catalog_snapshots) == 1
    assert status.snapshot()["snapshots"]["service-a"]["pending_since"] is None
    await publisher.close()


async def test_client_side_limits_omit_tools_and_report_them(mock: MockCloud) -> None:
    clock = ManualClock()
    rejections = _Rejections()
    publisher = _publisher(mock, clock, on_rejected=rejections)

    publisher.publish(
        _snapshot(
            _tool("deploy", name="   "),
            _tool("audit"),
            _tool(" audit "),
            _tool("x" * 600),
            *(_tool(f"bulk-{index:04d}") for index in range(5_000)),
        )
    )
    await publisher.flush()

    [snapshot] = mock.catalog_snapshots
    assert len(snapshot["tools"]) == 5_000
    assert snapshot["tools"][1]["id"] == "x" * 512
    reasons = [item.reason for item in rejections.calls[0]]
    assert reasons[0] == "catalog snapshot tool name is empty"
    assert reasons[1] == "catalog snapshot tool id is duplicated after normalization: audit"
    assert reasons[2:] == ["catalog snapshot tool limit is 5000"] * 2
    await publisher.close()


async def test_an_unserializable_snapshot_is_reported_and_never_raises(mock: MockCloud) -> None:
    clock = ManualClock()
    rejections = _Rejections()
    publisher = _publisher(mock, clock, on_rejected=rejections)

    publisher.publish(_snapshot(_tool("deploy", metadata={"bad": object()})))
    publisher.publish({"source_id": "service-a"})  # type: ignore[typeddict-item]
    await publisher.flush()

    assert mock.received_snapshots == [{"source_id": "service-a", "tools": []}]
    assert rejections.calls[0][0].reason == "catalog snapshot tool cannot be serialized"
    assert "cannot be prepared" in rejections.calls[1][0].reason
    await publisher.close()
