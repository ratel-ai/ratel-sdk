/**
 * The wiring of `ratel({ cloud })`: one configuration turns on each Cloud feature
 * for one runtime, and gives the Cloud API from the same object (`r.cloud`).
 *
 * `ratel()` calls {@link setupRatelCloud} only when the caller sets `cloud`. With
 * no `cloud` option, nothing in this module runs.
 *
 * This module imports no value from the SDK root, so `@ratel-ai/sdk/cloud` still
 * loads without the native addon.
 */

import type { RankFn, SearchMethod } from "../catalog.js";
import type { IntentGraph } from "../registry.js";
import { type CloudConfig, resolveCloudConfig } from "./config.js";
import { CloudSdkError } from "./errors.js";
import {
  type AttachOptions,
  attach,
  type CloudDefinitionsRuntimeAttachment,
  cloudDefinitionsReady,
  type RatelRuntimeCatalog,
  type RatelRuntimeEvents,
  type RuntimeAttachment,
} from "./runtime/attach.js";
import {
  type DeliveryResult,
  DeliveryStatus,
  type RuntimeDeliveryStatus,
} from "./runtime/delivery-status.js";
import {
  attachIntentGraphSync,
  type IntentGraphSync,
  type IntentGraphSyncOptions,
} from "./runtime/intent-graph-sync.js";
import { RuntimeEventsPublisher } from "./runtime/publisher.js";
import { createRatelCloud, type RatelCloud, type ToolPickerMode } from "./runtime/tool-picker.js";

/** Tuning of runtime event and catalog snapshot delivery: the options of `attach()` that are not shared. */
export type RatelCloudEventsOptions = Omit<
  AttachOptions,
  "apiKey" | "baseUrl" | "fetch" | "timeoutMs" | "sourceId" | "useCloudDefinitions"
>;

/** Tuning of intent graph sync: the options of `attachIntentGraphSync()` that are not shared. */
export type RatelCloudIntentGraphOptions = Omit<
  IntentGraphSyncOptions,
  "apiKey" | "endpoint" | "baseUrl" | "fetch" | "timeoutMs" | "sourceId"
>;

/**
 * The `cloud` option of `ratel()`. `apiKey`, `baseUrl`, `fetch` and `timeoutMs`
 * apply to every feature and resolve through `resolveCloudConfig` of
 * `@ratel-ai/sdk/cloud`: the option, then `RATEL_API_KEY` / `RATEL_BASE_URL`, then
 * the default.
 */
export interface RatelCloudConfig extends CloudConfig {
  /** Stable deployment source. Defaults to the runtime's event-stream source id. */
  sourceId?: string;
  /**
   * Publish runtime events and catalog snapshots. Default on. An object turns it on
   * with tuning. `RATEL_CLOUD_EVENTS=off` turns it off.
   */
  events?: boolean | RatelCloudEventsOptions;
  /**
   * Give Cloud ownership of Retrieval descriptions (`useCloudDefinitions` of
   * `attach()`). Default off. Needs `events`: Cloud overrides the catalog it receives.
   */
  definitions?: boolean;
  /**
   * Sync the intent graph of the tool catalog with Cloud and rank with it (adaptive
   * ranking). Default off. An object turns it on with tuning.
   * `RATEL_CLOUD_INTENT_GRAPH=off` turns it off.
   */
  intentGraph?: boolean | RatelCloudIntentGraphOptions;
  /**
   * Rank tools with the Cloud Tool Picker (`method: "custom"`). Default off. `true`
   * means `"precise"`. Do not set `method` or `retrieveFn` with it.
   */
  toolPicker?: boolean | ToolPickerMode;
}

