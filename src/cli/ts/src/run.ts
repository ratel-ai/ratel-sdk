import { randomUUID } from "node:crypto";
import { validate } from "../protocol/verify.mjs";
import { parseInput, parseParams, parseTools } from "./args.js";
import { type LoadedConfig, loadConfig } from "./config.js";
import { checkDocker, type Docker } from "./docker.js";
import { CliError, EXIT, type ExitCode } from "./errors.js";
import { type LocalImage, resolveImage } from "./image.js";
import { logsBlock, oneLine, pretty, short } from "./output.js";
import { platformClient, poll, type SandboxRun } from "./platform.js";
import type { CommandIo } from "./push.js";

export interface RunOptions {
  file: string;
  image?: string;
  input?: string;
  params: string[];
  tools: string[];
  fixtures?: string;
  version?: string;
}

interface ErrorBody {
  code: string;
  message: string;
}

/** What `ratel run --local --json` prints: the request and the response of one run. */
export interface LocalRunResult {
  mode: "http" | "command";
  image: string;
  request: Record<string, unknown>;
  /** The response, when the container answered per the contract with 200 (or stdout). */
  response: { output: unknown; toolCalls?: unknown[]; usage?: Record<string, number> } | null;
  /** The error of the agent, or the contract error. */
  error: ErrorBody | null;
  /** Whether `error` is a contract error (the container broke the contract). */
  contractError: boolean;
  logs: string;
  durationMs: number;
}

const DEFAULT_HEALTH_TIMEOUT_MS = 30_000;

/** `ratel run --local`: start the image with local Docker and send one request per the contract. */
export async function runLocal(options: RunOptions, io: CommandIo): Promise<LocalRunResult> {
  const loaded = await loadConfig(options.file);
  const request = buildRequest(loaded, options);
  await checkDocker(io.docker);
  const image = await resolveImage(io.docker, loaded, {
    image: options.image,
    progress: io.progress,
  });
  const started = Date.now();
  const mode = loaded.config.run?.mode ?? "http";
  const result =
    mode === "command"
      ? await runCommand(io, loaded, image, request)
      : await runHttp(io, loaded, image, request);
  return { mode, image: image.ref, request, ...result, durationMs: Date.now() - started };
}

/** `ratel run`: run the newest ready version on Ratel and wait for its end. */
export async function runRemote(options: RunOptions, io: CommandIo): Promise<SandboxRun> {
  const loaded = await loadConfig(options.file);
  const { config } = loaded;
  const body = {
    ...(options.version ? { versionId: options.version } : { name: config.name }),
    input: parseInput(config, options.input),
    params: parseParams(config, options.params),
    tools: parseTools(config, options.tools),
  };
  const client = platformClient(io.env);
  const started = await client.startRun(body);
  io.progress(`› run ${oneLine(started.runId)} of version ${oneLine(started.versionId)}\n`);
  const timeoutMs = ((config.run?.timeoutSeconds ?? 300) + 300) * 1000;
  return poll(
    () => client.getRun(started.runId),
    (run) => run.status === "completed" || run.status === "failed",
    { intervalMs: 1000, timeoutMs, sleep: io.sleep, what: `The run ${started.runId}` },
  );
}

export function localExit(result: LocalRunResult): ExitCode {
  return result.error ? EXIT.failed : EXIT.ok;
}

export function remoteExit(run: SandboxRun): ExitCode {
  return run.status === "failed" ? EXIT.failed : EXIT.ok;
}

/** The output of a run for a person, on stdout. */
export function runReport(response: {
  output?: unknown;
  toolCalls?: unknown[];
  usage?: Record<string, number>;
}): string {
  let text = `${pretty(response.output ?? null)}\n`;
  const calls = (response.toolCalls ?? []) as {
    name: string;
    mode: string;
    args: unknown;
    result?: unknown;
    error?: ErrorBody;
  }[];
  if (calls.length > 0) {
    text += "\ntool calls:\n";
    for (const call of calls) {
      const answer = call.error
        ? `error ${oneLine(call.error.code)}: ${oneLine(call.error.message)}`
        : short(call.result);
      text += `  ${oneLine(call.name)} (${oneLine(call.mode)}) ${short(call.args)} -> ${answer}\n`;
    }
  }
  const usage = response.usage;
  if (usage && Object.keys(usage).length > 0) {
    const parts = [
      usage.inputTokens !== undefined ? `${usage.inputTokens} input tokens` : "",
      usage.outputTokens !== undefined ? `${usage.outputTokens} output tokens` : "",
      usage.costUsd !== undefined ? `$${usage.costUsd}` : "",
    ].filter(Boolean);
    text += `\nusage: ${parts.join(", ")}\n`;
  }
  return text;
}

