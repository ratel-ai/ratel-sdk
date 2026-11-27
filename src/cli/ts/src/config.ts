import { readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { LineCounter, parseDocument } from "yaml";
import { validate } from "../protocol/verify.mjs";
import { CliError, EXIT } from "./errors.js";

/** The parsed `ratel.yaml` (`protocol/sandbox/v1/schema/ratel-yaml.schema.json`). */
export interface RatelConfig {
  name: string;
  image?: { dockerfile?: string; context?: string } | { ref: string };
  run?: { mode?: "http" | "command"; port?: number; command?: string[]; timeoutSeconds?: number };
  input: { example: unknown; schema?: Record<string, unknown> };
  params?: Record<string, ParamSpec>;
  secrets?: string[];
  tools?: Record<string, { mode?: ToolMode }>;
  network?: { allow?: string[] };
}

export type ToolMode = "simulated" | "recorded" | "live";

export interface ParamSpec {
  type: "string" | "number" | "boolean" | "enum";
  default?: string | number | boolean;
  description?: string;
  options?: (string | number)[];
}

/** A `ratel.yaml` that was read and validated. */
export interface LoadedConfig {
  /** The absolute path of the file. */
  file: string;
  /** The folder of the file: paths in it are relative to this folder. */
  dir: string;
  config: RatelConfig;
}

/** A wrong `ratel.yaml`. The message starts with `file:line:column:` when the line is known. */
export class ConfigError extends CliError {
  constructor(
    message: string,
    readonly path: string,
  ) {
    super(message, EXIT.usage, "invalid_config", { path });
    this.name = "ConfigError";
  }
}

/** Read, parse and validate a `ratel.yaml`. It throws a {@link ConfigError} with the path of the wrong field. */
export async function loadConfig(file = "ratel.yaml"): Promise<LoadedConfig> {
  const absolute = resolve(file);
  let text: string;
  try {
    text = await readFile(absolute, "utf8");
  } catch (error) {
    const missing = (error as NodeJS.ErrnoException).code === "ENOENT";
    throw new ConfigError(
      `${file}: ${missing ? "the file does not exist" : (error as Error).message}`,
      "",
    );
  }
  const lines = new LineCounter();
  const doc = parseDocument(text, { lineCounter: lines, prettyErrors: false });
  const [syntax] = doc.errors;
  if (syntax) {
    const at = lines.linePos(syntax.pos[0]);
    throw new ConfigError(`${file}:${at.line}:${at.col}: ${syntax.message.split("\n")[0]}`, "");
  }
  const config: unknown = doc.toJS();
  const [problem] = validate("ratel-yaml", config ?? {});
  if (problem) {
    const at = lineOf(doc, lines, problem.path);
    throw new ConfigError(`${file}${at}: ${problem.path || "/"} ${problem.message}`, problem.path);
  }
  return { file: absolute, dir: dirname(absolute), config: config as RatelConfig };
}

/** `:line:column` of the field at a JSON pointer, or of its nearest parent in the file. */
function lineOf(
  doc: ReturnType<typeof parseDocument>,
  lines: LineCounter,
  pointer: string,
): string {
  const parts = pointer
    .split("/")
    .slice(1)
    .map((part) => part.replaceAll("~1", "/").replaceAll("~0", "~"))
    .map((part) => (/^\d+$/.test(part) ? Number(part) : part));
  for (let n = parts.length; n >= 0; n--) {
    const path = parts.slice(0, n);
    // The key of a map entry has the better line: it is where the field starts.
    const node = n > 0 ? keyNode(doc, path) : doc.contents;
    const offset = (node as { range?: [number, number, number] } | null | undefined)?.range?.[0];
    if (offset !== undefined) {
      const at = lines.linePos(offset);
      return `:${at.line}:${at.col}`;
    }
  }
  return "";
}

function keyNode(doc: ReturnType<typeof parseDocument>, path: (string | number)[]): unknown {
  const parent = path.length === 1 ? doc.contents : doc.getIn(path.slice(0, -1), true);
  const last = path.at(-1);
  const items = (parent as { items?: unknown[] } | null)?.items;
  if (!items) return undefined;
  if (typeof last === "number") return items[last];
  const pair = items.find((item) => {
    const key = (item as { key?: { value?: unknown } }).key;
    return key?.value === last;
  }) as { key?: unknown } | undefined;
  return pair?.key;
}
