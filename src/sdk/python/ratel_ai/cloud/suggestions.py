"""The review surface for machine-drafted catalog proposals (``/suggestions*``)."""

from __future__ import annotations

from typing import cast

from .transport import CloudHttpClient
from .types import (
    CloudSuggestion,
    GenerateSuggestionsResult,
    ListSuggestionsResult,
    SuggestionStatus,
    SuggestionType,
)

__all__ = ["GENERATE_TIMEOUT", "SuggestionsClient"]

#: Generation can run signal detection and model drafting synchronously (seconds).
GENERATE_TIMEOUT = 300.0


class SuggestionsClient:
    """List, generate, approve and reject suggestions (Bearer project key)."""

    def __init__(self, http: CloudHttpClient) -> None:
        """Create the client on a shared `CloudHttpClient`."""
        self._http = http

    async def get(self, suggestion_id: str) -> CloudSuggestion:
        """Read one proposal, e.g. the ``suggestionId`` that a suggest job produced.

        Args:
            suggestion_id: The suggestion id.

        Returns:
            The suggestion.
        """
        return await self._suggestion("GET", f"/suggestions/{suggestion_id}")

    async def list(
        self,
        *,
        status: SuggestionStatus | None = None,
        type: SuggestionType | None = None,
        limit: int | None = None,
        end_user_id: str | None = None,
    ) -> ListSuggestionsResult:
        """List proposals.

        Args:
            status: Only proposals in this status.
            type: Only proposals of this type.
            limit: Page size, 1 to 100. The server default is 50.
            end_user_id: Only the proposals scoped to this opaque end user.

        Returns:
            The ``GET /suggestions`` body.
        """
        query = {
            "status": status,
            "type": type,
            "limit": None if limit is None else str(limit),
            "endUserId": end_user_id,
        }
        return cast(
            ListSuggestionsResult, await self._http.json("GET", "/suggestions", query=query)
        )

    async def generate(self) -> GenerateSuggestionsResult:
        """Start a generation run.

        The run drains synchronously on the server: when this returns, `list`
        already sees the results. ``coalesced`` is `True` when an in-flight run
        absorbed this request.

        Returns:
            The job id and the coalesced flag.
        """
        return cast(
            GenerateSuggestionsResult,
            await self._http.json("POST", "/suggestions/generate", timeout=GENERATE_TIMEOUT),
        )

    async def approve(self, suggestion_id: str) -> CloudSuggestion:
        """Approve a pending proposal.

        An ``edit_skill`` applies through the version guard. A ``new_skill`` lands
        as a draft skill (with the ``endUserId`` of the proposal).

        Args:
            suggestion_id: The suggestion id.

        Returns:
            The approved suggestion.

        Raises:
            CloudError: Code ``conflict`` with reason ``not_pending``,
                ``version_conflict`` or ``name_conflict`` on a state race.
        """
        return await self._suggestion("POST", f"/suggestions/{suggestion_id}/approve")

    async def reject(self, suggestion_id: str) -> CloudSuggestion:
        """Reject a pending proposal.

        Args:
            suggestion_id: The suggestion id.

        Returns:
            The rejected suggestion.
        """
        return await self._suggestion("POST", f"/suggestions/{suggestion_id}/reject")

    async def _suggestion(self, method: str, path: str) -> CloudSuggestion:
        response = await self._http.json(method, path)
        return cast(CloudSuggestion, response["suggestion"])
