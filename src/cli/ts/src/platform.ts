import { openAsBlob } from "node:fs";
import { PlatformError, resolvePlatformConfig, Transport } from "@ratel-ai/sdk/platform";
import { CliError, EXIT } from "./errors.js";

type Env = Record<string, string | undefined>;

/** The upload target of a new version (`create-version.response`). */
export interface UploadTarget {
  method: "PUT";
  url: string;
  headers?: Record<string, string>;
  authorize?: boolean;
}

/** A version on Ratel (`version.schema.json`). */
export interface Version {
  versionId: string;
  name: string;
  status: "uploading" | "uploaded" | "preparing" | "ready" | "failed";
  reason?: string | null;
  smoke?: {
    ok: boolean;
    output?: unknown;
    durationMs?: number;
    logs?: string;
    error?: { code: string; message: string };
  };
  url?: string;
  [key: string]: unknown;
}

/** A run on Ratel (`sandbox-run.schema.json`). */
export interface SandboxRun {
  runId: string;
  versionId: string;
  status: "queued" | "running" | "completed" | "failed";
  output?: unknown;
  toolCalls?: unknown[];
  usage?: Record<string, number>;
  error?: { code: string; message: string };
  logs?: string;
  url?: string;
  [key: string]: unknown;
}

/** The push API and the runs API of `protocol/sandbox/v1`, with the HTTP safety rules. */
export interface PlatformClient {
  createVersion(body: unknown): Promise<{ versionId: string; upload: UploadTarget }>;
  upload(target: UploadTarget, file: string): Promise<void>;
  finalize(versionId: string): Promise<Version>;
  getVersion(versionId: string): Promise<Version>;
  startRun(body: unknown): Promise<SandboxRun>;
  getRun(runId: string): Promise<SandboxRun>;
}

/**
 * A client for the Ratel of `RATEL_API_KEY` and `RATEL_BASE_URL`. A redirect is an error, and
 * the key goes only to the origin of `RATEL_BASE_URL`. No key: a usage error, before any work.
 */
export function platformClient(env: Env): PlatformClient {
  const noRedirect: typeof fetch = (input, init) => fetch(input, { ...init, redirect: "error" });
  const config = resolvePlatformConfig({ fetch: noRedirect }, env);
  if (!config.apiKey) {
    throw new CliError(
      "RATEL_API_KEY is not set. Create a project key on Ratel and set RATEL_API_KEY (and RATEL_BASE_URL for your own Ratel).",
      EXIT.usage,
      "config",
    );
  }
  if (!URL.canParse(config.baseUrl)) {
    throw new CliError(`RATEL_BASE_URL is not an address: ${config.baseUrl}`, EXIT.usage, "config");
  }
  const apiKey = config.apiKey;
  const transport = new Transport({ apiKey, baseUrl: config.baseUrl, fetch: noRedirect });
  const id = (value: string) => encodeURIComponent(value);
  return {
    createVersion: (body) => transport.json("POST", "/sandboxes/versions", { body }),
    finalize: (versionId) =>
      transport.json("POST", `/sandboxes/versions/${id(versionId)}/finalize`, { body: {} }),
    getVersion: (versionId) => transport.json("GET", `/sandboxes/versions/${id(versionId)}`),
    startRun: (body) => transport.json("POST", "/sandbox-runs", { body }),
    getRun: (runId) => transport.json("GET", `/sandbox-runs/${id(runId)}`),
    async upload(target, file) {
      const toRatel =
        target.authorize === true && new URL(target.url).origin === new URL(config.baseUrl).origin;
      const headers = {
        ...target.headers,
        ...(toRatel ? { authorization: `Bearer ${apiKey}` } : {}),
      };
      // A presigned address has a credential in its query: messages name the path only.
      const where = `${target.method} ${new URL(target.url).pathname}`;
      let response: Response;
      try {
        response = await fetch(target.url, {
          method: target.method,
          headers,
          body: await openAsBlob(file),
          redirect: "error",
        });
      } catch (error) {
        throw new CliError(
          `${where} got no answer: ${(error as Error).message}`,
          EXIT.unavailable,
          "network_error",
        );
      }
      if (!response.ok) {
        let body: unknown = null;
        try {
          body = JSON.parse(await response.text());
        } catch {
          // Not JSON: the status says enough.
        }
        throw new PlatformError(`${where} answered ${response.status}`, {
          status: response.status,
          code: response.status >= 500 ? "server_error" : "invalid",
          body,
        });
      }
    },
  };
}

/** Call `read` until `done`, with a pause between calls, or fail after the timeout. */
export async function poll<T>(
  read: () => Promise<T>,
  done: (value: T) => boolean,
  options: {
    intervalMs: number;
    timeoutMs: number;
    sleep: (ms: number) => Promise<void>;
    what: string;
  },
): Promise<T> {
  const deadline = Date.now() + options.timeoutMs;
  for (;;) {
    const value = await read();
    if (done(value)) return value;
    if (Date.now() > deadline) {
      throw new CliError(
        `${options.what} did not end in ${Math.round(options.timeoutMs / 1000)} s.`,
        EXIT.unavailable,
        "wait_timeout",
      );
    }
    await options.sleep(options.intervalMs);
  }
}
