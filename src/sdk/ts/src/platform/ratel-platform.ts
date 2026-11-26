/**
 * The wiring of `ratel({ platform })`: one configuration turns on each Platform feature
 * for one runtime, and gives the Platform API from the same object (`r.platform`).
 *
 * `ratel()` calls {@link setupRatelPlatform} only when the caller sets `platform`. With
 * no `platform` option, nothing in this module runs.
 *
 * This module imports no value from the SDK root, so `@ratel-ai/sdk/platform` still
 * loads without the native addon.
 */

import type { RankFn, SearchMethod } from "../catalog.js";
import type { IntentGraph } from "../registry.js";
import { type PlatformConfig, resolvePlatformConfig } from "./config.js";
import { PlatformError } from "./errors.js";
import {
  type AttachOptions,
  attach,
  type PlatformDefinitionsRuntimeAttachment,
  platformDefinitionsReady,
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
import {
  createRatelPlatform,
  type RatelPlatform,
  type ToolPickerMode,
} from "./runtime/tool-picker.js";

/** Tuning of runtime event and catalog snapshot delivery: the options of `attach()` that are not shared. */
export type RatelPlatformEventsOptions = Omit<
  AttachOptions,
  "apiKey" | "baseUrl" | "fetch" | "timeoutMs" | "sourceId" | "usePlatformDefinitions"
>;

/** Tuning of intent graph sync: the options of `attachIntentGraphSync()` that are not shared. */
export type RatelPlatformIntentGraphOptions = Omit<
  IntentGraphSyncOptions,
  "apiKey" | "endpoint" | "baseUrl" | "fetch" | "timeoutMs" | "sourceId"
>;

/**
 * The `platform` option of `ratel()`. `apiKey`, `baseUrl`, `fetch` and `timeoutMs`
 * apply to every feature and resolve through `resolvePlatformConfig` of
 * `@ratel-ai/sdk/platform`: the option, then `RATEL_API_KEY` / `RATEL_BASE_URL`, then
 * the default.
 */
export interface RatelPlatformConfig extends PlatformConfig {
  /** Stable deployment source. Defaults to the runtime's event-stream source id. */
  sourceId?: string;
  /**
   * Publish runtime events and catalog snapshots. Default on. An object turns it on
   * with tuning. `RATEL_PLATFORM_EVENTS=off` turns it off.
   */
  events?: boolean | RatelPlatformEventsOptions;
  /**
   * Give Platform ownership of Retrieval descriptions (`usePlatformDefinitions` of
   * `attach()`). Default off. Needs `events`: Platform overrides the catalog it receives.
   */
  definitions?: boolean;
  /**
   * Sync the intent graph of the tool catalog with Platform and rank with it (adaptive
   * ranking). Default off. An object turns it on with tuning.
   * `RATEL_PLATFORM_INTENT_GRAPH=off` turns it off.
   */
  intentGraph?: boolean | RatelPlatformIntentGraphOptions;
  /**
   * Rank tools with the Platform Tool Picker (`method: "custom"`). Default off. `true`
   * means `"precise"`. Do not set `method` or `retrieveFn` with it.
   */
  toolPicker?: boolean | ToolPickerMode;
}

/** `r.platform`: the Platform API plus the lifecycle of the Platform features of one runtime. */
export interface RatelPlatformRuntime extends RatelPlatform {
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
  /** Flush, then stop each Platform feature of this runtime. Idempotent. Never rejects. */
  close(): Promise<void>;
  /** Pull and apply changed Platform definitions. `false` when `definitions` is off. */
  refreshDefinitions(): Promise<boolean>;
}

/** The parts of a `ratel()` runtime that the Platform features use. */
export interface RatelPlatformHost {
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
export interface RatelPlatformRanking {
  readonly method?: SearchMethod | undefined;
  readonly retrieveFn?: RankFn | undefined;
}

/** What {@link setupRatelPlatform} gives `ratel()`. */
export interface RatelPlatformSetup {
  /** The ranking for the tool and skill catalogs, or `undefined` to keep the caller's. */
  readonly ranking: { readonly method: "custom"; readonly retrieveFn: RankFn } | undefined;
  /** Start the Platform features for the runtime and return its `r.platform`. */
  connect(host: RatelPlatformHost): RatelPlatformRuntime;
}

/**
 * Validate the `platform` option of `ratel()` before the catalogs exist. Throws a
 * {@link PlatformError} (`config`) when no API key is set or the option conflicts
 * with the caller's ranking. Sends no request.
 */
export function setupRatelPlatform(
  platform: true | RatelPlatformConfig,
  ranking: RatelPlatformRanking,
): RatelPlatformSetup {
  const options: RatelPlatformConfig = platform === true ? {} : platform;
  const shared = sharedConfig(options);
  if (!resolvePlatformConfig(shared).apiKey) {
    throw configError(
      "ratel({ platform }) needs an API key: pass platform.apiKey or set RATEL_API_KEY",
    );
  }
  if (options.events === false && options.definitions === true) {
    throw configError("ratel({ platform }): platform.definitions needs platform.events");
  }
  const pickerMode = toolPickerMode(options.toolPicker);
  if (
    pickerMode !== undefined &&
    (ranking.method !== undefined || ranking.retrieveFn !== undefined)
  ) {
    throw configError(
      "ratel({ platform }): platform.toolPicker sets method and retrieveFn; remove method and retrieveFn",
    );
  }
  const api = createRatelPlatform(shared, pickerMode ?? "precise");
  return {
    ranking:
      pickerMode === undefined ? undefined : { method: "custom", retrieveFn: api.toolPicker },
    connect: (host) => connectRatelPlatform(host, options, shared, api),
  };
}

function connectRatelPlatform(
  host: RatelPlatformHost,
  options: RatelPlatformConfig,
  shared: PlatformConfig,
  api: RatelPlatform,
): RatelPlatformRuntime {
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
    ready: () => settled([platformDefinitionsReady(host), intentGraph.loaded]),
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
      if (attachment === undefined || !("refreshPlatformDefinitions" in attachment)) return false;
      try {
        return await attachment.refreshPlatformDefinitions();
      } catch {
        return false;
      }
    },
  };
}

