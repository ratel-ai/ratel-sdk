import { RESERVED_SECRET_PREFIXES, type SandboxDefinition } from "./define.js";

/** `protocol/sandbox/v1/schema/manifest.schema.json`. */
export type Manifest = {
  v: 1;
  name: string;
  entry: string;
  base: string;
  install: string[];
  secrets: string[];
  tools: Array<{ name: string; description?: string; sideEffect: boolean; fake: boolean }>;
};

export const DEFAULT_BASE = "node:24-slim";

const NAME = /^[a-z0-9][a-z0-9-]{0,62}$/;
const TOOL_NAME = /^[A-Za-z0-9_.-]{1,64}$/;
const SECRET_NAME = /^[A-Z][A-Z0-9_]{0,127}$/;
const BASE_IMAGE = /^[A-Za-z0-9][A-Za-z0-9._/:@-]*$/;

/** The manifest of a definition. Throws one error that lists each problem. */
export function toManifest(definition: SandboxDefinition): Manifest {
  const problems: string[] = [];
  const check = (ok: boolean, problem: string) => {
    if (!ok) problems.push(problem);
  };
  const { name, entry, base = DEFAULT_BASE, install = [], secrets = [], tools = {} } = definition;

  check(typeof name === "string" && NAME.test(name), "name must be letters a to z, digits and -");
  check(isInsideProject(entry), "entry must be a path inside the project folder");
  check(typeof base === "string" && BASE_IMAGE.test(base), "base must be an image name");
  check(
    install.every((command) => typeof command === "string" && command.length > 0),
    "each install command must be text",
  );
  for (const [index, secret] of secrets.entries()) {
    // The text of a bad entry can be a secret value, so the problem names its position only.
    check(
      typeof secret === "string" && SECRET_NAME.test(secret),
      `secrets[${index}] is not a name. Give the name of an environment variable, never a value`,
    );
    check(
      !RESERVED_SECRET_PREFIXES.some((prefix) => String(secret).startsWith(prefix)),
      `secrets[${index}] uses a reserved prefix (${RESERVED_SECRET_PREFIXES.join(", ")})`,
    );
  }
  check(new Set(secrets).size === secrets.length, "secrets has a name two times");
  for (const [tool, spec] of Object.entries(tools)) {
    check(TOOL_NAME.test(tool), `tool ${tool} has a name that is not valid`);
    check(typeof spec.execute === "function", `tool ${tool} has no execute function`);
    check(typeof spec.sideEffect === "boolean", `tool ${tool} must set sideEffect`);
    check(
      spec.sideEffect !== true || typeof spec.fake === "function",
      `tool ${tool} has side effects and no fake handler`,
    );
  }
  if (problems.length > 0)
    throw new Error(`The sandbox definition is not valid: ${problems.join("; ")}.`);

  return {
    v: 1,
    name,
    entry,
    base,
    install: [...install],
    secrets: [...secrets],
    tools: Object.entries(tools).map(([tool, spec]) => ({
      name: tool,
      ...(spec.description ? { description: spec.description } : {}),
      sideEffect: spec.sideEffect,
      fake: typeof spec.fake === "function",
    })),
  };
}

function isInsideProject(path: unknown): boolean {
  if (typeof path !== "string" || path.length === 0 || path.includes("\0")) return false;
  const parts = path.split(/[/\\]/);
  return parts[0] !== "" && !/^[A-Za-z]:/.test(path) && !parts.includes("..");
}
