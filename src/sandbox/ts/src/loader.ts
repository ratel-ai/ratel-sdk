import { existsSync } from "node:fs";
import { registerHooks } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import type { SandboxDefinition } from "./define.js";

/** The definition file, at the root of the project folder. */
export const DEFINITION_FILE = "ratel.sandbox.ts";

let registered = false;

/**
 * Resolves the imports of the host's TypeScript: `@ratel-ai/sandbox` is this package, also when the
 * project has not installed it, and `./x.js` or `./x` finds `./x.ts`. Node removes the types.
 */
export function registerLoader(): void {
  if (registered) return;
  registered = true;
  const self = pathToFileURL(join(dirname(fileURLToPath(import.meta.url)), "index.js")).href;
  registerHooks({
    resolve(specifier, context, next) {
      if (specifier === "@ratel-ai/sandbox") return { url: self, shortCircuit: true };
      try {
        return next(specifier, context);
      } catch (error) {
        const path = /^\.{0,2}\//.test(specifier) && context.parentURL?.startsWith("file:");
        if (!path) throw error;
        const base = specifier.replace(/\.(js|mjs)$/, "");
        for (const candidate of [`${base}.ts`, `${base}.mts`, `${base}/index.ts`]) {
          const url = new URL(candidate, context.parentURL);
          if (existsSync(url)) return next(candidate, context);
        }
        throw error;
      }
    },
  });
}

export async function loadDefinition(projectDir: string): Promise<SandboxDefinition> {
  registerLoader();
  const file = join(projectDir, DEFINITION_FILE);
  if (!existsSync(file)) throw new Error(`There is no ${DEFINITION_FILE} in ${projectDir}.`);
  const module = (await import(pathToFileURL(file).href)) as { default?: SandboxDefinition };
  if (!module.default) throw new Error(`${DEFINITION_FILE} has no default export.`);
  return module.default;
}
