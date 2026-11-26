from __future__ import annotations

import json
from typing import Any

import pytest

from ratel_ai.cloud import (
    CloudError,
    CloudHttpClient,
    JobsClient,
    RatelCloudClient,
    TransportRequest,
    TransportResponse,
    resolve_cloud_config,
)


class JobSequence:
    """A transport that serves each job body once, then repeats the last one."""

    def __init__(self, bodies: list[dict[str, Any]], status: int = 200) -> None:
        self.bodies = bodies
        self.status = status
        self.polls = 0

    async def send(self, request: TransportRequest) -> TransportResponse:
        body = {
            "id": "job_1",
            "kind": "suggest_skill",
            "status": "queued",
            "result": None,
            "error": None,
            **self.bodies[min(self.polls, len(self.bodies) - 1)],
        }
        self.polls += 1
        return TransportResponse(self.status, {}, json.dumps(body).encode())


class FakeClock:
    """A clock that only moves when the code under test sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def jobs_client(transport: Any, clock: FakeClock) -> JobsClient:
    config = resolve_cloud_config(api_key="rtl_test", env={})
    return JobsClient(
        CloudHttpClient(config, transport=transport), clock=clock.monotonic, sleep=clock.sleep
    )


async def test_get_reads_a_job_once() -> None:
    client = RatelCloudClient(
        api_key="rtl_test",
        transport=JobSequence([{"status": "done", "result": {"suggestionId": "sug_1"}}]),
    )
    job = await client.jobs.get("job_1")
    assert job["status"] == "done"
    assert job["result"] == {"suggestionId": "sug_1"}


async def test_wait_for_polls_through_queued_and_running_until_done() -> None:
    clock = FakeClock()
    transport = JobSequence(
        [
            {"status": "queued"},
            {"status": "running"},
            {"status": "done", "result": {"suggestionId": "sug_1"}},
        ]
    )
    job = await jobs_client(transport, clock).wait_for("job_1", interval=0.1)
    assert job["status"] == "done"
    assert job["result"] == {"suggestionId": "sug_1"}
    assert transport.polls == 3
    assert clock.sleeps == [0.1, 0.1]


async def test_wait_for_returns_an_error_job_instead_of_raising() -> None:
    clock = FakeClock()
    transport = JobSequence([{"status": "error", "error": "drafting model unavailable"}])
    job = await jobs_client(transport, clock).wait_for("job_1")
    assert job["status"] == "error"
    assert job["error"] == "drafting model unavailable"
    assert clock.sleeps == []


async def test_wait_for_raises_a_poll_timeout_when_the_job_never_settles() -> None:
    clock = FakeClock()
    transport = JobSequence([{"status": "running"}])
    with pytest.raises(CloudError) as info:
        await jobs_client(transport, clock).wait_for("job_1", interval=0.1, timeout=0.3)
    assert info.value.status is None
    assert info.value.code == "unavailable"
    assert info.value.reason == "poll_timeout"
    assert "job_1" in str(info.value)
    assert clock.now == pytest.approx(0.3)


async def test_wait_for_uses_one_second_polls_and_a_two_minute_timeout_by_default() -> None:
    clock = FakeClock()
    with pytest.raises(CloudError):
        await jobs_client(JobSequence([{"status": "running"}]), clock).wait_for("job_1")
    assert set(clock.sleeps) == {1.0}
    assert clock.now == pytest.approx(120.0)


async def test_wait_for_propagates_a_poll_error() -> None:
    clock = FakeClock()
    transport = JobSequence([{"error": "not_found"}], status=404)
    with pytest.raises(CloudError) as info:
        await jobs_client(transport, clock).wait_for("job_missing")
    assert info.value.code == "not_found"
