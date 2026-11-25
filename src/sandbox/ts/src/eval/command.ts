import { parseArgs } from "node:util";
import {
  type CaseSide,
  type Comparison,
  compareEvalRun,
  EVAL_EXIT,
  EvalCallError,
  type Experiment,
  exitCodeOf,
  pinBaseline,
  requireBaseline,
  startEvalRun,
  VERDICT_EXIT,
  waitForEvalRun,
} from "./run.js";

export const EVAL_USAGE = `Usage: ratel eval run [--sandbox-version <id|latest>] [--trials <n>] [--params <json>] [--json] [--no-wait] [--compare-baseline]
       ratel eval baseline pin <experiment-id> [--json]

Runs each approved case on a sandbox version and prints the counts of each case. With
--compare-baseline, prints the verdict against the pinned baseline, and it gives the exit code.

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

  let run: { experimentId: string; url: string } | null = null;
  try {
    if (options.pin) {
      const pinned = await pinBaseline(options.pin, { url, apiKey });
      if (json) print(pinned);
      else process.stdout.write(`${oneLine(pinned.baseline.experimentId)}\n`);
      return EVAL_EXIT.pass;
    }
    if (options.compare) await requireBaseline({ url, apiKey });
    const started = await startEvalRun({ ...options, url, apiKey });
    if (options.compare) run = { experimentId: started.experimentId, url: started.url };
    if (!options.wait) {
      if (json) print(started);
      else process.stdout.write(`${oneLine(started.experimentId)}\n`);
      return EVAL_EXIT.pass;
    }
    process.stderr.write(`ratel: experiment ${oneLine(started.experimentId)} started\n`);
    const experiment = await waitForEvalRun(started.experimentId, { url, apiKey });
    const exitCode = exitCodeOf(experiment);
    if (options.compare) {
      const comparison = await compareEvalRun(started.experimentId, { url, apiKey });
      if (json) print({ ...experiment, comparison });
      else process.stdout.write(report(experiment) + compared(comparison));
      return VERDICT_EXIT[comparison.verdict];
    }
    if (json) print(experiment);
    else process.stdout.write(report(experiment));
    return exitCode;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const body = error instanceof EvalCallError ? error.body : {};
    if (json) print({ error: "eval_run_failed", ...body, message, ...run });
    process.stderr.write(`ratel: ${oneLine(message)}\n`);
    return error instanceof EvalCallError ? error.exitCode : EVAL_EXIT.unavailable;
  }
}

type Options = {
  sandboxVersion: string;
  trials?: number;
  params?: Record<string, unknown>;
  wait: boolean;
  compare: boolean;
  /** The experiment that `baseline pin` pins. */
  pin?: string;
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
    compare: values["compare-baseline"] ?? false,
    help: values.help ?? false,
  };
  if (base.help) return base;
  if (positionals[0] === "baseline") {
    const [, command, pin, ...rest] = positionals;
    if (command !== "pin" || !pin || rest.length > 0) return "Unknown command.";
    const other = Object.keys(values).find((name) => name !== "json");
    return other ? `--${other} is not an option of baseline pin.` : { ...base, pin };
  }
  if (positionals.length !== 1 || positionals[0] !== "run") return "Unknown command.";
  if (base.compare && !base.wait) return "--compare-baseline needs the end of the run.";
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
      "compare-baseline": { type: "boolean" },
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
      `${oneLine(name)} (v${number}): ${counts.pass} pass, ${counts.fail} fail, ${counts.invalid} invalid`,
  );
  return `${[...lines, oneLine(url)].join("\n")}\n`;
}

function compared({ verdict, rule, url, baseline, changedCases }: Comparison): string {
  const word = `${verdict[0]?.toUpperCase()}${verdict.slice(1)}`;
  const lines = changedCases.map(({ name, change, baseline: before, candidate: after }) => {
    const sides = [before, after].filter((side) => side !== null);
    const versions = sides.map(({ number }) => `v${number}`).join(" to ");
    return `  ${oneLine(change)}: ${oneLine(name)} (${versions}): ${sides.map(passes).join(" to ")}`;
  });
  return `${[`${word} against the baseline ${oneLine(baseline.experimentId)}`, oneLine(rule), ...lines, oneLine(url)].join("\n")}\n`;
}

function passes({ counts: { pass, fail, invalid } }: CaseSide): string {
  return `${pass}/${pass + fail + invalid} pass${invalid > 0 ? `, ${invalid} invalid` : ""}`;
}

/** A name can have any character. The output goes to a terminal and to the log of a CI job. */
function oneLine(text: string): string {
  // biome-ignore lint/suspicious/noControlCharactersInRegex: these are what it removes
  return text.replace(/[\u0000-\u001f\u007f-\u009f\u2028\u2029]/g, " ");
}
