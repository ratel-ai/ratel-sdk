export { RatelPlatformClient } from "./client.js";
export {
  API_KEY_ENV,
  BASE_URL_ENV,
  type PlatformConfig,
  type ResolvedPlatformConfig,
  resolvePlatformConfig,
} from "./config.js";
export { PlatformError, type PlatformErrorCode } from "./errors.js";
export { IntentsClient, type ListIntentsOptions } from "./intents.js";
export { JobsClient, type WaitForJobOptions } from "./jobs.js";
export type {
  RatelPlatformConfig,
  RatelPlatformEventsOptions,
  RatelPlatformIntentGraphOptions,
  RatelPlatformRuntime,
} from "./ratel-platform.js";
// The runtime names: fail-open runtime facts delivery, plus Tool Picker ranking.
// `RuntimeCatalogSnapshot`, `RuntimeCatalogToolDefinition` and `RuntimeEvent` come from
// `./types.js` above.
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
  type PlatformDefinitionsRuntimeAttachment,
  type RatelPlatform,
  type RatelPlatformOptions,
  type RatelRuntime,
  type RatelRuntimeCatalog,
  type RatelRuntimeCatalogToolDefinition,
  type RatelRuntimeEvents,
  RUNTIME_EVENT_BATCH_MAX_BYTES,
  RUNTIME_EVENT_BATCH_MAX_EVENTS,
  RUNTIME_EVENT_MAX_BYTES,
  RUNTIME_EVENT_TYPES,
  type RuntimeAttachment,
  type RuntimeDeliveryStatus,
  type RuntimeEventRejection,
  type RuntimeEventSubscription,
  RuntimeEventsPublisher,
  type RuntimeEventsPublisherOptions,
  type RuntimeEventsRetryOptions,
  type RuntimeEventType,
  ratelPlatform,
  type SnapshotDeliveryStatus,
  type ToolPicker,
  type ToolPickerMode,
} from "./runtime/index.js";
export { RuntimeCatalogClient } from "./runtime-catalog.js";
export { type ListSkillsOptions, type ListSkillsResult, SkillsClient } from "./skills.js";
export { type ListSuggestionsOptions, SuggestionsClient } from "./suggestions.js";
export {
  consoleLogEvent,
  DEFAULT_BASE_URL,
  DEFAULT_TIMEOUT_MS,
  type PlatformClientOptions,
  type PlatformLogEvent,
  Transport,
} from "./transport.js";
export * from "./types.js";
export {
  canonicalSet,
  canonicalSetV2,
  canonicalSkill,
  canonicalSkillV2,
  ifNoneMatchMatches,
  resolve,
  resolveV2,
} from "./wire.js";
