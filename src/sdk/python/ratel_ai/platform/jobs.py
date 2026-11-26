"""Poll async jobs (``GET /jobs/{id}``, Bearer project key)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable
from typing import Callable, cast

from .errors import PlatformError
from .transport import PlatformHttpClient
from .types import Job

__all__ = ["DEFAULT_POLL_INTERVAL", "DEFAULT_WAIT_TIMEOUT", "JobsClient"]

#: Default poll interval of `JobsClient.wait_for`, in seconds.
DEFAULT_POLL_INTERVAL = 1.0

#: Default time limit of `JobsClient.wait_for`, in seconds.
DEFAULT_WAIT_TIMEOUT = 120.0


class JobsClient:
    """Read and wait for async jobs.

    The drafting flow starts a job with `IntentsClient.suggest`. Poll it here
    until it is terminal. `ratel_ai.platform.testing.MockPlatform` serves the same wire
    contract for tests.
    """

    def __init__(
        self,
        http: PlatformHttpClient,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Create the client on a shared `PlatformHttpClient`.

        Args:
            http: The HTTP client.
            clock: A monotonic clock in seconds. Tests inject a fake one.
            sleep: An async sleep in seconds. Tests inject a fake one.
        """
        self._http = http
        self._clock = clock
        self._sleep = sleep

    async def get(self, job_id: str) -> Job:
        """Read a job once.

        Args:
            job_id: The job id.

        Returns:
            The job.
        """
        return cast(Job, await self._http.json("GET", f"/jobs/{job_id}"))

    async def wait_for(
        self,
        job_id: str,
        *,
        interval: float = DEFAULT_POLL_INTERVAL,
        timeout: float = DEFAULT_WAIT_TIMEOUT,
    ) -> Job:
        """Poll until the job is terminal (``done`` or ``error``) or the time is up.

        A job with status ``error`` is returned, not raised: read ``job["error"]``.

        Args:
            job_id: The job id.
            interval: Seconds between polls.
            timeout: Seconds to wait before giving up.

        Returns:
            The terminal job.

        Raises:
            PlatformError: A poll failed, or the job did not settle in time (code
                ``unavailable``, reason ``poll_timeout``).
        """
        deadline = self._clock() + timeout
        while True:
            job = await self.get(job_id)
            if job["status"] in ("done", "error"):
                return job
            if self._clock() >= deadline:
                raise PlatformError(
                    f"Job {job_id} did not finish within {timeout}s",
                    status=None,
                    code="unavailable",
                    reason="poll_timeout",
                )
            await self._sleep(interval)
