import { fork } from "node:child_process";
import { fileURLToPath } from "node:url";
import { gunzipSync } from "node:zlib";
import type { FromAgent, ToAgent } from "./agent-process.js";
import type { RunInput, RunResult, RunUsage, ToolCall } from "./contract.js";
import { createRedactor, type Redactor, redactLines } from "./redact.js";
import { newTrace, rootSpan, traceparent } from "./trace.js";
import { typeScriptFlags } from "./typescript.js";

export type RunnerOptions = {
  /** The environment of the instance: `RATEL_URL`, `RATEL_RUN_TOKEN`, `RATEL_RUN_INPUT`, secrets. */
  env: Readonly<Record<string, string | undefined>>;
  /** The project folder, with `ratel.sandbox.ts`. */
  projectDir: string;
  /** The names of the secrets in `env`. */
  secretNames: readonly string[];
  stdout?: (text: string) => void;
  stderr?: (text: string) => void;
  /** The wait before each new try of a post, in milliseconds. */
  retryDelaysMs?: readonly number[];
};

/** Ratel refuses a larger result. */
export const MAX_RESULT_BYTES = 4 * 1024 * 1024;

/** The depth of the output and of the tool calls. Ratel does not store a much deeper value. */
const MAX_DEPTH = 500;
const TOO_DEEP = {
  type: "ResultTooDeep",
  message: `The output or a tool call has more than ${MAX_DEPTH} levels.`,
};

/** How long the agent process has to flush and exit after it gave its output. */
const EXIT_GRACE_MS = 10_000;
/** How long the output of an ended agent process can still come. */
const DRAIN_MS = 1_000;
const POST_TIMEOUT_MS = 30_000;
const TRACE_TIMEOUT_MS = 5_000;
const RETRY_DELAYS_MS = [1_000, 2_000, 4_000, 8_000];

/**
 * Runs the agent of the project one time and posts the result. It posts a failed result when the
 * agent throws or its process ends early. It rejects when it cannot post the result.
 */
export async function runSandbox(options: RunnerOptions): Promise<RunResult> {
  const { env } = options;
  const url = required(env, "RATEL_URL").replace(/\/+$/, "");
  const token = required(env, "RATEL_RUN_TOKEN");
  const runInput = decodeRunInput(required(env, "RATEL_RUN_INPUT"));
  const redactor = createRedactor([
    token,
    ...options.secretNames.flatMap((name) => env[name] ?? []),
  ]);
  const ids = newTrace();
  const startMs = Date.now();

  const agent = await runAgent(options, runInput, traceparent(ids), redactor, { url, token });
  // Only what the agent wrote is redacted: a secret can be equal to a part of a tool name.
  const deep = isDeep([agent.output, agent.toolCalls]);
  const result = bounded({
    v: 1,
    runId: runInput.runId,
    ...(deep
      ? { status: "failed", error: TOO_DEEP, toolCalls: [] }
      : {
          ...(agent.error
            ? { status: "failed", error: redactor.json(agent.error) }
            : { status: "completed", output: redactor.json(agent.output) }),
          toolCalls: agent.toolCalls.map((call) => redactCall(call, redactor)),
        }),
    // The time until the answer. What the agent flushes after it is not its latency.
    usage: { ...agent.usage, durationMs: agent.endMs - startMs },
    traceId: ids.traceId,
  });

  const span = rootSpan(ids, {
    name: agent.name,
    startMs,
    endMs: agent.endMs,
    input: redactor.json(runInput.input),
    output: result.output,
    runId: runInput.runId,
    ...(result.error ? { error: result.error } : {}),
  });
  const delays = options.retryDelaysMs ?? RETRY_DELAYS_MS;
  // A lost span must not lose the result.
  await post(`${url}/v1/traces`, token, span, [], TRACE_TIMEOUT_MS).catch(() => {
    (options.stderr ?? writeStderr)("ratel: the trace of the run was not sent\n");
  });
  await post(`${url}/api/v1/sandbox-runs/${runInput.runId}/result`, token, result, delays);
  return result;
}