/** `r.cloud`: the Cloud API plus the lifecycle of the Cloud features of one runtime. */
export interface RatelCloudRuntime extends RatelCloud {
  /** Current JSON-serializable delivery health of events and snapshots. */
  status(): RuntimeDeliveryStatus;
  /** Probe event ingestion without mutating publisher queues. */
  verify(): Promise<DeliveryResult>;
  /**
   * Resolves when the first definitions pull and the first intent graph load end,
   * with success or not. Never rejects.
   */
  ready(): Promise<void>;
  /** Deliver the events, snapshots and intent graph changes already accepted. Never rejects. */
  flush(): Promise<void>;
  /** Flush, then stop each Cloud feature of this runtime. Idempotent. Never rejects. */
  close(): Promise<void>;
  /** Pull and apply changed Cloud definitions. `false` when `definitions` is off. */
  refreshDefinitions(): Promise<boolean>;
}

/** The parts of a `ratel()` runtime that the Cloud features use. */
export interface RatelCloudHost {
  readonly events: RatelRuntimeEvents;
  readonly catalog: RatelRuntimeCatalog;
  /** The tool catalog, for adaptive ranking with the intent graph. */
  readonly tools: {
    experimentalEnableAdaptiveRanking(
      graph: IntentGraph,
      options?: { learn?: boolean; graphKey?: string },
    ): void;
  };
}

/** The ranking options of `ratel()` that the Tool Picker replaces. */
export interface RatelCloudRanking {
  readonly method?: SearchMethod | undefined;
  readonly retrieveFn?: RankFn | undefined;
}

/** What {@link setupRatelCloud} gives `ratel()`. */
export interface RatelCloudSetup {
  /** The ranking for the tool and skill catalogs, or `undefined` to keep the caller's. */
  readonly ranking: { readonly method: "custom"; readonly retrieveFn: RankFn } | undefined;
  /** Start the Cloud features for the runtime and return its `r.cloud`. */
  connect(host: RatelCloudHost): RatelCloudRuntime;
}

/**
 * Validate the `cloud` option of `ratel()` before the catalogs exist. Throws a
 * {@link CloudSdkError} (`config`) when no API key is set or the option conflicts
 * with the caller's ranking. Sends no request.
 */
export function setupRatelCloud(
  cloud: true | RatelCloudConfig,
  ranking: RatelCloudRanking,
): RatelCloudSetup {
  const options: RatelCloudConfig = cloud === true ? {} : cloud;
  const shared = sharedConfig(options);
  if (!resolveCloudConfig(shared).apiKey) {
    throw configError("ratel({ cloud }) needs an API key: pass cloud.apiKey or set RATEL_API_KEY");
  }
  if (options.events === false && options.definitions === true) {
    throw configError("ratel({ cloud }): cloud.definitions needs cloud.events");
  }
  const pickerMode = toolPickerMode(options.toolPicker);
  if (
    pickerMode !== undefined &&
    (ranking.method !== undefined || ranking.retrieveFn !== undefined)
  ) {
    throw configError(
      "ratel({ cloud }): cloud.toolPicker sets method and retrieveFn; remove method and retrieveFn",
    );
  }
  const api = createRatelCloud(shared, pickerMode ?? "precise");
  return {
    ranking:
      pickerMode === undefined ? undefined : { method: "custom", retrieveFn: api.toolPicker },
    connect: (host) => connectRatelCloud(host, options, shared, api),
  };
}

function connectRatelCloud(
  host: RatelCloudHost,
  options: RatelCloudConfig,
  shared: CloudConfig,
  api: RatelCloud,
): RatelCloudRuntime {
  const sourceId = options.sourceId === undefined ? {} : { sourceId: options.sourceId };
  const attachment = startEvents(host, options, shared, sourceId);
  const intentGraph = startIntentGraph(host, options, shared, sourceId);
  let verifier: RuntimeEventsPublisher | undefined;
  let closing: Promise<void> | undefined;

  return {
    ...api,
    status: () => attachment?.status() ?? new DeliveryStatus({ enabled: false }).snapshot(),
    verify: () => {
      if (attachment !== undefined) return attachment.verify();
      verifier ??= new RuntimeEventsPublisher(shared);
      return verifier.verify();
    },
    ready: () => settled([cloudDefinitionsReady(host), intentGraph.loaded]),
    flush: () => settled([attachment?.flush(), intentGraph.loaded.then((sync) => sync?.flush())]),
    close: () => {
      closing ??= settled([
        attachment?.close(),
        intentGraph.loaded.then((sync) => sync?.close()),
        verifier?.close(),
      ]);
      return closing;
    },
    refreshDefinitions: async () => {
      if (attachment === undefined || !("refreshCloudDefinitions" in attachment)) return false;
      try {
        return await attachment.refreshCloudDefinitions();
      } catch {
        return false;
      }
    },
  };
}

