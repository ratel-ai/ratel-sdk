/** `protocol/sandbox/v1/schema/run-input.schema.json`. */
export type RunInput = {
  v: 1;
  runId: string;
  input: unknown;
  params: Record<string, unknown>;
  toolPolicy: { sideEffects: "fake" };
  fixtures: ToolFixture[];
};

export type ToolFixture = { tool: string; arguments: unknown; result: unknown };

export type ToolCall = {
  name: string;
  arguments: unknown;
  result?: unknown;
  error?: string;
  source: "real" | "fixture" | "fake";
  durationMs: number;
};

export type RunUsage = {
  durationMs: number;
  inputTokens?: number;
  outputTokens?: number;
  costUsd?: number;
};

/** `protocol/sandbox/v1/schema/run-result.schema.json`. */
export type RunResult = {
  v: 1;
  runId: string;
  status: "completed" | "failed";
  output?: unknown;
  toolCalls: ToolCall[];
  usage: RunUsage;
  traceId: string;
  error?: { type: string; message: string };
};
