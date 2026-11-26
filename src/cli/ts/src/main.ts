import { type ParseArgsOptionsConfig, parseArgs } from "node:util";
import type { Docker } from "./docker.js";
import { type CliError, EXIT, toCliError, UsageError } from "./errors.js";
import { evalCommand } from "./eval/command.js";
import { push, pushExit, pushReport } from "./push.js";
import { failureReport, localExit, remoteExit, runLocal, runRemote, runReport } from "./run.js";

/** What the CLI reads and writes. The tests give fakes. */
export interface MainIo {
  env: Record<string, string | undefined>;
  stdout: (text: string) => void;
  stderr: (text: string) => void;
  docker: Docker;
  sleep: (ms: number) => Promise<void>;
}

export const USAGE = `Usage:
  ratel push [-f ratel.yaml] [--image ref] [--platform os/arch] [--no-wait] [--timeout seconds] [--json]
  ratel run --local [-f ratel.yaml] [--image ref] [--input json|@file] [--param name=value]...
                    [--tool name=mode]... [--fixtures @file] [--json]
  ratel run [-f ratel.yaml] [--version id] [--input json|@file] [--param name=value]...
            [--tool name=mode]... [--json]
  ratel eval run --help
  ratel eval baseline pin <experiment-id> [--json]

Push the Docker image of an agent to Ratel, or run one input on this machine or on Ratel.
ratel.yaml holds the configuration of the agent (protocol/sandbox/v1).

Environment:
  RATEL_API_KEY   a project key of Ratel (push, run without --local)
  RATEL_BASE_URL  the API root of Ratel, with /api/v1 (default https://app.ratel.sh/api/v1)

Exit codes: 0 ok, 1 the agent failed, 2 usage or ratel.yaml, 3 Ratel refused, 4 Docker or Ratel unavailable.
`;

const OPTIONS = {
  file: { type: "string", short: "f", default: "ratel.yaml" },
  image: { type: "string" },
  platform: { type: "string", default: "linux/amd64" },
  "no-wait": { type: "boolean" },
  timeout: { type: "string" },
  json: { type: "boolean" },
  local: { type: "boolean" },
  input: { type: "string" },
  param: { type: "string", multiple: true, default: [] as string[] },
  tool: { type: "string", multiple: true, default: [] as string[] },
  fixtures: { type: "string" },
  version: { type: "string" },
  help: { type: "boolean", short: "h" },
} satisfies ParseArgsOptionsConfig;

type Values = ReturnType<
  typeof parseArgs<{ args: string[]; options: typeof OPTIONS; allowPositionals: true }>
>["values"];

/** Run the CLI. It returns the exit code. */
export async function main(argv: string[], io: MainIo): Promise<number> {
  // `ratel eval` keeps the flags and the exit codes of its own contract (see the README).
  if (argv[0] === "eval") return evalCommand(argv.slice(1), io);
  let values: Values;
  let positionals: string[];
  try {
    ({ values, positionals } = parseArgs({ args: argv, options: OPTIONS, allowPositionals: true }));
  } catch (error) {
    return usage(io, (error as Error).message);
  }
  if (values.help || positionals.length === 0) {
    io.stdout(USAGE);
    return values.help ? EXIT.ok : EXIT.usage;
  }
  const [command, ...rest] = positionals;
  if (rest.length > 0) return usage(io, `Unexpected argument: ${rest[0]}`);
  const commandIo = {
    env: io.env,
    docker: io.docker,
    sleep: io.sleep,
    progress: (text: string) => io.stderr(text),
  };

  try {
    if (command === "push") {
      const timeout = values.timeout === undefined ? 900 : Number(values.timeout);
      if (!Number.isFinite(timeout) || timeout <= 0)
        throw new UsageError("--timeout must be a number of seconds");
      const result = await push(
        {
          file: values.file,
          image: values.image,
          platform: values.platform,
          wait: !values["no-wait"],
          timeoutMs: timeout * 1000,
        },
        commandIo,
      );
      io.stdout(values.json ? `${JSON.stringify(result)}\n` : pushReport(result));
      return pushExit(result);
    }
    if (command === "run") {
      const options = {
        file: values.file,
        image: values.image,
        input: values.input,
        params: values.param,
        tools: values.tool,
        fixtures: values.fixtures,
        version: values.version,
      };
      if (values.local) {
        const result = await runLocal(options, commandIo);
        if (values.json) io.stdout(`${JSON.stringify(result)}\n`);
        else if (result.response) io.stdout(runReport(result.response));
        if (result.error) io.stderr(failureReport(result.error, result.contractError, result.logs));
        return localExit(result);
      }
      const run = await runRemote(options, commandIo);
      if (values.json) io.stdout(`${JSON.stringify(run)}\n`);
      else if (run.status === "completed")
        io.stdout(`${runReport(run)}${run.url ? `${run.url}\n` : ""}`);
      if (run.status === "failed" && run.error)
        io.stderr(failureReport(run.error, false, run.logs));
      return remoteExit(run);
    }
    return usage(io, `Unknown command: ${command}`);
  } catch (error) {
    return fail(io, toCliError(error), values.json === true);
  }
}

function fail(io: MainIo, error: CliError, json: boolean): number {
  if (json)
    io.stdout(
      `${JSON.stringify({ error: error.code, message: error.message, ...error.details })}\n`,
    );
  io.stderr(`ratel: ${error.message}\n`);
  return error.exit;
}

function usage(io: MainIo, problem: string): number {
  io.stderr(`ratel: ${problem}\n\n${USAGE}`);
  return EXIT.usage;
}
