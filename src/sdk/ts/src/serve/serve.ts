import {
  createServer,
  type IncomingMessage,
  type RequestListener,
  type ServerResponse,
} from "node:http";
import type { AddressInfo } from "node:net";
import {
  ERROR_CODE,
  type ErrorBody,
  type RunRequest,
  type RunResponse,
  validateRunRequest,
} from "./contract.js";
import { createRedactor, type Redactor } from "./redact.js";
import { runInScope } from "./scope.js";

/** What {@link serve} gives to the run function of the agent. */
export interface RunArgs {
  /** Any JSON. Conversation history belongs here. */
  input: unknown;
  /** The values of the parameters that `ratel.yaml` declares. */
  params: RunRequest["params"];
  /** The mode of each tool for this run. {@link tool} applies them. */
  tools: RunRequest["tools"];
  /** The id of the run. */
  runId: string;
  /** The W3C trace context of the run, when Ratel sends one. */
  traceparent?: string;
}

/** The run function of the agent. It returns the output: any JSON. */
export type RunFn = (args: RunArgs) => unknown | Promise<unknown>;

/** Options of {@link serve}. */
export interface ServeOptions {
  /** Default: `$PORT`, else 8080. 0 picks a free port. */
  port?: number;
  /** Default: `0.0.0.0`. */
  host?: string;
  /**
   * The names of the environment variables whose values never leave the container in a
   * response. Default: the names in `$RATEL_SECRET_NAMES` (comma-separated).
   */
  secrets?: readonly string[];
  /** The largest request body, in bytes. Default: 10 MiB. */
  maxBodyBytes?: number;
}

/** A running server of {@link serve}. */
export interface ServeHandle {
  /** The port that the server listens on. */
  readonly port: number;
  /** Stop the server and close its connections. */
  close(): Promise<void>;
}

const DEFAULT_PORT = 8080;
const DEFAULT_MAX_BODY = 10 * 1024 * 1024;

/**
 * Make an agent answer the Ratel run contract (sandbox/v1, HTTP mode): `GET /ratel/health`
 * and `POST /ratel/run` on `$PORT`. One line in the entry point of the image:
 *
 * ```ts
 * serve(async ({ input, params }) => agent.reply(input, params));
 * ```
 */
export async function serve(run: RunFn, options: ServeOptions = {}): Promise<ServeHandle> {
  const server = createServer(createRunHandler(run, options));
  const port = options.port ?? portFromEnv();
  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, options.host ?? "0.0.0.0", () => {
      server.off("error", reject);
      resolve();
    });
  });
  return {
    port: (server.address() as AddressInfo).port,
    close: () =>
      new Promise<void>((resolve, reject) => {
        server.closeAllConnections();
        server.close((error) => (error ? reject(error) : resolve()));
      }),
  };
}

/**
 * The request listener of {@link serve}, for a server that you create yourself.
 */
export function createRunHandler(
  run: RunFn,
  options: Omit<ServeOptions, "port" | "host"> = {},
): RequestListener {
  const redactor = createRedactor(secretValues(options.secrets));
  const maxBody = options.maxBodyBytes ?? DEFAULT_MAX_BODY;
  return (req, res) => {
    handle(req, res, run, redactor, maxBody).catch((error) => {
      send(res, 500, redactor, failure("run_failed", error));
    });
  };
}

async function handle(
  req: IncomingMessage,
  res: ServerResponse,
  run: RunFn,
  redactor: Redactor,
  maxBody: number,
): Promise<void> {
  const path = (req.url ?? "/").split("?")[0];
  if (path === "/ratel/health") {
    if (req.method !== "GET" && req.method !== "HEAD")
      return refuse(res, redactor, 405, "method_not_allowed", req);
    return send(res, 200, redactor, { ok: true });
  }
  if (path !== "/ratel/run") {
    return send(res, 404, redactor, { error: { code: "not_found", message: `No route ${path}.` } });
  }
  if (req.method !== "POST") return refuse(res, redactor, 405, "method_not_allowed", req);

  const text = await readBody(req, maxBody);
  if (text === undefined) {
    return send(res, 413, redactor, {
      error: { code: "invalid_request", message: `The body is larger than ${maxBody} bytes.` },
    });
  }
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    return send(res, 400, redactor, {
      error: { code: "invalid_request", message: "The body is not JSON." },
    });
  }
  const errors = validateRunRequest(body);
  if (errors.length > 0) {
    const [first] = errors;
    return send(res, 400, redactor, {
      error: { code: "invalid_request", message: `${first.path || "/"} ${first.message}` },
    });
  }

  const request = body as RunRequest;
  const { input, params, tools, runId, traceparent } = request;
  const args: RunArgs = traceparent
    ? { input, params, tools, runId, traceparent }
    : { input, params, tools, runId };
  const { scope, result } = runInScope(request, () => Promise.resolve().then(() => run(args)));
  try {
    const output = await result;
    const response: RunResponse = {
      output: output === undefined ? null : output,
      toolCalls: scope.toolCalls,
    };
    if (scope.usage) response.usage = scope.usage;
    send(res, 200, redactor, response);
  } catch (error) {
    send(res, 500, redactor, failure("run_failed", error));
  }
}

function refuse(
  res: ServerResponse,
  redactor: Redactor,
  status: number,
  code: string,
  req: IncomingMessage,
) {
  send(res, status, redactor, {
    error: { code, message: `${req.method} is not allowed on ${req.url}.` },
  });
}

function failure(fallback: string, error: unknown): { error: ErrorBody } {
  const code = (error as { code?: unknown } | null)?.code;
  const message = error instanceof Error ? error.message : String(error);
  return {
    error: {
      code: typeof code === "string" && ERROR_CODE.test(code) ? code : fallback,
      message: message || "The run failed.",
    },
  };
}

function send(res: ServerResponse, status: number, redactor: Redactor, body: unknown): void {
  if (res.headersSent) return;
  let text: string;
  try {
    text = JSON.stringify(redactor.json(body));
  } catch (error) {
    status = 500;
    text = JSON.stringify(redactor.json(failure("invalid_response", error)));
  }
  res.writeHead(status, { "content-type": "application/json; charset=utf-8" });
  res.end(text);
}

function readBody(req: IncomingMessage, maxBody: number): Promise<string | undefined> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = [];
    let size = 0;
    let tooLarge = false;
    req.on("data", (chunk: Buffer) => {
      size += chunk.length;
      if (size > maxBody) tooLarge = true;
      else chunks.push(chunk);
    });
    req.on("end", () => resolve(tooLarge ? undefined : Buffer.concat(chunks).toString("utf8")));
    req.on("error", reject);
  });
}

function portFromEnv(): number {
  const raw = process.env.PORT;
  if (raw === undefined || raw === "") return DEFAULT_PORT;
  const port = Number(raw);
  if (!Number.isInteger(port) || port < 0 || port > 65535) {
    throw new RangeError(`ratel: PORT must be a port number, got ${JSON.stringify(raw)}`);
  }
  return port;
}

function secretValues(names?: readonly string[]): string[] {
  const list =
    names ??
    (process.env.RATEL_SECRET_NAMES ?? "")
      .split(",")
      .map((name) => name.trim())
      .filter(Boolean);
  return list.map((name) => process.env[name]).filter((value): value is string => !!value);
}
