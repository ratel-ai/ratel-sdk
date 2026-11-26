/**
 * The client error taxonomy. The v1 surface puts a machine code in `error`
 * ("not_found" | "conflict" | "invalid", per ratel-platform `lib/suggestions/v1-errors.ts`)
 * and an optional finer-grained code in `reason` ("version_conflict",
 * "not_pending", …); auth failures carry a display sentence instead. We trust
 * a code-shaped `error` body when present and fall back to the HTTP status.
 */

export type PlatformErrorCode =
  | "unauthorized"
  | "forbidden"
  | "not_found"
  | "conflict"
  | "invalid"
  | "quota_exceeded"
  | "unavailable"
  | "server_error"
  | "network_error"
  /** The client is not configured: no request was sent (for example, no API key). */
  | "config";

export class PlatformError extends Error {
  /** HTTP status, or null when the request never got a response. */
  readonly status: number | null;
  readonly code: PlatformErrorCode;
  /** Finer-grained server code, e.g. "version_conflict" | "not_pending". */
  readonly reason: string | null;
  /** The parsed JSON body of the refusal, or null when there is none. */
  readonly body: unknown;

  constructor(
    message: string,
    opts: { status: number | null; code: PlatformErrorCode; reason?: string | null; body?: unknown },
  ) {
    super(message);
    this.name = "PlatformError";
    this.status = opts.status;
    this.code = opts.code;
    this.reason = opts.reason ?? null;
    this.body = opts.body ?? null;
  }
}

const CODES_BY_STATUS: Record<number, PlatformErrorCode> = {
  400: "invalid",
  401: "unauthorized",
  403: "forbidden",
  404: "not_found",
  409: "conflict",
  402: "quota_exceeded",
  429: "quota_exceeded",
  503: "unavailable",
};

function codeForStatus(status: number): PlatformErrorCode {
  return CODES_BY_STATUS[status] ?? (status >= 500 ? "server_error" : "invalid");
}

const KNOWN_BODY_CODES: ReadonlySet<string> = new Set([
  "unauthorized",
  "forbidden",
  "not_found",
  "conflict",
  "invalid",
  "quota_exceeded",
  "unavailable",
  "server_error",
]);

/** Build the typed error for a non-2xx response body (already JSON-parsed, may be null). */
export function errorFromResponse(status: number, body: unknown): PlatformError {
  let bodyError: string | null = null;
  let reason: string | null = null;
  if (body !== null && typeof body === "object") {
    const b = body as Record<string, unknown>;
    if (typeof b.error === "string") bodyError = b.error;
    if (typeof b.reason === "string") reason = b.reason;
  }
  const code =
    bodyError !== null && KNOWN_BODY_CODES.has(bodyError)
      ? (bodyError as PlatformErrorCode)
      : codeForStatus(status);
  const detail = reason ? ` (${reason})` : "";
  // A non-code `error` is a display sentence — keep it as the message.
  const message =
    bodyError !== null && !KNOWN_BODY_CODES.has(bodyError)
      ? bodyError
      : `Ratel Platform request failed: ${status} ${code}${detail}`;
  return new PlatformError(message, { status, code, reason, body });
}
