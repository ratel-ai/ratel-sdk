/**
 * The one place that resolves where Ratel Cloud is and how to authenticate.
 *
 * Every Cloud feature (the management client, `attach()`, the publishers, the Tool
 * Picker, intent graph sync and the OTLP resolvers) reads `apiKey` and `baseUrl`
 * through {@link resolveCloudConfig}, so one env variable moves all of them.
 *
 * This module imports nothing from the rest of the Cloud code, so any module can
 * import it without an import cycle.
 */

/** Default production API root. All paths are relative to it. */
export const DEFAULT_BASE_URL = "https://cloud.ratel.sh/api/v1";

export const DEFAULT_TIMEOUT_MS = 30_000;

/** Env variable that holds the project API key used when `apiKey` is omitted. */
export const API_KEY_ENV = "RATEL_API_KEY";

/** Env variable that holds the API root used when `baseUrl` is omitted. */
export const BASE_URL_ENV = "RATEL_BASE_URL";

/** The settings that every Ratel Cloud feature shares. */
export interface CloudConfig {
  /** Project API key (`rtl_…`), sent as `Authorization: Bearer <key>`. Defaults to `RATEL_API_KEY`. */
  apiKey?: string;
  /**
   * API root, including the `/api/v1` prefix. Defaults to `RATEL_BASE_URL`, then
   * {@link DEFAULT_BASE_URL}.
   */
  baseUrl?: string;
  /** Injectable for testing. Defaults to the global `fetch`. */
  fetch?: typeof fetch;
  /** Per-request timeout. Defaults to {@link DEFAULT_TIMEOUT_MS}. */
  timeoutMs?: number;
}

/** A {@link CloudConfig} with each default applied. `apiKey` stays undefined when no key is set. */
export interface ResolvedCloudConfig {
  apiKey: string | undefined;
  /** Never ends with a slash. */
  baseUrl: string;
  fetch: typeof fetch;
  timeoutMs: number;
}

type Env = Record<string, string | undefined>;

/**
 * Resolve {@link CloudConfig} into concrete settings.
 *
 * Precedence for each value: the explicit option, then the env variable
 * (`RATEL_API_KEY`, `RATEL_BASE_URL`), then the default. An empty env value counts
 * as unset. Pure and env-injectable.
 */
export function resolveCloudConfig(
  options: CloudConfig = {},
  env: Env = process.env,
): ResolvedCloudConfig {
  return {
    apiKey: options.apiKey ?? nonEmpty(env[API_KEY_ENV]),
    baseUrl: trimTrailingSlashes(
      options.baseUrl ?? nonEmpty(env[BASE_URL_ENV]) ?? DEFAULT_BASE_URL,
    ),
    fetch: options.fetch ?? globalThis.fetch,
    timeoutMs: options.timeoutMs ?? DEFAULT_TIMEOUT_MS,
  };
}

function nonEmpty(value: string | undefined): string | undefined {
  return value === undefined || value === "" ? undefined : value;
}

function trimTrailingSlashes(url: string): string {
  return url.replace(/\/+$/, "");
}
