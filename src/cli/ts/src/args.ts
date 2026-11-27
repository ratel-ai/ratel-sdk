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
    params[name] = typed(name, declaredSpec(config, name), value);
  }
  return params;
}

/**
 * The `--param` flags of an eval run. A value with commas is a list: each item is typed as
 * `parseParams` types a value, and the parameter goes to `matrix`, not to `params` (with its default).
 */
export function parseParamFlags(
  config: RatelConfig,
  flags: readonly string[],
): { params: Record<string, ParamValue>; matrix: Record<string, ParamValue[]> } {
  const isList = (flag: string) => flag.slice(flag.indexOf("=") + 1).includes(",");
  const params = parseParams(
    config,
    flags.filter((flag) => !isList(flag)),
  );
  const matrix: Record<string, ParamValue[]> = {};
  for (const flag of flags.filter(isList)) {
    const [name, values] = split("--param", flag);
    const spec = declaredSpec(config, name);
    matrix[name] = values.split(",").map((value) => typed(name, spec, value));
    delete params[name];
  }
  return { params, matrix };
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

/** The declaration of a parameter in `ratel.yaml`, or a usage error. */
function declaredSpec(config: RatelConfig, name: string) {
  const declared = config.params ?? {};
  const spec = declared[name];
  if (spec) return spec;
  const names = Object.keys(declared);
  throw new UsageError(
    `--param ${name}: ratel.yaml declares no such parameter${names.length ? ` (${names.join(", ")})` : ""}`,
  );
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
