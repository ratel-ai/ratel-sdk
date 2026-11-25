import { parseArgs } from "node:util";
import {
  EVAL_EXIT,
  EvalCallError,
  type Experiment,
  exitCodeOf,
  startEvalRun,
  waitForEvalRun,
} from "./run.js";

export const EVAL_USAGE = `Usage: ratel eval run [--sandbox-version <id|latest>] [--trials <n>] [--params <json>] [--json] [--no-wait]

Runs each approved case on a sandbox version and prints the counts of each case.

  RATEL_API_KEY  a project key with the permissions evals:read and evals:write
  RATEL_URL      the address of Ratel
`;

const MAX_TRIALS = 20;

/** `ratel eval ...`. Returns the exit code. */
export async function evalCommand(argv: string[], env: NodeJS.ProcessEnv): Promise<number> {
  const json = argv.some((arg) => arg === "--json" || arg.startsWith("--json="));
  const print = (value: unknown) => process.stdout.write(`${JSON.stringify(value)}\n`);
  const usage = (problem: string) => {
    if (json) print({ error: "usage", message: problem });
    process.stderr.write(`ratel: ${problem}\n\n${EVAL_USAGE}`);
    return EVAL_EXIT.refused;
  };
  const options = read(argv);
  if (typeof options === "string") return usage(options);
  if (options.help) {
    // With --json the standard output has one document or nothing.
    (json ? process.stderr : process.stdout).write(EVAL_USAGE);
    return EVAL_EXIT.pass;
  }
  const { RATEL_API_KEY: apiKey, RATEL_URL: url } = env;
  if (!apiKey || !url) return usage(`${apiKey ? "RATEL_URL" : "RATEL_API_KEY"} is not set.`);
  if (!isAddress(url)) return usage("RATEL_URL is not an http or https address.");
  if (!/^[\x21-\x7e]+$/.test(apiKey)) return usage("RATEL_API_KEY is not a key.");

  try {
    const started = await startEvalRun({ ...options, url, apiKey });
    if (!options.wait) {
      if (json) print(started);
      else process.stdout.write(`${started.experimentId}\n`);
      return EVAL_EXIT.pass;
    }
    process.stderr.write(`ratel: experiment ${started.experimentId} started\n`);
    const experiment = await waitForEvalRun(started.experimentId, { url, apiKey });
    const exitCode = exitCodeOf(experiment);
    if (json) print(experiment);
    else process.stdout.write(report(experiment));
    return exitCode;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const body = error instanceof EvalCallError ? error.body : {};
    if (json) print({ error: "eval_run_failed", ...body, message });
    process.stderr.write(`ratel: ${message}\n`);
    return error instanceof EvalCallError ? error.exitCode : EVAL_EXIT.unavailable;
  }
}

type Options = {
  sandboxVersion: string;
  trials?: number;
  params?: Record<string, unknown>;
  wait: boolean;
  help: boolean;
};

/** The options, or what is wrong with them. */
function read(argv: string[]): Options | string {
  let parsed: ReturnType<typeof parse>;
  try {
    parsed = parse(argv);
  } catch (error) {
    return (error as Error).message;
  }
  const { values, positionals } = parsed;
  const base = {
    sandboxVersion: values["sandbox-version"] ?? "latest",
    wait: !values["no-wait"],
    help: values.help ?? false,
  };
  if (base.help) return base;
  if (positionals.length !== 1 || positionals[0] !== "run") return "Unknown command.";
  const trials = values.trials === undefined ? undefined : Number(values.trials);
  if (trials !== undefined && !(Number.isInteger(trials) && trials >= 1 && trials <= MAX_TRIALS)) {
    return `--trials is a whole number from 1 to ${MAX_TRIALS}.`;
  }
  const params = values.params === undefined ? undefined : object(values.params);
  if (params === null) return "--params is a JSON object.";
  return { ...base, ...(trials ? { trials } : {}), ...(params ? { params } : {}) };
}

function parse(argv: string[]) {
  return parseArgs({
    args: argv,
    allowPositionals: true,
    options: {
      "sandbox-version": { type: "string" },
      trials: { type: "string" },
      params: { type: "string" },
      json: { type: "boolean" },
      "no-wait": { type: "boolean" },
      help: { type: "boolean", short: "h" },
    },
  });
}

function isAddress(text: string): boolean {
  if (!URL.canParse(text)) return false;
  const { protocol, username, password } = new URL(text);
  return ["http:", "https:"].includes(protocol) && !username && !password;
}

function object(text: string): Record<string, unknown> | null {
  try {
    const value: unknown = JSON.parse(text);
    return typeof value === "object" && value !== null && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

function report({ cases, url }: Experiment): string {
  const lines = cases.map(
    ({ name, number, counts }) =>
      `${name} (v${number}): ${counts.pass} pass, ${counts.fail} fail, ${counts.invalid} invalid`,
  );
  return `${[...lines, url].join("\n")}\n`;
}
