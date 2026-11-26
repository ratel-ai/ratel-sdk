"""The pull surface for operator-authored runtime catalog overrides (Bearer project key)."""

from __future__ import annotations

from typing import overload

from .transport import CloudHttpClient
from .types import (
    RuntimeCatalogOverridesFresh,
    RuntimeCatalogOverridesNotModified,
    RuntimeCatalogOverridesResult,
)

__all__ = ["RuntimeCatalogClient"]


class RuntimeCatalogClient:
    """Read the runtime-catalog overlay with a conditional GET."""

    def __init__(self, http: CloudHttpClient) -> None:
        """Create the client on a shared `CloudHttpClient`."""
        self._http = http

    @overload
    async def list_overrides(self) -> RuntimeCatalogOverridesFresh: ...

    @overload
    async def list_overrides(
        self, *, if_none_match: str | None
    ) -> RuntimeCatalogOverridesResult: ...

    async def list_overrides(
        self, *, if_none_match: str | None = None
    ) -> RuntimeCatalogOverridesResult:
        """Read the overrides, or learn that the cached copy is still current.

        Args:
            if_none_match: The ``etag`` of the last fresh result. Sent as
                ``If-None-Match``.

        Returns:
            `RuntimeCatalogOverridesFresh` with the overrides and their ``etag``,
            or `RuntimeCatalogOverridesNotModified` on a 304 (keep the cached
            overrides).
        """
        headers = {"if-none-match": if_none_match} if if_none_match else {}
        response = await self._http.request(
            "GET", "/runtime-catalog/overrides", headers=headers, accept_statuses=(304,)
        )
        etag = response.headers.get("etag")
        if response.status == 304:
            return RuntimeCatalogOverridesNotModified(etag=etag)
        return RuntimeCatalogOverridesFresh(etag=etag, overrides=response.json["overrides"])
