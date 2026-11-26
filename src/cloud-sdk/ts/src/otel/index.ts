/**
 * `@ratel-ai/cloud-sdk/otel` is deprecated. Import `@ratel-ai/sdk/otel` instead.
 *
 * This entry point re-exports the names it exported before the Cloud code moved into
 * `@ratel-ai/sdk`. It holds no Cloud logic.
 *
 * @deprecated Import `@ratel-ai/sdk/otel`.
 */

export {
  API_KEY_ENV,
  aiSdkSignalFilter,
  EXPERIMENT_BAGGAGE_PREFIX,
  type LogFilter,
  OTLP_ENDPOINT_ENV,
  OTLP_LOGS_ENDPOINT_ENV,
  RatelLogRecordProcessor,
  type RatelLogRecordProcessorOptions,
  type RatelOtlpLogsOptions,
  type RatelOtlpOptions,
  RatelSpanProcessor,
  type RatelSpanProcessorOptions,
  type ResolvedOtlpConfig,
  ratelEventFilter,
  ratelLogExporter,
  ratelSignalFilter,
  ratelTraceExporter,
  resolveOtlpConfig,
  resolveOtlpLogsConfig,
  type SpanFilter,
} from "@ratel-ai/sdk/otel";
