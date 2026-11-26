"""The intent surface (``/intents*``, Bearer project key)."""

from __future__ import annotations

from typing import cast

from .transport import CloudHttpClient
from .types import AnalyzeInput, AnalyzeResult, ListIntentsResult, SuggestResult

__all__ = ["ANALYZE_TIMEOUT", "IntentsClient"]

#: Analysis runs extraction and coverage scoring on the server (seconds).
ANALYZE_TIMEOUT = 120.0


class IntentsClient:
    """Analyze conversations, read the intent ledger and start drafting jobs.

    The flow is async: `analyze` extracts intents and scores coverage but does
    not draft skills. Call `suggest` with an intent id to start a drafting job,
    poll it with `JobsClient`, then read the proposal with `SuggestionsClient.get`.
    `ratel_ai.cloud.testing.MockCloud` serves the same wire contract for tests.
    """

    def __init__(self, http: CloudHttpClient) -> None:
        """Create the client on a shared `CloudHttpClient`."""
        self._http = http

    async def analyze(self, analysis: AnalyzeInput) -> AnalyzeResult:
        """Extract the intents of a conversation and score each against the catalog.

        Analyzing an unchanged conversation against an unchanged catalog again is
        a server-side cache hit (``cached: True``). Put ``"noCache": True`` in
        ``analysis`` to force a live extraction (tests, debugging): it also
        replaces the stored run.

        Args:
            analysis: The ``POST /intents/analyze`` body, with wire keys.

        Returns:
            The run, its catalog version and the extracted intents.

        Raises:
            CloudError: Code ``unavailable`` (reason ``extractor_not_configured``
                or ``extractor_unavailable``) when the server cannot extract.
                Try again later: it is not an empty result.
        """
        return cast(
            AnalyzeResult,
            await self._http.json(
                "POST", "/intents/analyze", body=analysis, timeout=ANALYZE_TIMEOUT
            ),
        )

    async def list(self, *, page: int | None = None) -> ListIntentsResult:
        """Read the recurring asks of the project (the intent ledger), most frequent first.

        Args:
            page: Zero-based page index. The server serves 50 per page.

        Returns:
            The ``GET /intents`` page.
        """
        query = {"page": None if page is None else str(page)}
        return cast(ListIntentsResult, await self._http.json("GET", "/intents", query=query))

    async def suggest(self, intent_id: str) -> SuggestResult:
        """Start a drafting job for one intent id (from `analyze` or `list`).

        It returns at once with a ``jobId``. Poll it with `JobsClient.wait_for`.
        When the job is ``done``, its result carries the ``suggestionId``.

        Args:
            intent_id: The intent id.

        Returns:
            The job id to poll.

        Raises:
            CloudError: Code ``not_found`` when the intent is not in the project of the key.
        """
        return cast(SuggestResult, await self._http.json("POST", f"/intents/{intent_id}/suggest"))
