import { AsyncLocalStorage } from "node:async_hooks";
import type { RunRequest, ToolCallRecord, Usage } from "./contract.js";
import { fixtureReader } from "./tool-args.js";

/** What the code of a run can read about it: see {@link currentRun}. */
export interface RunInfo {
  /** The id of the run. */
  runId: string;
  /** The W3C trace context of the run, when Ratel sends one. */
  traceparent?: string;
}

/** @internal The state of one run, carried through its async work. */
export interface RunScope {
  readonly info: RunInfo;
  readonly tools: RunRequest["tools"];
  readonly nextFixture: ReturnType<typeof fixtureReader>;
  readonly toolCalls: ToolCallRecord[];
  usage?: Usage;
}

const storage = new AsyncLocalStorage<RunScope>();

/** The run that the caller is inside, or `undefined` outside a run of {@link serve}. */
export function currentRun(): RunInfo | undefined {
  const info = storage.getStore()?.info;
  return info ? { ...info } : undefined;
}

/**
 * Add model usage to the current run. The response sums each call. Outside a run it does nothing.
 */
export function recordUsage(usage: Usage): void {
  const scope = storage.getStore();
  if (!scope) return;
  const sum = { ...scope.usage };
  for (const key of ["inputTokens", "outputTokens", "costUsd"] as const) {
    const value = usage[key];
    if (typeof value === "number" && Number.isFinite(value)) sum[key] = (sum[key] ?? 0) + value;
  }
  scope.usage = sum;
}

/** @internal */
export function activeRun(): RunScope | undefined {
  return storage.getStore();
}

/** @internal Run `fn` inside the scope of one run request. */
export function runInScope<T>(request: RunRequest, fn: () => T): { scope: RunScope; result: T } {
  const info: RunInfo = request.traceparent
    ? { runId: request.runId, traceparent: request.traceparent }
    : { runId: request.runId };
  const scope: RunScope = {
    info,
    tools: request.tools,
    nextFixture: fixtureReader(request.fixtures),
    toolCalls: [],
  };
  return { scope, result: storage.run(scope, fn) };
}
