#!/usr/bin/env node
import { parseArgs } from "node:util";
import { PushRefused, push } from "./push.js";

const USAGE = `Usage: ratel sandbox push [folder] [--json]

Bundles the project folder (the present folder by default) and makes a new sandbox version.

  RATEL_API_KEY  a project key with the permission sandboxes:write
  RATEL_URL      the address of Ratel
`;

const EXIT = { ok: 0, refused: 1, usage: 2 } as const;

main(process.argv.slice(2)).then((code) => {
  process.exitCode = code;
});

async function main(argv: string[]): Promise<number> {
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
