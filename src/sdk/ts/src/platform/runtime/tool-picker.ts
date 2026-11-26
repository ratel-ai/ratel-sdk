import type { RankCandidate, RankedId, RankFn, RetrieverError } from "../../index.js";
import { RatelPlatformClient } from "../client.js";
import { type PlatformConfig, resolvePlatformConfig } from "../config.js";
import type { IntentsClient } from "../intents.js";
import type { JobsClient } from "../jobs.js";
import type { RuntimeCatalogClient } from "../runtime-catalog.js";
import type { SkillsClient } from "../skills.js";
import type { SuggestionsClient } from "../suggestions.js";

/**
 * How the Tool Picker ranks: `instant` is BM25 only (free), `precise` puts a
 * BM25 shortlist in front of Jev, and `exhaustive` asks Jev about the whole
 * catalog (slow — Platform gives up at 45 s).
 */
export type ToolPickerMode = "instant" | "precise" | "exhaustive";

/**
 * `apiKey` and `baseUrl` resolve through {@link resolvePlatformConfig} on every pick,
 * so a change of `RATEL_API_KEY` or `RATEL_BASE_URL` applies to the next pick.
 * `timeoutMs` overrides the per-mode request budget (15 s, or 60 s for `exhaustive`).
 */
export interface RatelPlatformOptions extends Readonly<PlatformConfig> {}

/**
 * A {@link RankFn} that ranks with the Ratel Platform Tool Picker. Pass it as
 * `retrieveFn` (with `method: "custom"`) or as `rerankerFn`.
 */
export interface ToolPicker extends RankFn {
  readonly mode: ToolPickerMode;
  /** The same picker with another mode. The original is unchanged. */
  withMode(mode: ToolPickerMode): ToolPicker;
}

/**
 * The Ratel Platform API from one configuration: the management clients and the
 * Tool Picker. `ratelPlatform()` returns it. `ratel({ platform }).platform` returns it with
 * the runtime methods added (`RatelPlatformRuntime`).
 */
export interface RatelPlatform {
  /** Ranks tools with the Tool Picker. `precise` mode unless `ratel({ platform: { toolPicker } })` sets another. */
  readonly toolPicker: ToolPicker;
  /** Skill catalog management: create, update, publish and import skills. */
  readonly skills: SkillsClient;
  /** Review of the improvement suggestions that Platform drafts. */
  readonly suggestions: SuggestionsClient;
  /** Intent analysis of conversations and the recurring-ask ledger. */
  readonly intents: IntentsClient;
  /** Status of the asynchronous jobs that Platform runs. */
  readonly jobs: JobsClient;
  /** The Platform-owned Retrieval descriptions of the runtime catalog. */
  readonly runtimeCatalog: RuntimeCatalogClient;
}

/** Platform's `top_k` ceiling for `/tools/pick`. */
const MAX_TOP_K = 20;
/** Platform rejects longer queries with a 400, which would fail a reranker instead of falling back. */
const MAX_QUERY_CHARS = 2_000;
const PICK_TIMEOUT_MS = 15_000;
/** Above Platform's 45 s pick deadline, so its 504 arrives before our abort. */
const EXHAUSTIVE_TIMEOUT_MS = 60_000;

/** Ratel Platform features for a `ratel()` runtime from `@ratel-ai/sdk`. */
export function ratelPlatform(options: RatelPlatformOptions = {}): RatelPlatform {
  return createRatelPlatform(options, "precise");
}

/**
 * The {@link RatelPlatform} of `options`, with its Tool Picker in `mode`. Builds no
 * connection: a client sends its first request when the caller calls it.
 */
export function createRatelPlatform(
  options: RatelPlatformOptions,
  mode: ToolPickerMode,
): RatelPlatform {
  const sdk = new RatelPlatformClient(options);
  return {
    toolPicker: createToolPicker(options, mode),
    skills: sdk.skills,
    suggestions: sdk.suggestions,
    intents: sdk.intents,
    jobs: sdk.jobs,
    runtimeCatalog: sdk.runtimeCatalog,
  };
}

function createToolPicker(options: RatelPlatformOptions, mode: ToolPickerMode): ToolPicker {
  const pick = (query: string, candidates: RankCandidate[], topK: number) =>
    pickTools(options, mode, query, candidates, topK);
  return Object.assign(pick, {
    mode,
    withMode: (next: ToolPickerMode) => createToolPicker(options, next),
  });
}