type AgentOutcome = {
  name: string;
  /** When the agent answered, or when its process ended with no answer. */
  endMs: number;
  output?: unknown;
  error?: { type: string; message: string };
  toolCalls: ToolCall[];
  usage: Omit<RunUsage, "durationMs">;
};

function runAgent(
  options: RunnerOptions,
  runInput: RunInput,
  parent: string,
  redactor: Redactor,
  ratel: { url: string; token: string },
): Promise<AgentOutcome> {
  return new Promise((done) => {
    const outcome: AgentOutcome = { name: "sandbox", endMs: 0, toolCalls: [], usage: {} };
    let answered = false;
    const child = fork(fileURLToPath(new URL("./agent-process.js", import.meta.url)), [], {
      cwd: options.projectDir,
      env: agentEnv(options.env, parent, ratel),
      execArgv: typeScriptFlags(),
      // A process group of its own, so what the agent started ends with it.
      detached: true,
      stdio: ["ignore", "pipe", "pipe", "ipc"],
      serialization: "json",
    });
    const out = redactLines(redactor, options.stdout ?? writeStdout);
    const err = redactLines(redactor, options.stderr ?? writeStderr);
    child.stdout?.setEncoding("utf8").on("data", (chunk: string) => out.push(chunk));
    child.stderr?.setEncoding("utf8").on("data", (chunk: string) => err.push(chunk));

    let grace: NodeJS.Timeout | undefined;
    let drain: NodeJS.Timeout | undefined;
    child.on("message", (sent) => {
      // Code of the host can send its own messages on this channel.
      const message = sent as FromAgent | null;
      if (message?.type === "name") outcome.name = message.name;
      else if (message?.type === "call" && isCall(message.call))
        outcome.toolCalls.push(message.call);
      else if (message?.type === "usage") outcome.usage = addUsage(outcome.usage, message.usage);
      else if (message?.type === "output" || (message?.type === "error" && message.error)) {
        answered = true;
        outcome.endMs ||= Date.now();
        if (message.type === "output") outcome.output = message.output;
        else outcome.error = textError(message.error);
        // The agent may flush its telemetry before it ends.
        grace ??= setTimeout(() => child.kill("SIGKILL"), EXIT_GRACE_MS);
      }
    });
    child.on("exit", () => {
      endGroup(child.pid);
      // A process that left the group still holds the pipes.
      drain = setTimeout(() => {
        child.stdout?.destroy();
        child.stderr?.destroy();
      }, DRAIN_MS);
    });
    child.on("error", (error) => {
      outcome.error ??= { type: "AgentProcessError", message: error.message };
    });
    child.on("close", (code, signal) => {
      clearTimeout(grace);
      clearTimeout(drain);
      out.end();
      err.end();
      outcome.endMs ||= Date.now();
      if (!answered) {
        outcome.error ??= {
          type: "AgentExit",
          message: `The agent process ended before it gave an output (${signal ?? `exit code ${code}`}).`,
        };
      }
      done(outcome);
    });
    const message: ToAgent = { projectDir: options.projectDir, runInput, traceparent: parent };
    child.send(message, () => {});
  });
}

/** The agent can send its own messages, and an error can have any message. */
function textError({ type, message }: { type: unknown; message: unknown }) {
  return { type: textOf(type) || "Error", message: textOf(message) };
}

function textOf(value: unknown): string {
  if (typeof value === "string") return value;
  return value === undefined ? "" : JSON.stringify(value);
}

function isCall(call: ToolCall | undefined): call is ToolCall {
  return typeof call?.name === "string" && ["real", "fixture", "fake"].includes(call.source);
}

/** A walk with no recursion, because the value can be deeper than the stack. */
function isDeep(value: unknown): boolean {
  let level: unknown[] = [value];
  for (let depth = 0; level.length > 0; depth += 1) {
    if (depth > MAX_DEPTH) return true;
    level = level.flatMap((item) =>
      item !== null && typeof item === "object" ? Object.values(item) : [],
    );
  }
  return false;
}

