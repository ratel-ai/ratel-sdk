"""Retry, backoff and the injectable timers of the runtime publishers.

The port of ``retry.ts``. Transient failures (no response, 408, 429, 5xx) retry
with exponential full-jitter backoff, and a ``Retry-After`` header replaces the
backoff. Every delay goes through an injected ``sleep`` and every timestamp
through an injected ``clock``, so tests run with no real waits.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import random as _random
import time
from collections.abc import Awaitable, Coroutine
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Callable

from ..transport import PlatformResponse

__all__ = [
    "BackgroundTasks",
    "Clock",
    "RetryConfig",
    "RetryOptions",
    "Sleep",
    "create_retry_config",
    "non_negative",
    "parse_retry_after",
    "positive_integer",
    "request_with_retry",
    "start_timer",
]

#: Waits the given number of seconds, like `asyncio.sleep`.
Sleep = Callable[[float], Awaitable[None]]

#: Returns the current time in seconds since the epoch, like `time.time`.
Clock = Callable[[], float]

#: Returns a number in [0, 1), like `random.random`.
Random = Callable[[], float]

_DEFAULT_MAX_ATTEMPTS = 3
_DEFAULT_INITIAL_BACKOFF_MS = 250.0
_DEFAULT_MAX_BACKOFF_MS = 30_000.0


@dataclass(frozen=True)
class RetryOptions:
    """How a publisher retries one request.

    Attributes:
        max_attempts: Attempts per request, the first one included. Default 3.
        initial_backoff_ms: The backoff before the second attempt. It doubles
            for each later attempt. Default 250 ms.
        max_backoff_ms: The cap of each delay, ``Retry-After`` included. Default 30 s.
        random: The source of the full jitter. Defaults to `random.random`.
    """

    max_attempts: int | None = None
    initial_backoff_ms: float | None = None
    max_backoff_ms: float | None = None
    random: Random | None = None


@dataclass(frozen=True)
class RetryConfig:
    """`RetryOptions` with every invalid or missing value set to its default."""

    max_attempts: int
    initial_backoff_ms: float
    max_backoff_ms: float
    random: Random


async def request_with_retry(
    request: Callable[[], Awaitable[PlatformResponse]],
    retry: RetryConfig,
    sleep: Sleep,
    clock: Clock = time.time,
) -> PlatformResponse | None:
    """Send a request, and send it again after transient failures.

    Args:
        request: Sends one attempt. It raises when no response arrived.
        retry: The retry settings.
        sleep: The timer for the delays between attempts.
        clock: The clock that resolves an HTTP-date ``Retry-After``.

    Returns:
        The first success or terminal response, the last transient response
        after the last attempt, or `None` when no attempt got a response.
    """
    response: PlatformResponse | None = None
    for attempt in range(retry.max_attempts):
        retry_after_ms: float | None = None
        try:
            response = await request()
            if _is_success(response.status) or _is_terminal(response.status):
                return response
            retry_after_ms = parse_retry_after(response.headers.get("retry-after"), clock() * 1_000)
        except Exception:
            # Network and timeout failures retry; exhaustion stays fail-open.
            pass
        if attempt + 1 < retry.max_attempts:
            exponential = retry.initial_backoff_ms * 2.0**attempt
            capped = min(
                retry_after_ms if retry_after_ms is not None else exponential, retry.max_backoff_ms
            )
            delay_ms = capped if retry_after_ms is not None else _full_jitter(capped, retry.random)
            with contextlib.suppress(Exception):
                # An injected timer cannot make delivery fail closed.
                await sleep(delay_ms / 1_000)
    return response


def create_retry_config(options: RetryOptions | None) -> RetryConfig:
    """Resolve `RetryOptions` into a `RetryConfig`.

    Args:
        options: The options, or `None` for every default.

    Returns:
        The resolved settings.
    """
    resolved = options if options is not None else RetryOptions()
    return RetryConfig(
        max_attempts=positive_integer(resolved.max_attempts, _DEFAULT_MAX_ATTEMPTS),
        initial_backoff_ms=non_negative(resolved.initial_backoff_ms, _DEFAULT_INITIAL_BACKOFF_MS),
        max_backoff_ms=non_negative(resolved.max_backoff_ms, _DEFAULT_MAX_BACKOFF_MS),
        random=resolved.random if resolved.random is not None else _random.random,
    )


def positive_integer(value: int | None, fallback: int) -> int:
    """Return ``value`` when it is an integer above zero, else ``fallback``."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return fallback
    return value


def non_negative(value: float | None, fallback: float) -> float:
    """Return ``value`` when it is a finite number of zero or more, else ``fallback``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return fallback
    return float(value) if math.isfinite(value) and value >= 0 else fallback


def parse_retry_after(value: str | None, now_ms: float) -> float | None:
    """Parse a ``Retry-After`` header into milliseconds.

    Args:
        value: The header value: seconds, or an HTTP date.
        now_ms: The current time in milliseconds since the epoch.

    Returns:
        The delay in milliseconds (zero for a past date), or `None` when the
        header is missing or invalid.
    """
    if value is None:
        return None
    with contextlib.suppress(ValueError):
        seconds = float(value)
        return seconds * 1_000 if math.isfinite(seconds) and seconds >= 0 else None
    try:
        timestamp_ms = parsedate_to_datetime(value).timestamp() * 1_000
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    return max(0.0, timestamp_ms - now_ms)


class BackgroundTasks:
    """The fire-and-forget tasks of one publisher, so that ``close()`` can settle them."""

    def __init__(self) -> None:
        """Start with no tasks."""
        self._tasks: set[asyncio.Task[None]] = set()

    def spawn(self, work: Coroutine[Any, Any, None]) -> asyncio.Task[None] | None:
        """Run ``work`` as a task on the running loop.

        Args:
            work: The coroutine.

        Returns:
            The task, or `None` when no event loop runs (``work`` is then closed).
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            work.close()
            return None
        task = loop.create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def wait(self) -> None:
        """Wait until every task, also the ones started meanwhile, is done."""
        while self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)


def start_timer(
    delay_ms: float, callback: Callable[[], None], sleep: Sleep, tasks: BackgroundTasks
) -> asyncio.Task[None] | None:
    """Call ``callback`` after ``delay_ms``, on a task of ``tasks``.

    Cancel the returned task to stop the timer. ``callback`` runs synchronously
    after the sleep, so a cancel after it fired has no effect.

    Args:
        delay_ms: The delay in milliseconds.
        callback: The function to call. Its exceptions are suppressed.
        sleep: The timer.
        tasks: The task set that owns the timer.

    Returns:
        The timer task, or `None` when no event loop runs.
    """

    async def fire() -> None:
        await sleep(delay_ms / 1_000)
        with contextlib.suppress(Exception):
            callback()

    return tasks.spawn(fire())


def _is_success(status: int) -> bool:
    return 200 <= status < 300


def _is_retryable(status: int) -> bool:
    return status in (408, 429) or status >= 500


def _is_terminal(status: int) -> bool:
    return 300 <= status < 400 or not _is_retryable(status)


def _full_jitter(delay_ms: float, random: Random) -> float:
    sample = random()
    ratio = min(max(sample, 0.0), 1.0) if math.isfinite(sample) else 0.0
    return delay_ms * ratio
