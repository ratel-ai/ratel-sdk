/**
 * The shim must keep every name that `@ratel-ai/cloud-sdk` exported before the Cloud
 * code moved into `@ratel-ai/sdk`. The fixed lists below come from `main` at the move
 * (commit 8ce2342). A name that goes missing, appears or changes kind fails here.
 */

import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";

const MAIN_EXPORTS: Record<string, { values: string[]; types: string[] }> = {
  "index.ts": {
    values: [
      "CloudSdkError",
      "DEFAULT_BASE_URL",
      "DEFAULT_TIMEOUT_MS",
      "IntentsClient",
      "JOB_STATUSES",
      "JobsClient",
      "RatelCloudSdk",
      "RuntimeCatalogClient",
      "SIGNAL_KINDS",
      "SKILL_FIELDS",
      "SKILL_STATUSES",
      "SUGGESTION_STATUSES",
      "SUGGESTION_TYPES",
      "SkillsClient",
      "SuggestionsClient",
      "Transport",
      "V2_SKILL_FIELDS",
      "canonicalSet",
      "canonicalSetV2",
      "canonicalSkill",
      "canonicalSkillV2",
      "consoleLogEvent",
      "ifNoneMatchMatches",
      "resolve",
      "resolveV2",
    ],
    types: [
      "AnalyzeInput",
      "AnalyzeResult",
      "Catalog",
      "CatalogResponse",
      "CatalogResponseV2",
      "CatalogV2",
      "CloudSdkErrorCode",
      "CloudSdkLogEvent",
      "CloudSdkOptions",
      "CloudSkill",
      "CloudSuggestion",
      "ConversationMessage",
      "EditSkillPatch",
      "ExtractedIntent",
      "GenerateSuggestionsResult",
      "ImportReport",
      "Job",
      "JobStatus",
      "ListIntentsOptions",
      "ListIntentsResult",
      "ListRuntimeCatalogOverridesOptions",
      "ListSkillsOptions",
      "ListSkillsResult",
      "ListSuggestionsOptions",
      "ListSuggestionsResult",
      "MessageRole",
      "NewSkillInput",
      "NewSkillPatch",
      "QueryIntent",
      "RetrievabilityPreview",
      "RetrievabilityPreviewHit",
      "RuntimeCatalogEntryKind",
      "RuntimeCatalogOverride",
      "RuntimeCatalogOverridesFreshResult",
      "RuntimeCatalogOverridesNotModifiedResult",
      "RuntimeCatalogOverridesResponse",
      "RuntimeCatalogOverridesResult",
      "RuntimeCatalogSnapshot",
      "RuntimeCatalogToolDefinition",
      "RuntimeEvent",
      "SignalKind",
      "SkillStatus",
      "SuggestJobReason",
      "SuggestJobResult",
      "SuggestResult",
      "SuggestionStatus",
      "SuggestionType",
      "UpdateSkillInput",
      "WaitForJobOptions",
      "WireSkill",
      "WireSkillV2",
    ],
  },
  "runtime/index.ts": {
    values: [
      "CatalogSnapshotsPublisher",
      "DeliveryStatus",
      "RUNTIME_EVENT_BATCH_MAX_BYTES",
      "RUNTIME_EVENT_BATCH_MAX_EVENTS",
      "RUNTIME_EVENT_MAX_BYTES",
      "RUNTIME_EVENT_TYPES",
      "RuntimeEventsPublisher",
      "attach",
      "attachIntentGraphSync",
      "hashCatalogSnapshot",
      "isRemotelyPublishable",
      "ratelCloud",
    ],
    types: [
      "AttachOptions",
      "CatalogSnapshotsPublisherOptions",
      "CloudDefinitionsRuntimeAttachment",
      "DeliveryKind",
      "DeliveryOverall",
      "DeliveryResult",
      "DeliveryStatusOptions",
      "EventsDeliveryStatus",
      "IntentGraphSync",
      "IntentGraphSyncCatalog",
      "IntentGraphSyncError",
      "IntentGraphSyncErrorKind",
      "IntentGraphSyncOptions",
      "IntentGraphSyncStatus",
      "RatelCloud",
      "RatelCloudOptions",
      "RatelRuntime",
      "RatelRuntimeCatalog",
      "RatelRuntimeCatalogToolDefinition",
      "RatelRuntimeEvents",
      "RuntimeAttachment",
      "RuntimeCatalogSnapshot",
      "RuntimeCatalogToolDefinition",
      "RuntimeDeliveryStatus",
      "RuntimeEvent",
      "RuntimeEventRejection",
      "RuntimeEventSubscription",
      "RuntimeEventType",
      "RuntimeEventsPublisherOptions",
      "RuntimeEventsRetryOptions",
      "SnapshotDeliveryStatus",
      "ToolPicker",
      "ToolPickerMode",
    ],
  },
  "otel/index.ts": {
    values: [
      "API_KEY_ENV",
      "EXPERIMENT_BAGGAGE_PREFIX",
      "OTLP_ENDPOINT_ENV",
      "OTLP_LOGS_ENDPOINT_ENV",
      "RatelLogRecordProcessor",
      "RatelSpanProcessor",
      "aiSdkSignalFilter",
      "ratelEventFilter",
      "ratelLogExporter",
      "ratelSignalFilter",
      "ratelTraceExporter",
      "resolveOtlpConfig",
      "resolveOtlpLogsConfig",
    ],
    types: [
      "LogFilter",
      "RatelLogRecordProcessorOptions",
      "RatelOtlpLogsOptions",
      "RatelOtlpOptions",
      "RatelSpanProcessorOptions",
      "ResolvedOtlpConfig",
      "SpanFilter",
    ],
  },
  "testing/index.ts": {
    values: ["MockCloud"],
    types: ["MockCloudOptions"],
  },
};

