/**
 * Pure endpoint/auth resolution for the Ratel Platform OTLP destination.
 *
 * No OpenTelemetry import: these helpers only decide *where* telemetry goes and
 * *how* it authenticates, so the precedence rules stay testable without a network
 * or an SDK. The processor in `./processor.ts` consumes the result.
 *
 * The route derives from the same `baseUrl` the management client uses, resolved by
 * {@link resolvePlatformConfig} (`baseUrl`, then `RATEL_BASE_URL`, then
 * {@link DEFAULT_BASE_URL}), so a project points both halves of the SDK at one
 * deployment by setting one value.
 */

import { API_KEY_ENV, type DEFAULT_BASE_URL, resolvePlatformConfig } from "../config.js";

export { API_KEY_ENV };

/** Env var naming the OTLP traces endpoint. Redirects telemetry to any OTLP backend. */
export const OTLP_ENDPOINT_ENV = "RATEL_OTLP_ENDPOINT";

/** Env var naming the OTLP logs endpoint. Redirects logs to any OTLP backend. */
export const OTLP_LOGS_ENDPOINT_ENV = "RATEL_OTLP_LOGS_ENDPOINT";

/** Where telemetry goes and how it authenticates. */
export interface RatelOtlpOptions {
  /** Project API key (`rtl_…`), sent as `Authorization: Bearer <key>`. Defaults to `RATEL_API_KEY`. */
  apiKey?: string;
  /**
   * API root the traces route hangs off. Defaults to `RATEL_BASE_URL`, then
   * {@link DEFAULT_BASE_URL}. Include
   * the `/api/v1` prefix, exactly as the management client expects it.
   */
  baseUrl?: string;
  /** Full OTLP traces URL. Overrides {@link baseUrl}; defaults to `RATEL_OTLP_ENDPOINT`. */
  endpoint?: string;
  /**
   * Extra headers merged onto every export. An `Authorization` header set here is
   * kept over the `RATEL_API_KEY` env fallback, but an explicit `apiKey` still wins.
   */
  headers?: Record<string, string>;
}

/** Endpoint and authentication options for the Ratel Platform OTLP Logs destination. */
export interface RatelOtlpLogsOptions extends RatelOtlpOptions {}

/** Resolved destination config — the pure core of the processor, exposed for testing. */
export interface ResolvedOtlpConfig {
  url: string;
  headers: Record<string, string>;
}

/**
 * Resolve {@link RatelOtlpOptions} into concrete exporter config.
 *
 * Endpoint precedence: explicit `endpoint` → `RATEL_OTLP_ENDPOINT` → `${baseUrl}/traces`,
 * where `baseUrl` resolves through {@link resolvePlatformConfig}.
 * Auth precedence: explicit `apiKey` → a caller-supplied `Authorization` header →
 * `RATEL_API_KEY`. Code-level config always beats ambient environment, and ambient
 * environment never clobbers an auth header the caller set deliberately.
 *
 * Pure and env-injectable so the precedence is provable without a network.
 */
export function resolveOtlpConfig(
  opts: RatelOtlpOptions = {},
  env: Record<string, string | undefined> = process.env,
): ResolvedOtlpConfig {
  const url = opts.endpoint ?? env[OTLP_ENDPOINT_ENV] ?? `${otlpBaseUrl(opts, env)}/traces`;

  // The caller's own Authorization header sits between the explicit key and the env
  // key, so the resolver sees the explicit key only when the caller set no header.
  const headers: Record<string, string> = { ...opts.headers };
  const apiKey = opts.apiKey
    ? opts.apiKey
    : hasAuthorizationHeader(headers)
      ? undefined
      : resolvePlatformConfig({}, env).apiKey;
  if (apiKey) headers.Authorization = `Bearer ${apiKey}`;

  return { url, headers };
}

/**
 * Resolve OTLP Logs exporter config.
 *
 * Endpoint precedence: explicit `endpoint` → `RATEL_OTLP_LOGS_ENDPOINT` →
 * `${baseUrl}/logs`. Authentication and header precedence exactly match
 * {@link resolveOtlpConfig}.
 */
export function resolveOtlpLogsConfig(
  opts: RatelOtlpLogsOptions = {},
  env: Record<string, string | undefined> = process.env,
): ResolvedOtlpConfig {
  const endpoint = opts.endpoint ?? env[OTLP_LOGS_ENDPOINT_ENV] ?? `${otlpBaseUrl(opts, env)}/logs`;
  return resolveOtlpConfig({ ...opts, endpoint }, env);
}

function otlpBaseUrl(opts: RatelOtlpOptions, env: Record<string, string | undefined>): string {
  return resolvePlatformConfig(opts.baseUrl === undefined ? {} : { baseUrl: opts.baseUrl }, env)
    .baseUrl;
}

/** Whether the caller already supplied an `Authorization` header (any casing). */
function hasAuthorizationHeader(headers: Record<string, string>): boolean {
  return Object.keys(headers).some((key) => key.toLowerCase() === "authorization");
}
