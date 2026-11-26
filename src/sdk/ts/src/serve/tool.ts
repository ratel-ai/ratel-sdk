import type { ToolCallRecord, ToolMode } from "./contract.js";
import { activeRun } from "./scope.js";
import { toJson } from "./tool-args.js";

/** The functions of one tool: see {@link tool}. */
export interface ToolHandlers<A, R> {
  /** The real function. It runs in mode `live`, and outside a run. */
  execute: (args: A) => R | Promise<R>;
  /** The fake handler. It answers in mode `simulated` and never has side effects. */
  fake?: (args: A) => R | Promise<R>;
}

/** A failed tool call. `code` is `no_fake`, `no_fixture` or `tool_failed`. */
export class ToolCallError extends Error {
  /** `no_fake`, `no_fixture` or `tool_failed`. */
  readonly code: string;
  /** A failed call with its code. */
  constructor(code: string, message: string, options?: { cause?: unknown }) {
    super(message, options);
    this.name = "ToolCallError";
    this.code = code;
  }
}

/**
 * Wrap a tool so each run applies its mode. Inside a run of {@link serve}, the request sets the
 * mode (default `simulated`):
 *
 * - `simulated` answers from `fake`. With no `fake`, the call fails with the code `no_fake`.
 * - `recorded` answers from the fixture with the same tool name and normalized arguments. With
 *   no fixture, the call fails with the code `no_fixture`.
 * - `live` runs `execute`.
 *
 * The response lists each call with its mode. Outside a run, the tool runs `execute`.
 */
export function tool<A = Record<string, unknown>, R = unknown>(
  name: string,
  handlers: ToolHandlers<A, R>,
): (args: A) => Promise<R> {
  if (typeof handlers?.execute !== "function") {
    throw new TypeError(`ratel: the tool ${name} needs an execute function`);
  }
  return async (args: A) => {
    const scope = activeRun();
    if (!scope) return handlers.execute(args);
    const mode: ToolMode = scope.tools[name]?.mode ?? "simulated";
    // A copy, so later changes to the arguments do not change the record.
    const given = toJson(args ?? {}) as Record<string, unknown>;
    const record = (outcome: Pick<ToolCallRecord, "result" | "error">) =>
      scope.toolCalls.push({ name, args: given, mode, ...outcome });
    try {
      const result = await answer(name, mode, handlers, args, scope.nextFixture);
      record({ result: toJson(result) });
      return result;
    } catch (error) {
      const code = error instanceof ToolCallError ? error.code : "tool_failed";
      record({ error: { code, message: error instanceof Error ? error.message : String(error) } });
      throw error;
    }
  };
}

async function answer<A, R>(
  name: string,
  mode: ToolMode,
  handlers: ToolHandlers<A, R>,
  args: A,
  nextFixture: (tool: string, args: unknown) => { result: unknown } | undefined,
): Promise<R> {
  if (mode === "live") return handlers.execute(args);
  if (mode === "recorded") {
    const fixture = nextFixture(name, args ?? {});
    if (!fixture) {
      throw new ToolCallError(
        "no_fixture",
        `The tool ${name} is recorded and has no fixture for these arguments.`,
      );
    }
    return structuredClone(fixture.result) as R;
  }
  if (typeof handlers.fake !== "function") {
    throw new ToolCallError("no_fake", `The tool ${name} is simulated and has no fake handler.`);
  }
  return handlers.fake(args);
}