const LOADERS: Record<string, () => Promise<Record<string, unknown>>> = {
  "index.ts": () => import("./index.js"),
  "runtime/index.ts": () => import("./runtime/index.js"),
  "otel/index.ts": () => import("./otel/index.js"),
  "testing/index.ts": () => import("./testing/index.js"),
};

const SRC = import.meta.dirname;
const ENTRIES = Object.keys(MAIN_EXPORTS);
const program = ts.createProgram(
  ENTRIES.map((entry) => resolve(SRC, entry)),
  {
    module: ts.ModuleKind.NodeNext,
    moduleResolution: ts.ModuleResolutionKind.NodeNext,
    target: ts.ScriptTarget.ES2022,
    strict: true,
    skipLibCheck: true,
  },
);
const checker = program.getTypeChecker();
const SDK_CLOUD_DIST = resolve(dirname(realResolve("@ratel-ai/sdk/package.json")), "dist/cloud");

describe.each(ENTRIES)("the %s entry point", (entry) => {
  const expected = MAIN_EXPORTS[entry] as { values: string[]; types: string[] };
  const exported = exportsOf(entry);

  it("exports the same value names as on main", async () => {
    const loaded = await (LOADERS[entry] as () => Promise<Record<string, unknown>>)();
    expect(Object.keys(loaded).sort()).toEqual(expected.values);
    expect(exported.values).toEqual(expected.values);
  });

  it("exports the same type names as on main", () => {
    expect(exported.types).toEqual(expected.types);
  });

  it("re-exports each name from @ratel-ai/sdk", () => {
    expect(exported.outside).toEqual([]);
  });
});

function exportsOf(entry: string): { values: string[]; types: string[]; outside: string[] } {
  const source = program.getSourceFile(resolve(SRC, entry));
  const moduleSymbol = source && checker.getSymbolAtLocation(source);
  if (moduleSymbol === undefined) throw new Error(`cannot read ${entry}`);
  const values: string[] = [];
  const types: string[] = [];
  const outside: string[] = [];
  for (const symbol of checker.getExportsOfModule(moduleSymbol)) {
    const target = symbol.flags & ts.SymbolFlags.Alias ? checker.getAliasedSymbol(symbol) : symbol;
    (target.flags & ts.SymbolFlags.Value ? values : types).push(symbol.name);
    const declared = target.declarations?.[0]?.getSourceFile().fileName ?? "";
    if (!resolve(declared).startsWith(SDK_CLOUD_DIST)) outside.push(symbol.name);
  }
  return { values: values.sort(), types: types.sort(), outside };
}

function realResolve(specifier: string): string {
  return ts.sys.realpath?.(createRequire(import.meta.url).resolve(specifier)) ?? specifier;
}