async function pickTools(
  options: RatelPlatformOptions,
  mode: ToolPickerMode,
  query: string,
  candidates: RankCandidate[],
  topK: number,
): Promise<RankedId[]> {
  // The picker ranks the project's synced tool catalog; it knows no skills. The SDK
  // calls a RankFn once per catalog, so every candidate shares the first one's kind.
  if (candidates.length === 0 || topK <= 0 || candidates[0]?.kind === "skill") return [];
  const fail = await retrieverErrorFactory();
  const config = resolvePlatformConfig(options);
  const apiKey = config.apiKey ?? "";
  if (apiKey === "") {
    throw fail(
      "Ratel Platform Tool Picker needs an API key: pass apiKey or set RATEL_API_KEY",
      "Config",
    );
  }

  const { baseUrl } = config;
  const timeoutMs =
    options.timeoutMs ?? (mode === "exhaustive" ? EXHAUSTIVE_TIMEOUT_MS : PICK_TIMEOUT_MS);
  let response: Response;
  try {
    response = await config.fetch(`${baseUrl}/tools/pick`, {
      method: "POST",
      headers: { authorization: `Bearer ${apiKey}`, "content-type": "application/json" },
      body: JSON.stringify({ query: truncate(query), mode, top_k: Math.min(topK, MAX_TOP_K) }),
      redirect: "manual",
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (error) {
    if (isAbort(error)) {
      throw fail(`Ratel Platform Tool Picker did not answer within ${timeoutMs} ms`, "Timeout", {
        transient: true,
      });
    }
    throw fail(`Ratel Platform Tool Picker is unreachable: ${errorText(error)}`, "Unreachable", {
      transient: true,
    });
  }

  if (!response.ok) throw await httpError(fail, response);
  const status = response.status;
  let body: unknown;
  try {
    body = await response.json();
  } catch (error) {
    if (isAbort(error)) {
      throw fail(`Ratel Platform Tool Picker did not answer within ${timeoutMs} ms`, "Timeout", {
        transient: true,
        status,
      });
    }
    throw fail("Ratel Platform Tool Picker returned a body that is not JSON", "Malformed", {
      transient: true,
      status,
    });
  }
  const ranked = parseRanked(body);
  if (ranked === undefined) {
    throw fail("Ratel Platform Tool Picker returned an unexpected response shape", "Malformed", {
      transient: true,
      status,
    });
  }
  return ranked;
}

type ErrorFactory = (
  message: string,
  code: string,
  details?: { transient?: boolean; status?: number; retryAfterSecs?: number },
) => RetrieverError;

let errorFactory: Promise<ErrorFactory> | undefined;

/**
 * `RetrieverError` must be the SDK's own class — the SDK checks it with
 * `instanceof` to decide a reranker fallback. It is loaded on first pick so
 * `/runtime` keeps loading next to SDKs that predate it.
 */
function retrieverErrorFactory(): Promise<ErrorFactory> {
  errorFactory ??= import("../../errors.js").then(
    (sdk: { RetrieverError?: typeof RetrieverError }) => {
      const Retriever = sdk.RetrieverError;
      if (Retriever === undefined) {
        throw new Error(
          "Ratel Platform Tool Picker needs an @ratel-ai/sdk release that exports RetrieverError",
        );
      }
      return (message, code, details) => new Retriever(message, code, details);
    },
    (error: unknown) => {
      errorFactory = undefined;
      throw new Error(`Ratel Platform Tool Picker needs @ratel-ai/sdk: ${errorText(error)}`);
    },
  );
  return errorFactory;
}

async function httpError(fail: ErrorFactory, response: Response): Promise<RetrieverError> {
  const status = response.status;
  const detail = `HTTP ${status}: ${await errorMessage(response)}`;
  switch (status) {
    case 400:
    case 413:
      return fail(`Ratel Platform Tool Picker rejected the request (${detail})`, "InvalidRequest", {
        status,
      });
    case 401:
    case 403:
      return fail(`Ratel Platform rejected the API key (${detail})`, "Unauthorized", { status });
    case 402:
      return fail(
        `Ratel Platform Tool Picker is out of credits (${detail})`,
        "InsufficientCredits",
        {
          status,
        },
      );
    case 409:
      return fail(
        `Ratel Platform has no synced tools for this project (${detail}). Call attach(runtime) from ` +
          "@ratel-ai/sdk/platform, register your tools, then await handle.flush() so the " +
          "catalog snapshot lands before the first search.",
        "NoSyncedTools",
        { status },
      );
    case 429: {
      const retryAfterSecs = retryAfterSeconds(response.headers.get("retry-after"));
      return fail(`Ratel Platform Tool Picker is rate limited (${detail})`, "RateLimited", {
        transient: true,
        status,
        ...(retryAfterSecs === undefined ? {} : { retryAfterSecs }),
      });
    }
    case 502:
    case 503:
      return fail(`Ratel Platform Tool Picker is unavailable (${detail})`, "Unavailable", {
        transient: true,
        status,
      });
    case 504:
      return fail(`Ratel Platform Tool Picker timed out (${detail})`, "Timeout", {
        transient: true,
        status,
      });
    default:
      return fail(`Ratel Platform Tool Picker failed (${detail})`, "Http", {
        transient: status >= 500,
        status,
      });
  }
}

/** The pick envelope is `{ error: { message, type } }`; anything else falls back to raw text. */
async function errorMessage(response: Response): Promise<string> {
  const text = await response.text().catch(() => "");
  try {
    const body: unknown = JSON.parse(text);
    if (isRecord(body)) {
      const error = body.error;
      if (isRecord(error) && typeof error.message === "string") return error.message;
      if (typeof error === "string") return error;
    }
  } catch {
    // Not JSON: use the raw text below.
  }
  return text.slice(0, 500) || response.statusText || "no response body";
}

function parseRanked(body: unknown): RankedId[] | undefined {
  if (!isRecord(body) || !Array.isArray(body.tools)) return undefined;
  const ranked: RankedId[] = [];
  for (const tool of body.tools) {
    if (!isRecord(tool) || typeof tool.id !== "string" || typeof tool.score !== "number") {
      return undefined;
    }
    ranked.push({ id: tool.id, score: tool.score });
  }
  return ranked;
}

/** Cut to Platform's limit (UTF-16 units, as Platform counts) without splitting a surrogate pair. */
function truncate(query: string): string {
  if (query.length <= MAX_QUERY_CHARS) return query;
  const last = query.charCodeAt(MAX_QUERY_CHARS - 1);
  const highSurrogate = last >= 0xd800 && last <= 0xdbff;
  return query.slice(0, highSurrogate ? MAX_QUERY_CHARS - 1 : MAX_QUERY_CHARS);
}

/** Whole seconds only; an HTTP-date is ignored. */
function retryAfterSeconds(value: string | null): number | undefined {
  if (value === null || !/^\d+$/.test(value.trim())) return undefined;
  return Number(value.trim());
}

function isAbort(error: unknown): boolean {
  return error instanceof Error && (error.name === "TimeoutError" || error.name === "AbortError");
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
