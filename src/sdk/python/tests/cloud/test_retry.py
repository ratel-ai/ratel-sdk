"""Retry with exponential full-jitter backoff and ``Retry-After``: the port of retry.test.ts."""

from __future__ import annotations

from email.utils import formatdate

from ratel_ai.cloud import CloudError, CloudResponse
from ratel_ai.cloud.runtime import RetryOptions
from ratel_ai.cloud.runtime.retry import create_retry_config, parse_retry_after, request_with_retry


class _Responder:
    def __init__(self, *outcomes: int | Exception, headers: dict[str, str] | None = None) -> None:
        self.outcomes = list(outcomes)
        self.headers = headers or {}
        self.calls = 0

    async def __call__(self) -> CloudResponse:
        self.calls += 1
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, Exception):
            raise outcome
        return CloudResponse(status=outcome, headers=self.headers)


class _Sleeps:
    def __init__(self) -> None:
        self.seconds: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.seconds.append(seconds)


async def test_returns_redirects_without_retrying() -> None:
    request = _Responder(302)

    response = await request_with_retry(
        request, create_retry_config(RetryOptions(max_attempts=3)), _Sleeps()
    )

    assert response is not None and response.status == 302
    assert request.calls == 1


async def test_returns_a_terminal_reject_without_retrying() -> None:
    request = _Responder(400)

    response = await request_with_retry(request, create_retry_config(None), _Sleeps())

    assert response is not None and response.status == 400
    assert request.calls == 1


async def test_applies_full_jitter_after_clamping_exponential_backoff() -> None:
    sleeps = _Sleeps()
    samples: list[int] = []

    def random() -> float:
        samples.append(1)
        return 0.5

    retry = create_retry_config(
        RetryOptions(max_attempts=3, initial_backoff_ms=100, max_backoff_ms=150, random=random)
    )
    network = CloudError("offline", status=None, code="network_error")

    response = await request_with_retry(_Responder(network), retry, sleeps)

    assert response is None
    assert sleeps.seconds == [0.05, 0.075]
    assert len(samples) == 2


async def test_retries_transient_statuses_and_returns_the_success() -> None:
    request = _Responder(503, 429, 200)
    sleeps = _Sleeps()

    response = await request_with_retry(
        request, create_retry_config(RetryOptions(random=lambda: 1.0)), sleeps
    )

    assert response is not None and response.status == 200
    assert request.calls == 3
    assert sleeps.seconds == [0.25, 0.5]


async def test_honors_retry_after_seconds_without_jitter() -> None:
    request = _Responder(429, 200, headers={"retry-after": "2"})
    sleeps = _Sleeps()

    await request_with_retry(request, create_retry_config(RetryOptions(random=lambda: 0.0)), sleeps)

    assert sleeps.seconds == [2.0]


async def test_caps_retry_after_at_the_maximum_backoff() -> None:
    request = _Responder(503, 200, headers={"retry-after": "120"})
    sleeps = _Sleeps()

    await request_with_retry(request, create_retry_config(RetryOptions()), sleeps)

    assert sleeps.seconds == [30.0]


def test_parses_retry_after_as_seconds_or_an_http_date() -> None:
    now_ms = 1_750_000_000_000.0

    assert parse_retry_after("1.5", now_ms) == 1_500
    assert parse_retry_after(formatdate(1_750_000_004, usegmt=True), now_ms) == 4_000
    assert parse_retry_after(formatdate(1_749_999_000, usegmt=True), now_ms) == 0
    assert parse_retry_after("soon", now_ms) is None
    assert parse_retry_after(None, now_ms) is None
