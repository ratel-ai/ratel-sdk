import type { ToolCall, ToolFixture } from "./contract.js";
import type { SandboxTool } from "./define.js";

/**
 * The tools of one run with the tool policy applied. A read-only tool runs for real. A tool with
 * side effects answers from a fixture, else from its fake handler, and never runs for real.
 */
export function policyTools(
  tools: Readonly<Record<string, SandboxTool>>,
  fixtures: readonly ToolFixture[],
  record: (call: ToolCall) => void,
): Record<string, (args?: unknown) => Promise<unknown>> {
  const nextFixture = fixtureReader(fixtures);
  return Object.fromEntries(
    Object.entries(tools).map(([name, tool]) => [
      name,
      async (args: unknown = {}) => {
        const started = performance.now();
        // A copy, so the host's later changes to the arguments do not change the record.
        const given = toJson(args);
        const fixture = tool.sideEffect === false ? undefined : nextFixture(name, given);
        const source =
          tool.sideEffect === false
            ? ("real" as const)
            : fixture
              ? ("fixture" as const)
              : ("fake" as const);
        const call = (outcome: { result: unknown } | { error: string }) =>
          record({
            name,
            arguments: given,
            ...outcome,
            source,
            durationMs: performance.now() - started,
          });
        try {
          const result = fixture
            ? structuredClone(fixture.result)
            : await (tool.sideEffect === false ? tool.execute : fakeOf(name, tool))(args);
          call({ result: toJson(result) });
          return result;
        } catch (error) {
          call({ error: error instanceof Error ? error.message : String(error) });
          throw error;
        }
      },
    ]),
  );
}

/** JSON with sorted object keys: equal arguments give equal text. */
export function normalize(value: unknown): string {
  return JSON.stringify(sortKeys(toJson(value)));
}

/** The value as JSON carries it. What JSON cannot carry becomes its text. */
export function toJson(value: unknown): unknown {
  if (value === undefined) return null;
  try {
    const text = JSON.stringify(value);
    return text === undefined ? String(value) : JSON.parse(text);
  } catch {
    return String(value);
  }
}

function fakeOf(name: string, tool: SandboxTool): (args: unknown) => unknown {
  if (typeof tool.fake !== "function") throw new Error(`tool ${name} has no fake handler`);
  return tool.fake;
}

/** Equal fixtures answer in order, and the last one repeats. */
function fixtureReader(fixtures: readonly ToolFixture[]) {
  const queues = new Map<string, ToolFixture[]>();
  for (const fixture of fixtures) {
    const key = `${fixture.tool}\n${normalize(fixture.arguments)}`;
    queues.set(key, [...(queues.get(key) ?? []), fixture]);
  }
  return (tool: string, args: unknown): ToolFixture | undefined => {
    const queue = queues.get(`${tool}\n${normalize(args)}`);
    return queue && queue.length > 1 ? queue.shift() : queue?.[0];
  };
}

function sortKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortKeys);
  if (value === null || typeof value !== "object") return value;
  return Object.fromEntries(
    Object.entries(value)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
      .map(([key, item]) => [key, sortKeys(item)]),
  );
}