/** Ends what the agent process left behind. */
function endGroup(pid: number | undefined): void {
  try {
    if (pid) process.kill(-pid, "SIGKILL");
  } catch {
    // The group is empty.
  }
}

function redactCall(call: ToolCall, redactor: Redactor): ToolCall {
  const { result, error } = call;
  return {
    ...call,
    arguments: redactor.json(call.arguments),
    ...(result === undefined ? {} : { result: redactor.json(result) }),
    ...(error === undefined ? {} : { error: redactor.text(error) }),
  };
}

/** The agent sees its secrets and where to send traces, with the run token. */
function agentEnv(
  env: RunnerOptions["env"],
  parent: string,
  { url, token }: { url: string; token: string },
): NodeJS.ProcessEnv {
  const { RATEL_RUN_INPUT: _input, RATEL_RUN_TOKEN: _token, ...rest } = env;
  const traces = `${url}/v1/traces`;
  return {
    ...rest,
    TRACEPARENT: parent,
    RATEL_OTLP_ENDPOINT: traces,
    RATEL_API_KEY: token,
    OTEL_EXPORTER_OTLP_TRACES_ENDPOINT: traces,
    OTEL_EXPORTER_OTLP_TRACES_HEADERS: `authorization=${encodeURIComponent(`Bearer ${token}`)}`,
  };
}

function bounded(result: RunResult): RunResult {
  const bytes = Buffer.byteLength(JSON.stringify(result));
  if (bytes <= MAX_RESULT_BYTES) return result;
  const { output: _output, ...rest } = result;
  return {
    ...rest,
    status: "failed",
    toolCalls: [],
    error: {
      type: "ResultTooLarge",
      message: `The result has ${bytes} bytes. The maximum is ${MAX_RESULT_BYTES}.`,
    },
  };
}

async function post(
  url: string,
  token: string,
  body: unknown,
  delays: readonly number[],
  timeoutMs = POST_TIMEOUT_MS,
): Promise<void> {
  for (let attempt = 0; ; attempt++) {
    const failure = await send(url, token, body, timeoutMs);
    if (!failure) return;
    const delay = delays[attempt];
    if (!failure.retry || delay === undefined) throw new Error(failure.message);
    await new Promise((resolve) => setTimeout(resolve, delay));
  }
}

async function send(url: string, token: string, body: unknown, timeoutMs: number) {
  const path = new URL(url).pathname;
  try {
    const response = await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: `Bearer ${token}` },
      body: JSON.stringify(body),
      redirect: "error",
      signal: AbortSignal.timeout(timeoutMs),
    });
    await response.arrayBuffer();
    if (response.ok) return undefined;
    const retry = response.status === 429 || response.status >= 500;
    return { retry, message: `POST ${path} answered ${response.status}.` };
  } catch (error) {
    return { retry: true, message: `POST ${path} failed: ${(error as Error).name}.` };
  }
}

function decodeRunInput(text: string): RunInput {
  const runInput = JSON.parse(gunzipSync(Buffer.from(text, "base64")).toString("utf8")) as RunInput;
  if (runInput.v !== 1)
    throw new Error("RATEL_RUN_INPUT has a version that this runner does not know.");
  if (runInput.toolPolicy?.sideEffects !== "fake")
    throw new Error("RATEL_RUN_INPUT has no valid tool policy.");
  return runInput;
}

function addUsage(a: AgentOutcome["usage"], b: AgentOutcome["usage"]): AgentOutcome["usage"] {
  const sum = { ...a };
  for (const key of ["inputTokens", "outputTokens", "costUsd"] as const) {
    const value = b?.[key];
    if (typeof value === "number" && Number.isFinite(value) && value >= 0)
      sum[key] = (sum[key] ?? 0) + value;
  }
  return sum;
}

function required(env: RunnerOptions["env"], name: string): string {
  const value = env[name];
  if (!value) throw new Error(`${name} is not set.`);
  return value;
}

const writeStdout = (text: string) => void process.stdout.write(text);
const writeStderr = (text: string) => void process.stderr.write(text);
