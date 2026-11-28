import type { ToolFixture } from "./contract.js";

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

/** The fixture for a call. Equal fixtures answer in order, and the last one repeats. */
export function fixtureReader(fixtures: readonly ToolFixture[]) {
  const queues = new Map<string, ToolFixture[]>();
  for (const fixture of fixtures) {
    const key = `${fixture.tool}\n${normalize(fixture.args)}`;
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
