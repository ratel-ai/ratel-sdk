"""The SDK-side shapes that runtime delivery reads: events and catalog snapshots.

The keys are snake_case, like `ratel_ai.RuntimeCatalog.snapshot`. The publishers
map them to the wire names of ``PUT /catalog/snapshot``.
"""

from __future__ import annotations

from typing import Any, TypedDict

__all__ = ["RuntimeCatalogSnapshot", "RuntimeCatalogToolDefinition", "RuntimeEvent"]

#: One runtime event envelope (ADR-0020), as `ratel_ai.RuntimeEvents` delivers it.
RuntimeEvent = dict[str, Any]


class _ToolRequired(TypedDict):
    id: str
    name: str


class RuntimeCatalogToolDefinition(_ToolRequired, total=False):
    """One tool of a catalog snapshot.

    ``experimental_searchable_description`` is experimental (ADR-0021): the text
    that Platform matches prompts against. It reaches the wire as
    ``searchable_description``. Omitted means "match on the description".
    """

    description: str | None
    experimental_searchable_description: str | None
    input_schema: dict[str, Any] | None
    output_schema: dict[str, Any] | None
    metadata: dict[str, Any] | None


class _SnapshotRequired(TypedDict):
    source_id: str
    tools: list[RuntimeCatalogToolDefinition]


class RuntimeCatalogSnapshot(_SnapshotRequired, total=False):
    """A complete catalog snapshot of one source.

    ``skills`` is accepted for parity with the core SDK snapshot. It is not part of
    the Platform tool inventory.
    """

    skills: list[Any]