/** The failure of a run for a person, on stderr. */
export function failureReport(
  error: ErrorBody,
  contract: boolean,
  logs: string | undefined,
): string {
  const title = contract ? "contract error" : "ratel";
  return `${title}: ${oneLine(error.code)}: ${oneLine(error.message)}\n${logsBlock(logs)}`;
}

function buildRequest(loaded: LoadedConfig, options: RunOptions): Record<string, unknown> {
  const { config } = loaded;
  const fixtures = options.fixtures === undefined ? [] : parseInput(config, options.fixtures);
  if (!Array.isArray(fixtures))
    throw new CliError("--fixtures must be a JSON array", EXIT.usage, "usage");
  const request = {
    v: 1,
    runId: `local-${randomUUID()}`,
    input: parseInput(config, options.input),
    params: parseParams(config, options.params),
    tools: parseTools(config, options.tools),
    fixtures,
  };
  const [problem] = validate("run-request", request);
  if (problem)
    throw new CliError(
      `The request is not valid: ${problem.path} ${problem.message}`,
      EXIT.usage,
      "usage",
    );
  return request;
}

/** `-e NAME` for each declared secret that this shell has: the value never shows in the arguments. */
function secretArgs(io: CommandIo, loaded: LoadedConfig): string[] {
  const names = loaded.config.secrets ?? [];
  const missing = names.filter((name) => !io.env[name]);
  if (missing.length > 0)
    io.progress(`› not set here, so the container gets no value: ${missing.join(", ")}\n`);
  const present = names.filter((name) => io.env[name]);
  return [
    ...present.flatMap((name) => ["-e", name]),
    ...(names.length > 0 ? ["-e", `RATEL_SECRET_NAMES=${names.join(",")}`] : []),
  ];
}

type Outcome = Pick<LocalRunResult, "response" | "error" | "contractError" | "logs">;

async function runHttp(
  io: CommandIo,
  loaded: LoadedConfig,
  image: LocalImage,
  request: Record<string, unknown>,
): Promise<Outcome> {
  const port = loaded.config.run?.port ?? 8080;
  const started = await io.docker.exec([
    "run",
    "-d",
    "-p",
    `127.0.0.1::${port}`,
    "-e",
    `PORT=${port}`,
    ...secretArgs(io, loaded),
    image.ref,
  ]);
  if (started.code !== 0) {
    throw new CliError(
      `docker run failed: ${oneLine(started.stderr)}`,
      EXIT.unavailable,
      "docker_failed",
    );
  }
  const id = started.stdout.trim();
  try {
    const outcome = await talk(io, loaded, id, port, request);
    return { ...outcome, logs: await logsOf(io.docker, id) };
  } finally {
    await io.docker.exec(["rm", "-f", id]);
  }
}

