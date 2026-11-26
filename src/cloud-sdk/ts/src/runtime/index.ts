/**
 * `@ratel-ai/cloud-sdk/runtime` is deprecated. Import `@ratel-ai/sdk/platform` instead.
 *
 * This entry point re-exports the names it exported before the platform code moved into
 * `@ratel-ai/sdk`. Each old Cloud name is an alias of its new Platform name. It holds
 * no logic.
 *
 * @deprecated Import `@ratel-ai/sdk/platform`.
 */

export {
  type AttachOptions,
  attach,
  attachIntentGraphSync,
  CatalogSnapshotsPublisher,
  type CatalogSnapshotsPublisherOptions,
  type DeliveryKind,
  type DeliveryOverall,
  type DeliveryResult,
  DeliveryStatus,
  type DeliveryStatusOptions,
  type EventsDeliveryStatus,
  hashCatalogSnapshot,
  type IntentGraphSync,
  type IntentGraphSyncCatalog,
  type IntentGraphSyncError,
  type IntentGraphSyncErrorKind,
  type IntentGraphSyncOptions,
  type IntentGraphSyncStatus,
  isRemotelyPublishable,
  type PlatformDefinitionsRuntimeAttachment as CloudDefinitionsRuntimeAttachment,
  type RatelPlatform as RatelCloud,
  type RatelPlatformOptions as RatelCloudOptions,
  type RatelRuntime,
  type RatelRuntimeCatalog,
  type RatelRuntimeCatalogToolDefinition,
  type RatelRuntimeEvents,
  RUNTIME_EVENT_BATCH_MAX_BYTES,
  RUNTIME_EVENT_BATCH_MAX_EVENTS,
  RUNTIME_EVENT_MAX_BYTES,
  RUNTIME_EVENT_TYPES,
  type RuntimeAttachment,
  type RuntimeCatalogSnapshot,
  type RuntimeCatalogToolDefinition,
  type RuntimeDeliveryStatus,
  type RuntimeEvent,
  type RuntimeEventRejection,
  type RuntimeEventSubscription,
  RuntimeEventsPublisher,
  type RuntimeEventsPublisherOptions,
  type RuntimeEventsRetryOptions,
  type RuntimeEventType,
  ratelPlatform as ratelCloud,
  type SnapshotDeliveryStatus,
  type ToolPicker,
  type ToolPickerMode,
} from "@ratel-ai/sdk/platform";
