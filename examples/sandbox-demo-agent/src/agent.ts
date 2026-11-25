import type { RunContext } from "@ratel-ai/sandbox";
import { answer } from "./support-agent.js";
import { flush, parentOf } from "./telemetry.js";

/** The entry point of the sandbox. The input is `{ "message": "..." }`. */
export default async function run({ input, params, tools, traceparent, reportUsage }: RunContext) {
  const { message } = input as { message?: unknown };
  if (typeof message !== "string") throw new Error('The input must be { "message": "..." }.');
  try {
    return await answer(message, {
      tools,
      parent: parentOf(traceparent),
      onUsage: reportUsage,
      ...(typeof params.model === "string" ? { model: params.model } : {}),
    });
  } finally {
    await flush();
  }
}