/** The settings that every feature shares, with no key that holds `undefined`. */
function sharedConfig(options: RatelPlatformConfig): PlatformConfig {
  return {
    ...(options.apiKey === undefined ? {} : { apiKey: options.apiKey }),
    ...(options.baseUrl === undefined ? {} : { baseUrl: options.baseUrl }),
    ...(options.fetch === undefined ? {} : { fetch: options.fetch }),
    ...(options.timeoutMs === undefined ? {} : { timeoutMs: options.timeoutMs }),
  };
}

function configError(message: string): PlatformError {
  return new PlatformError(message, { status: null, code: "config" });
}

function toolPickerMode(option: RatelPlatformConfig["toolPicker"]): ToolPickerMode | undefined {
  if (option === undefined || option === false) return undefined;
  return option === true ? "precise" : option;
}

function startEvents(
  host: RatelPlatformHost,
  options: RatelPlatformConfig,
  shared: PlatformConfig,
  sourceId: { sourceId?: string },
): RuntimeAttachment | PlatformDefinitionsRuntimeAttachment | undefined {
  if (options.events === false) return undefined;
  const tuning = typeof options.events === "object" ? options.events : {};
  return attach(host, {
    ...tuning,
    ...shared,
    ...sourceId,
    ...(options.definitions === true ? { usePlatformDefinitions: true } : {}),
  });
}

/**
 * Start intent graph sync for the tool catalog. Once the graph loads, the tool
 * catalog ranks with it, and again with each graph Platform replaces it with. A sync
 * that cannot start warns once and leaves ranking as it was (fail-open).
 */
function startIntentGraph(
  host: RatelPlatformHost,
  options: RatelPlatformConfig,
  shared: PlatformConfig,
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
      consume ? { learn: false, graphKey: "platform" } : {},
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
      `[ratel-platform] intent graph sync did not start: ${error instanceof Error ? error.message : String(error)}`,
    );
  } catch {
    // Console diagnostics stay fail-open.
  }
}

/** Wait for each promise to end, ignore each result and each rejection. */
async function settled(promises: ReadonlyArray<Promise<unknown> | undefined>): Promise<void> {
  await Promise.allSettled(promises);
}
