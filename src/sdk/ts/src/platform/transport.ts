import { type PlatformConfig, resolvePlatformConfig } from "./config.js";
import { errorFromResponse, PlatformError } from "./errors.js";

export { DEFAULT_BASE_URL, DEFAULT_TIMEOUT_MS } from "./config.js";

/** A structured request/response/error event emitted for logging. The auth
 * header is never included. `path` includes the query string; `body` is the
 * parsed response JSON. */
export type PlatformLogEvent =
  | { phase: "request"; method: string; path: string; url: string }
  | {
      phase: "response";
      method: string;
      path: string;
      url: string;
      status: number;
      durationMs: number;
      body: unknown;
    }
  | {
      phase: "error";
      method: string;
      path: string;
      url: string;
      durationMs: number;
      error: string;
    };

/** The console sink used when `debug: true` and no custom `logger` is provided. */
export function consoleLogEvent(event: PlatformLogEvent): void {
  if (event.phase === "request") {
    console.log(`[ratel-platform] → ${event.method} ${event.path}`);
  } else if (event.phase === "response") {
    console.log(
      `[ratel-platform] ← ${event.status} ${event.method} ${event.path} (${event.durationMs}ms)`,
      event.body,
    );
  } else {
    console.log(
      `[ratel-platform] ✗ ${event.method} ${event.path} — ${event.error} (${event.durationMs}ms)`,
    );
  }
}

/**
 * Options of the management client. `apiKey`, `baseUrl`, `fetch` and `timeoutMs`
 * resolve through {@link resolvePlatformConfig}. Long-running calls (analyze,
 * generate) override `timeoutMs`.
 */
export interface PlatformClientOptions extends PlatformConfig {
  /** Log each request and its response/error to the console (never the auth
   * header). Convenience for {@link consoleLogEvent}; ignored if `logger` is set. */
  debug?: boolean;
  /** Custom log sink; receives structured {@link PlatformLogEvent}s. Takes
   * precedence over `debug`. */
  logger?: (event: PlatformLogEvent) => void;
}

export interface RequestOptions {
  query?: Record<string, string | undefined>;
  body?: unknown;
  headers?: Record<string, string>;
  /** Non-2xx statuses to return instead of throwing (e.g. 304 for conditional GET). */
  acceptStatuses?: number[];
  timeoutMs?: number;
}

export interface TransportResponse {
  status: number;
  headers: Headers;
  /** Parsed JSON body, or null when the body was empty / not JSON (e.g. 304). */
  json: unknown;
}

/**
 * Minimal fetch wrapper: base-URL join, Bearer auth, JSON in/out, typed errors.
 * No retries — mutations aren't idempotent and reads are cheap to re-issue at
 * the caller's discretion.
 */
export class Transport {
  private readonly baseUrl: string;
  private readonly apiKey: string | undefined;
  private readonly fetchImpl: typeof fetch;
  private readonly timeoutMs: number;
  private readonly log: ((event: PlatformLogEvent) => void) | undefined;

  constructor(options: PlatformClientOptions) {
    const config = resolvePlatformConfig(options);
    this.baseUrl = config.baseUrl;
    this.apiKey = config.apiKey;
    this.fetchImpl = config.fetch;
    this.timeoutMs = config.timeoutMs;
    const sink = options.logger ?? (options.debug ? consoleLogEvent : undefined);
    // Observability is best-effort: a throwing sink must not fail the request
    // or leak an untyped error past the PlatformError contract.
    this.log =
      sink &&
      ((event) => {
        try {
          sink(event);
        } catch {
          // deliberately swallowed
        }
      });
  }

  async request(
    method: string,
    path: string,
    opts: RequestOptions = {},
  ): Promise<TransportResponse> {
    if (this.apiKey === undefined || this.apiKey === "") {
      throw new PlatformError("Ratel Platform needs an API key: pass apiKey or set RATEL_API_KEY", {
        status: null,
        code: "config",
      });
    }
    let suffix = "";
    if (opts.query) {
      const params = new URLSearchParams();
      for (const [k, v] of Object.entries(opts.query)) {
        if (v !== undefined) params.set(k, v);
      }
      const qs = params.toString();
      if (qs) suffix = `?${qs}`;
    }
    const url = this.baseUrl + path + suffix;
    const loggedPath = path + suffix;

    const headers: Record<string, string> = {
      authorization: `Bearer ${this.apiKey}`,
      ...opts.headers,
    };
    let bodyInit: string | null = null;
    if (opts.body !== undefined) {
      headers["content-type"] = "application/json";
      bodyInit = JSON.stringify(opts.body);
    }

    this.log?.({ phase: "request", method, path: loggedPath, url });
    const startedAt = Date.now();

    let response: Response;
    try {
      response = await this.fetchImpl(url, {
        method,
        headers,
        body: bodyInit,
        signal: AbortSignal.timeout(opts.timeoutMs ?? this.timeoutMs),
      });
    } catch (err) {
      const message = errorMessage(err);
      this.log?.({
        phase: "error",
        method,
        path: loggedPath,
        url,
        durationMs: Date.now() - startedAt,
        error: message,
      });
      throw new PlatformError(`Ratel Platform request failed: ${message}`, {
        status: null,
        code: "network_error",
      });
    }

    let json: unknown = null;
    const text = await response.text();
    if (text.length > 0) {
      try {
        json = JSON.parse(text);
      } catch {
        json = null;
      }
    }

    this.log?.({
      phase: "response",
      method,
      path: loggedPath,
      url,
      status: response.status,
      durationMs: Date.now() - startedAt,
      body: json,
    });

    const ok =
      (response.status >= 200 && response.status < 300) ||
      (opts.acceptStatuses ?? []).includes(response.status);
    if (!ok) throw errorFromResponse(response.status, json);
    return { status: response.status, headers: response.headers, json };
  }

  /** Issue a request and return its parsed JSON body typed as `T`. */
  async json<T>(method: string, path: string, opts: RequestOptions = {}): Promise<T> {
    const res = await this.request(method, path, opts);
    return res.json as T;
  }
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}
