/**
 * The one place that resolves where Ratel Platform is and how to authenticate.
 *
 * Every Platform feature (the management client, `attach()`, the publishers, the Tool
 * Picker, intent graph sync and the OTLP resolvers) reads `apiKey` and `baseUrl`
 * through {@link resolvePlatformConfig}, so one env variable moves all of them.
 *
 * This module imports nothing from the rest of the Platform code, so any module can
 * import it without an import cycle.
 */

/** Default production API root. All paths are relative to it. */
export const DEFAULT_BASE_URL = "https://app.ratel.sh/api/v1";

export const DEFAULT_TIMEOUT_MS = 30_000;

/** Env variable that holds the project API key used when `apiKey` is omitted. */
export const API_KEY_ENV = "RATEL_API_KEY";

/** Env variable that holds the API root used when `baseUrl` is omitted. */
export const BASE_URL_ENV = "RATEL_BASE_URL";

/** A platform feature that an env switch can turn off. */
export type PlatformSwitch = "events" | "intentGraph";

/**
 * The env switch of each feature. The first name wins when it is set. The second
 * is the deprecated name that the SDK still reads.
 */
const SWITCH_ENV: Record<PlatformSwitch, readonly [string, string]> = {
  events: ["RATEL_PLATFORM_EVENTS", "RATEL_CLOUD_EVENTS"],
  intentGraph: ["RATEL_PLATFORM_INTENT_GRAPH", "RATEL_CLOUD_INTENT_GRAPH"],
};

/** The settings that every Ratel Platform feature shares. */
export interface PlatformConfig {
  /** Project API key (`rtl_…`), sent as `Authorization: Bearer <key>`. Defaults to `RATEL_API_KEY`. */
  apiKey?: string;
  /**
   * API root, including the `/api/v1` prefix. Defaults to `RATEL_BASE_URL`, then
   * `https://app.ratel.sh/api/v1` (`DEFAULT_BASE_URL`).
   */
  baseUrl?: string;
  /** Injectable for testing. Defaults to the global `fetch`. */
  fetch?: typeof fetch;
  /** Per-request timeout in ms. Each feature has its own default (30 s for most requests). */
  timeoutMs?: number;
}

/** A {@link PlatformConfig} with each default applied. `apiKey` stays undefined when no key is set. */
export interface ResolvedPlatformConfig {
  apiKey: string | undefined;
  /** Never ends with a slash. */
  baseUrl: string;
  fetch: typeof fetch;
  timeoutMs: number;
}

type Env = Record<string, string | undefined>;

/**
 * Resolve {@link PlatformConfig} into concrete settings.
 *
 * Precedence for each value: the explicit option, then the env variable
 * (`RATEL_API_KEY`, `RATEL_BASE_URL`), then the default. An empty env value counts
 * as unset. Pure and env-injectable.
 */
export function resolvePlatformConfig(
  options: PlatformConfig = {},
  env: Env = process.env,
): ResolvedPlatformConfig {
  return {
    apiKey: options.apiKey ?? nonEmpty(env[API_KEY_ENV]),
    baseUrl: trimTrailingSlashes(
      options.baseUrl ?? nonEmpty(env[BASE_URL_ENV]) ?? DEFAULT_BASE_URL,
    ),
    fetch: options.fetch ?? globalThis.fetch,
    timeoutMs: options.timeoutMs ?? DEFAULT_TIMEOUT_MS,
  };
}

/**
 * Whether the env leaves a platform feature on. `off` (any case, any spaces) turns
 * it off. The `RATEL_PLATFORM_*` name wins when it is set. Else the deprecated
 * `RATEL_CLOUD_*` name applies.
 */
export function platformSwitchOn(feature: PlatformSwitch, env: Env = process.env): boolean {
  const [current, deprecated] = SWITCH_ENV[feature];
  const value = nonEmpty(env[current]) ?? env[deprecated];
  return value?.trim().toLowerCase() !== "off";
}

function nonEmpty(value: string | undefined): string | undefined {
  return value === undefined || value === "" ? undefined : value;
}

function trimTrailingSlashes(url: string): string {
  return url.replace(/\/+$/, "");
}
