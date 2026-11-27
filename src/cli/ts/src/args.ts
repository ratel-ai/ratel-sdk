import { readFileSync } from "node:fs";
import type { RatelConfig, ToolMode } from "./config.js";
import { UsageError } from "./errors.js";

export { UsageError };

export type ParamValue = string | number | boolean;

const MODES: readonly ToolMode[] = ["simulated", "recorded", "live"];

/** The defaults of the declared parameters, with each `--param name=value` applied and typed. */
export function parseParams(
  config: RatelConfig,
  flags: readonly string[],
): Record<string, ParamValue> {
  const declared = config.params ?? {};
  const params: Record<string, ParamValue> = {};
  for (const [name, spec] of Object.entries(declared)) {
    if (spec.default !== undefined) params[name] = spec.default;
  }
  for (const flag of flags) {
    const [name, value] = split("--param", flag);
    const spec = declared[name];
    if (!spec) {
      const names = Object.keys(declared);
      throw new UsageError(
        `--param ${name}: ratel.yaml declares no such parameter${names.length ? ` (${names.join(", ")})` : ""}`,
      );
    }
    params[name] = typed(name, spec, value);
  }
  return params;
}

/**
 * The `--param` flags of an eval run: a value with commas is a list of values for the matrix
 * (`--param tone=dry,warm`), each one typed as ratel.yaml declares it. The other flags stay as
 * they are, for `parseParams`. A list has each value one time.
 */
export function parseMatrix(
  config: RatelConfig,
  flags: readonly string[],
): { single: string[]; matrix: Record<string, ParamValue[]> } {
  const declared = config.params ?? {};
  const single: string[] = [];
  const matrix: Record<string, ParamValue[]> = {};
  for (const flag of flags) {
    const [name, value] = split("--param", flag);
    if (!value.includes(",")) {
      single.push(flag);
      continue;
    }
    const spec = declared[name];
    if (!spec) {
      const names = Object.keys(declared);
      throw new UsageError(
        `--param ${name}: ratel.yaml declares no such parameter${names.length ? ` (${names.join(", ")})` : ""}`,
      );
    }
    const values = value.split(",").map((one) => typed(name, spec, one));
    if (new Set(values).size !== values.length) {
      throw new UsageError(`--param ${name}: a value is given two times`);
    }
    matrix[name] = values;
  }
  return { single, matrix };
}

/** The input of a run: `--input` as JSON (else as text), `@file`, or `input.example`. */
export function parseInput(config: RatelConfig, flag: string | undefined): unknown {
  if (flag === undefined) return config.input.example;
  let text = flag;
  if (flag.startsWith("@")) {
    try {
      text = readFileSync(flag.slice(1), "utf8");
    } catch (error) {
      const missing = (error as NodeJS.ErrnoException).code === "ENOENT";
      throw new UsageError(
        `--input ${flag}: ${missing ? "the file does not exist" : (error as Error).message}`,
      );
    }
  }
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

/** The mode of each declared tool (`simulated` by default), with each `--tool name=mode` applied. */
export function parseTools(
  config: RatelConfig,
  flags: readonly string[],
): Record<string, { mode: ToolMode }> {
  const tools: Record<string, { mode: ToolMode }> = {};
  for (const [name, spec] of Object.entries(config.tools ?? {}))
    tools[name] = { mode: spec.mode ?? "simulated" };
  for (const flag of flags) {
    const [name, mode] = split("--tool", flag);
    if (!MODES.includes(mode as ToolMode)) {
      throw new UsageError(`--tool ${name}: the mode must be simulated, recorded or live`);
    }
    tools[name] = { mode: mode as ToolMode };
  }
  return tools;
}

function split(flag: string, text: string): [string, string] {
  const at = text.indexOf("=");
  if (at <= 0) throw new UsageError(`${flag} ${text}: write name=value`);
  return [text.slice(0, at), text.slice(at + 1)];
}

function typed(
  name: string,
  spec: { type: string; options?: (string | number)[] },
  value: string,
): ParamValue {
  switch (spec.type) {
    case "number": {
      const number = Number(value);
      if (value.trim() === "" || !Number.isFinite(number))
        throw new UsageError(`--param ${name}: must be a number`);
      return number;
    }
    case "boolean":
      if (value === "true") return true;
      if (value === "false") return false;
      throw new UsageError(`--param ${name}: must be true or false`);
    case "enum": {
      const option = spec.options?.find((o) => String(o) === value);
      if (option === undefined)
        throw new UsageError(`--param ${name}: must be one of ${spec.options?.join(", ")}`);
      return option;
    }
    default:
      return value;
  }
}
