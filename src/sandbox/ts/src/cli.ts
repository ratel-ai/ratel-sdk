#!/usr/bin/env node
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { evalCommand } from "./eval/command.js";
import { PushRefused, push } from "./push.js";
import { typeScriptFlags } from "./typescript.js";

const USAGE = `Usage: ratel sandbox push [folder] [--json]
       ratel eval run --help

Bundles the project folder (the present folder by default) and makes a new sandbox version.

  RATEL_API_KEY  a project key with the permission sandboxes:write
  RATEL_URL      the address of Ratel
`;

const EXIT = { ok: 0, refused: 1, usage: 2 } as const;

// The definition loads here with the same TypeScript flags as in the instance.
const flags = typeScriptFlags();
if (flags.every((flag) => process.execArgv.includes(flag))) {
  main(process.argv.slice(2)).then((code) => {
    process.exitCode = code;
  });
} else {
  const argv = [
    ...process.execArgv,
    ...flags,
    fileURLToPath(import.meta.url),
    ...process.argv.slice(2),
  ];
  process.exitCode = spawnSync(process.execPath, argv, { stdio: "inherit" }).status ?? EXIT.refused;
}

async function main(argv: string[]): Promise<number> {
  if (argv[0] === "eval") return evalCommand(argv.slice(1), process.env);
  let args: ReturnType<typeof parse>;
  try {
    args = parse(argv);
  } catch (error) {
    return usage((error as Error).message);
  }
  const { values, positionals } = args;
  const [group, command, folder = ".", ...rest] = positionals;
  if (values.help) {
    process.stdout.write(USAGE);
    return EXIT.ok;
  }
  if (group !== "sandbox" || command !== "push" || rest.length > 0)
    return usage("Unknown command.");
  const apiKey = process.env.RATEL_API_KEY;
  const url = process.env.RATEL_URL;
  if (!apiKey || !url) return usage(`${apiKey ? "RATEL_URL" : "RATEL_API_KEY"} is not set.`);
  if (!URL.canParse(url)) return usage("RATEL_URL is not an address.");

  try {
    const result = await push({ projectDir: folder, url, apiKey });
    process.stdout.write(
      values.json
        ? `${JSON.stringify(result)}\n`
        : `${result.name}: version ${result.versionId}\n` +
            `  commit ${result.git.commit}${result.git.dirty ? " (with changes that are not committed)" : ""}\n` +
            `  bundle ${result.bundle.files} files, ${result.bundle.bytes} bytes\n` +
            `  status ${result.status}${result.reason ? `: ${result.reason}` : ""}\n`,
    );
    return EXIT.ok;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    if (values.json) {
      const refusal = error instanceof PushRefused ? error.body : {};
      process.stdout.write(`${JSON.stringify({ error: "push_failed", ...refusal, message })}\n`);
    }
    process.stderr.write(`ratel: ${message}\n`);
    return EXIT.refused;
  }
}

function parse(argv: string[]) {
  return parseArgs({
    args: argv,
    allowPositionals: true,
    options: { json: { type: "boolean" }, help: { type: "boolean", short: "h" } },
  });
}

function usage(problem: string): number {
  process.stderr.write(`ratel: ${problem}\n\n${USAGE}`);
  return EXIT.usage;
}
