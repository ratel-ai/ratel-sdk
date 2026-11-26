"""Fail-open delivery of runtime facts to Ratel Platform: events, catalog snapshots, definitions.

Attach a runtime one time, from a running event loop::

    from ratel_ai import ratel
    from ratel_ai.platform.runtime import attach

    runtime = ratel()
    platform_runtime = attach(runtime, use_platform_definitions=True)
    ...
    await platform_runtime.close()
"""

from __future__ import annotations

from .allowlist import RUNTIME_EVENT_TYPES, is_remotely_publishable
from .attach import (
    PlatformDefinitionsRuntimeAttachment,
    RatelRuntime,
    RatelRuntimeCatalog,
    RatelRuntimeEvents,
    RuntimeAttachment,
    RuntimeEventSubscription,
    attach,
    platform_definitions_ready,
)
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
from .intent_graph_sync import (
    INTENT_GRAPH_ENV,
    IntentGraphSync,
    IntentGraphSyncCatalog,
    IntentGraphSyncError,
    IntentGraphSyncErrorKind,
    IntentGraphSyncMode,
    IntentGraphSyncStatus,
    attach_intent_graph_sync,
)
from .platform_definitions import PlatformDefinitionsSource
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
from .tool_picker import RatelPlatform, ToolPicker, ToolPickerMode, ratel_platform
from .types import RuntimeCatalogSnapshot, RuntimeCatalogToolDefinition, RuntimeEvent

__all__ = [
    "CATALOG_SNAPSHOT_MAX_BYTES",
    "CATALOG_SNAPSHOT_MAX_DESCRIPTION_LENGTH",
    "CATALOG_SNAPSHOT_MAX_ID_OR_NAME_LENGTH",
    "CATALOG_SNAPSHOT_MAX_TOOLS",
    "EVENTS_ENV",
    "INTENT_GRAPH_ENV",
    "RUNTIME_EVENT_BATCH_MAX_BYTES",
    "RUNTIME_EVENT_BATCH_MAX_EVENTS",
    "RUNTIME_EVENT_MAX_BYTES",
    "RUNTIME_EVENT_TYPES",
    "CatalogSnapshotsPublisher",
    "Clock",
    "PlatformDefinitionsRuntimeAttachment",
    "PlatformDefinitionsSource",
    "DeliveryKind",
    "DeliveryOverall",
    "DeliveryResult",
    "DeliveryStatus",
    "EventsDeliveryStatus",
    "IntentGraphSync",
    "IntentGraphSyncCatalog",
    "IntentGraphSyncError",
    "IntentGraphSyncErrorKind",
    "IntentGraphSyncMode",
    "IntentGraphSyncStatus",
    "RatelPlatform",
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
    "ToolPicker",
    "ToolPickerMode",
    "attach",
    "attach_intent_graph_sync",
    "classify_delivery",
    "platform_definitions_ready",
    "hash_catalog_snapshot",
    "is_remotely_publishable",
    "normalize_source_id",
    "ratel_platform",
]