async function talk(
  io: CommandIo,
  loaded: LoadedConfig,
  id: string,
  port: number,
  request: Record<string, unknown>,
): Promise<Omit<Outcome, "logs">> {
  const contract = (code: string, message: string) => ({
    response: null,
    error: { code, message },
    contractError: true,
  });
  const mapped = await io.docker.exec(["port", id, `${port}/tcp`]);
  const hostPort = mapped.stdout.trim().split("\n")[0]?.split(":").at(-1);
  if (mapped.code !== 0 || !hostPort) {
    return contract("no_port", `The container does not publish the port ${port}.`);
  }
  const base = `http://127.0.0.1:${hostPort}`;

  const healthMs = Number(io.env.RATEL_HEALTH_TIMEOUT_MS) || DEFAULT_HEALTH_TIMEOUT_MS;
  const deadline = Date.now() + healthMs;
  io.progress(`› wait for GET /ratel/health on port ${port}\n`);
  for (;;) {
    const status = await fetch(`${base}/ratel/health`, { signal: AbortSignal.timeout(2000) })
      .then((res) => res.status)
      .catch(() => 0);
    if (status === 200) break;
    const running = await io.docker.exec(["inspect", "-f", "{{.State.Running}}", id]);
    if (running.stdout.trim() !== "true") {
      return contract("no_health", "The container stopped before GET /ratel/health answered 200.");
    }
    if (Date.now() > deadline) {
      return contract(
        "no_health",
        `GET /ratel/health did not answer 200 in ${Math.round(healthMs / 1000)} s.`,
      );
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }

  const timeoutSeconds = loaded.config.run?.timeoutSeconds ?? 300;
  io.progress(`› POST /ratel/run (${oneLine(String(request.runId))})\n`);
  let res: Response;
  try {
    res = await fetch(`${base}/ratel/run`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(request),
      signal: AbortSignal.timeout(timeoutSeconds * 1000),
    });
  } catch (error) {
    const timedOut = (error as Error).name === "TimeoutError";
    return timedOut
      ? contract("timeout", `POST /ratel/run did not answer in ${timeoutSeconds} s.`)
      : contract("no_answer", `POST /ratel/run got no answer: ${(error as Error).message}`);
  }
  const text = await res.text();
  let body: unknown;
  try {
    body = JSON.parse(text);
  } catch {
    return contract(
      "not_json",
      `POST /ratel/run answered ${res.status} with a body that is not JSON.`,
    );
  }
  if (res.status === 200) {
    const [problem] = validate("run-response", body);
    if (problem) {
      return contract(
        "invalid_response",
        `POST /ratel/run answered 200 and ${problem.path || "/"} ${problem.message}`,
      );
    }
    return { response: body as LocalRunResult["response"], error: null, contractError: false };
  }
  const [problem] = validate("error", body);
  if (problem) {
    return contract(
      "invalid_error",
      `POST /ratel/run answered ${res.status} and its body is not the error shape: ${problem.path || "/"} ${problem.message}`,
    );
  }
  return { response: null, error: (body as { error: ErrorBody }).error, contractError: false };
}

async function runCommand(
  io: CommandIo,
  loaded: LoadedConfig,
  image: LocalImage,
  request: Record<string, unknown>,
): Promise<Outcome> {
  const [entry, ...args] = loaded.config.run?.command ?? [];
  const timeoutSeconds = loaded.config.run?.timeoutSeconds ?? 300;
  const name = `ratel-run-${String(request.runId).slice(-12)}`;
  io.progress(`› ${oneLine(entry)} with the request on stdin\n`);
  const result = await io.docker.exec(
    [
      "run",
      "-i",
      "--rm",
      "--name",
      name,
      ...secretArgs(io, loaded),
      "--entrypoint",
      entry,
      image.ref,
      ...args,
    ],
    { input: JSON.stringify(request), timeoutMs: timeoutSeconds * 1000 },
  );
  const logs = result.stderr;
  if (result.code === 124) {
    await io.docker.exec(["rm", "-f", name]);
    return {
      response: null,
      error: { code: "timeout", message: `The command did not end in ${timeoutSeconds} s.` },
      contractError: true,
      logs,
    };
  }
  if (result.code !== 0) {
    return {
      response: null,
      error: { code: "run_failed", message: `The command exited with code ${result.code}.` },
      contractError: false,
      logs,
    };
  }
  const stdout = result.stdout.replace(/\n$/, "");
  let parsed: unknown;
  try {
    parsed = JSON.parse(stdout);
  } catch {
    return { response: { output: stdout }, error: null, contractError: false, logs };
  }
  if (
    parsed !== null &&
    typeof parsed === "object" &&
    !Array.isArray(parsed) &&
    "output" in parsed
  ) {
    const [problem] = validate("run-response", parsed);
    if (problem) {
      return {
        response: null,
        error: {
          code: "invalid_response",
          message: `stdout: ${problem.path || "/"} ${problem.message}`,
        },
        contractError: true,
        logs,
      };
    }
    return {
      response: parsed as LocalRunResult["response"],
      error: null,
      contractError: false,
      logs,
    };
  }
  return { response: { output: stdout }, error: null, contractError: false, logs };
}

async function logsOf(docker: Docker, id: string): Promise<string> {
  const result = await docker.exec(["logs", id]);
  return result.stdout + result.stderr;
}
