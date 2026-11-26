"""The bridge from the Cloud overrides client to the core SDK's overlay seam.

The port of ``cloud-definitions.ts``. `CloudDefinitionsSource` pulls
``GET /runtime-catalog/overrides`` with ``If-None-Match`` and answers in the
shape that `ratel_ai.RuntimeCatalog.experimental_attach_definition_overrides`
validates.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..runtime_catalog import RuntimeCatalogClient
from ..types import RuntimeCatalogOverridesFresh

__all__ = ["CloudDefinitionsSource"]


class CloudDefinitionsSource:
    """An overlay source (``ExperimentalDefinitionOverlaySource``) backed by Ratel Cloud."""

    def __init__(self, client: RuntimeCatalogClient) -> None:
        """Create the source on a runtime-catalog client.

        Args:
            client: The client that pulls the overrides.
        """
        self._client = client

    async def fetch(self, if_none_match: str | None = None) -> Mapping[str, Any]:
        """Pull the overlay, conditionally when ``if_none_match`` is set.

        Args:
            if_none_match: The last accepted strong ETag.

        Returns:
            ``{"status": 304}``, or ``{"status": 200, "etag": ..., "body": {"overrides": [...]}}``
            with the overrides in the snake_case shape of the core SDK.

        Raises:
            CloudError: The request failed.
            ValueError: A fresh answer has no ETag.
        """
        result = await self._client.list_overrides(if_none_match=if_none_match)
        if not isinstance(result, RuntimeCatalogOverridesFresh):
            return {"status": 304}
        if result.etag is None:
            raise ValueError("Ratel Cloud definitions response is missing a strong ETag")
        return {
            "status": 200,
            "etag": result.etag,
            "body": {
                "overrides": [
                    {
                        "kind": override["kind"],
                        "entry_id": override["entryId"],
                        "searchable_description": override["searchableDescription"],
                    }
                    for override in result.overrides
                ]
            },
        }
