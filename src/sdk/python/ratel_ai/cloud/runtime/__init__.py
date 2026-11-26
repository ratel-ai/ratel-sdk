"""Fail-open delivery of runtime facts to Ratel Cloud: events, catalog snapshots, definitions.

Attach a runtime one time, from a running event loop::

    from ratel_ai import ratel
    from ratel_ai.cloud.runtime import attach

    runtime = ratel()
    cloud_runtime = attach(runtime, use_cloud_definitions=True)
    ...
    await cloud_runtime.close()
"""

from __future__ import annotations

from .allowlist import RUNTIME_EVENT_TYPES, is_remotely_publishable
from .attach import (
    CloudDefinitionsRuntimeAttachment,
    RatelRuntime,
    RatelRuntimeCatalog,
    RatelRuntimeEvents,
    RuntimeAttachment,
    RuntimeEventSubscription,
    attach,
    cloud_definitions_ready,
)
from .cloud_definitions import CloudDefinitionsSource
from .delivery_status import (
    DeliveryKind,
    DeliveryOverall,
    DeliveryResult,
    DeliveryStatus,
    EventsDeliveryStatus,
    RuntimeDeliveryStatus,
    SnapshotDeliveryStatus,
    StatusChangeHandler,
    classify_delivery,
)
from .hash import hash_catalog_snapshot
from .publisher import (
    EVENTS_ENV,
    RUNTIME_EVENT_BATCH_MAX_BYTES,
    RUNTIME_EVENT_BATCH_MAX_EVENTS,
    RUNTIME_EVENT_MAX_BYTES,
    RejectionHandler,
    RuntimeEventRejection,
    RuntimeEventsPublisher,
)
from .retry import Clock, RetryOptions, Sleep
from .snapshots import (
    CATALOG_SNAPSHOT_MAX_BYTES,
    CATALOG_SNAPSHOT_MAX_DESCRIPTION_LENGTH,
    CATALOG_SNAPSHOT_MAX_ID_OR_NAME_LENGTH,
    CATALOG_SNAPSHOT_MAX_TOOLS,
    CatalogSnapshotsPublisher,
    normalize_source_id,
)
from .types import RuntimeCatalogSnapshot, RuntimeCatalogToolDefinition, RuntimeEvent

__all__ = [
    "CATALOG_SNAPSHOT_MAX_BYTES",
    "CATALOG_SNAPSHOT_MAX_DESCRIPTION_LENGTH",
    "CATALOG_SNAPSHOT_MAX_ID_OR_NAME_LENGTH",
    "CATALOG_SNAPSHOT_MAX_TOOLS",
    "EVENTS_ENV",
    "RUNTIME_EVENT_BATCH_MAX_BYTES",
    "RUNTIME_EVENT_BATCH_MAX_EVENTS",
    "RUNTIME_EVENT_MAX_BYTES",
    "RUNTIME_EVENT_TYPES",
    "CatalogSnapshotsPublisher",
    "Clock",
    "CloudDefinitionsRuntimeAttachment",
    "CloudDefinitionsSource",
    "DeliveryKind",
    "DeliveryOverall",
    "DeliveryResult",
    "DeliveryStatus",
    "EventsDeliveryStatus",
    "RatelRuntime",
    "RatelRuntimeCatalog",
    "RatelRuntimeEvents",
    "RejectionHandler",
    "RetryOptions",
    "RuntimeAttachment",
    "RuntimeCatalogSnapshot",
    "RuntimeCatalogToolDefinition",
    "RuntimeDeliveryStatus",
    "RuntimeEvent",
    "RuntimeEventRejection",
    "RuntimeEventSubscription",
    "RuntimeEventsPublisher",
    "SnapshotDeliveryStatus",
    "Sleep",
    "StatusChangeHandler",
    "attach",
    "classify_delivery",
    "cloud_definitions_ready",
    "hash_catalog_snapshot",
    "is_remotely_publishable",
    "normalize_source_id",
]
