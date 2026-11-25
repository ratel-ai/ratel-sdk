/**
 * The process that runs the agent. The runner starts it and owns its output, so the runner can
 * redact the output and report a crash. Not a public module.
 */
import { join, resolve } from "node:path";
import { pathToFileURL } from "node:url";
import type { RunInput, ToolCall } from "./contract.js";
import type { RunFunction } from "./define.js";
import { loadDefinition } from "./loader.js";
import { toManifest } from "./manifest.js";
import { policyTools, toJson } from "./tools.js";

export type ToAgent = { projectDir: string; runInput: RunInput; traceparent: string };

export type FromAgent =
  | { type: "name"; name: string }
  | { type: "call"; call: ToolCall }
  | { type: "usage"; usage: { inputTokens?: number; outputTokens?: number; costUsd?: number } }
  | { type: "output"; output: unknown }
  | { type: "error"; error: { type: string; message: string } };

process.once("message", (message: ToAgent) => {
  run(message)
    .then((output) => send({ type: "output", output }))
    .catch((error: unknown) => send({ type: "error", error: describe(error) }))
    // Without the channel the process can end when the agent has nothing left to do.
    .finally(() => process.disconnect?.());
});

async function run({ projectDir, runInput, traceparent }: ToAgent): Promise<unknown> {
  const definition = await loadDefinition(projectDir);
  const manifest = toManifest(definition);
  await send({ type: "name", name: manifest.name });
  const entry = resolve(join(projectDir, manifest.entry));
  const module = (await import(pathToFileURL(entry).href)) as { default?: RunFunction };
  if (typeof module.default !== "function") {
    throw new Error(`${manifest.entry} has no default export function.`);
  }
  const output = await module.default({
    input: runInput.input,
    params: runInput.params,
    tools: policyTools(definition.tools ?? {}, runInput.fixtures, (call) => {
      void send({ type: "call", call });
    }),
    traceparent,
    reportUsage: (usage) => void send({ type: "usage", usage }),
  });
  return toJson(output);
}

function send(message: FromAgent): Promise<void> {
  return new Promise((done) => {
    if (!process.send) return done();
    process.send(message, () => done());
  });
}

function textOf(message: unknown): string {
  return typeof message === "string" ? message : JSON.stringify(toJson(message));
}

function describe(error: unknown): { type: string; message: string } {
  return error instanceof Error
    ? { type: error.constructor.name || "Error", message: textOf(error.message) }
    : { type: typeof error, message: String(error) };
}