/** The settings that every feature shares, with no key that holds `undefined`. */
function sharedConfig(options: RatelCloudConfig): CloudConfig {
  return {
    ...(options.apiKey === undefined ? {} : { apiKey: options.apiKey }),
    ...(options.baseUrl === undefined ? {} : { baseUrl: options.baseUrl }),
    ...(options.fetch === undefined ? {} : { fetch: options.fetch }),
    ...(options.timeoutMs === undefined ? {} : { timeoutMs: options.timeoutMs }),
  };
}

function configError(message: string): CloudSdkError {
  return new CloudSdkError(message, { status: null, code: "config" });
}

function toolPickerMode(option: RatelCloudConfig["toolPicker"]): ToolPickerMode | undefined {
  if (option === undefined || option === false) return undefined;
  return option === true ? "precise" : option;
}

function startEvents(
  host: RatelCloudHost,
  options: RatelCloudConfig,
  shared: CloudConfig,
  sourceId: { sourceId?: string },
): RuntimeAttachment | CloudDefinitionsRuntimeAttachment | undefined {
  if (options.events === false) return undefined;
  const tuning = typeof options.events === "object" ? options.events : {};
  return attach(host, {
    ...tuning,
    ...shared,
    ...sourceId,
    ...(options.definitions === true ? { useCloudDefinitions: true } : {}),
  });
}

/**
 * Start intent graph sync for the tool catalog. Once the graph loads, the tool
 * catalog ranks with it, and again with each graph Cloud replaces it with. A sync
 * that cannot start warns once and leaves ranking as it was (fail-open).
 */
function startIntentGraph(
  host: RatelCloudHost,
  options: RatelCloudConfig,
  shared: CloudConfig,
  sourceId: { sourceId?: string },
): { loaded: Promise<IntentGraphSync | undefined> } {
  if (options.intentGraph === undefined || options.intentGraph === false) {
    return { loaded: Promise.resolve(undefined) };
  }
  const tuning = typeof options.intentGraph === "object" ? options.intentGraph : {};
  const consume = tuning.mode === "consume";
  const rank = (graph: IntentGraph): void => {
    host.tools.experimentalEnableAdaptiveRanking(
      graph,
      consume ? { learn: false, graphKey: "cloud" } : {},
    );
  };
  const loaded = attachIntentGraphSync(
    { events: host.events },
    {
      ...tuning,
      ...shared,
      ...sourceId,
      onReplaced: (graph) => {
        rank(graph);
        tuning.onReplaced?.(graph);
      },
    },
  ).then(
    (sync) => {
      if (sync.status !== "disabled") rank(sync.graph);
      return sync;
    },
    (error: unknown) => {
      warnIntentGraphFailure(error);
      return undefined;
    },
  );
  return { loaded };
}

function warnIntentGraphFailure(error: unknown): void {
  try {
    console.warn(
      `[ratel-cloud] intent graph sync did not start: ${error instanceof Error ? error.message : String(error)}`,
    );
  } catch {
    // Console diagnostics stay fail-open.
  }
}

/** Wait for each promise to end, ignore each result and each rejection. */
async function settled(promises: ReadonlyArray<Promise<unknown> | undefined>): Promise<void> {
  await Promise.allSettled(promises);
}
