import { existsSync } from "node:fs";
import { parseArgs } from "node:util";
import { resolvePlatformConfig } from "@ratel-ai/sdk/platform";
import { type ParamValue, parseParams } from "../args.js";
import { loadConfig } from "../config.js";
import { CliError } from "../errors.js";
import { oneLine } from "../output.js";
import {
  type CaseSide,
  type Comparison,
  compareEvalRun,
  EVAL_EXIT,
  EvalCallError,
  type EvalRow,
  type Experiment,
  evalRowsOf,
  exitCodeOf,
  pinBaseline,
  requireBaseline,
  startEvalRun,
  VERDICT_EXIT,
  waitForEvalRun,
} from "./run.js";

export const EVAL_USAGE = `Usage: ratel eval run [--version <id|latest>] [--eval <name>]... [--trials <n>] [-f ratel.yaml]
                      [--param name=value]... [--label <label>] [--name <name>] [--timeout <seconds>]
                      [--json] [--no-wait] [--compare-baseline]
       ratel eval baseline pin <experiment-id> [--json]

Runs the suite (each eval of the project) on an image version, or only each --eval. It prints the
counts of each case and the score of each eval. With --compare-baseline, prints the verdict against
the pinned baseline, and it gives the exit code. --trials is the trials of each case. Default: the
trials of each eval. --param takes a parameter that ratel.yaml declares. The defaults of ratel.yaml
apply. --timeout is the longest wait for the end of the run. Default: trials (3 when not given)
times run.timeoutSeconds of ratel.yaml (300 when not given), plus 600. At the deadline it exits
with 4.

  RATEL_API_KEY   a project key with the permissions evals:read and evals:write
  RATEL_BASE_URL  the API root of Ratel, with /api/v1 (default https://app.ratel.sh/api/v1)
`;

const MAX_TRIALS = 20;
/** The most --eval names of one run, and the longest name (protocol/evals/v1 start-experiment). */
const MAX_EVALS = 100;
const MAX_EVAL_NAME = 200;
/**
 * The trials for the default --timeout when --trials is not given. Then each eval has its own
 * trials, which the CLI does not know before the run.
 */
const DEFAULT_TRIALS = 3;
/** The run.timeoutSeconds of ratel.yaml when it is not given (protocol/sandbox/v1). */
const DEFAULT_RUN_TIMEOUT_SECONDS = 300;
/** The time on top of the trials: the queue and the scorers. */
const WAIT_MARGIN_SECONDS = 600;

/** What `ratel eval` reads and writes. */
export interface EvalIo {
  env: Record<string, string | undefined>;
  stdout: (text: string) => void;
  stderr: (text: string) => void;
}

/** `ratel eval ...`. Returns the exit code. */
export async function evalCommand(argv: string[], io: EvalIo): Promise<number> {
  const json = argv.some((arg) => arg === "--json" || arg.startsWith("--json="));
  const print = (value: unknown) => io.stdout(`${JSON.stringify(value)}\n`);
  const usage = (problem: string) => {
    if (json) print({ error: "usage", message: problem });
    io.stderr(`ratel: ${problem}\n\n${EVAL_USAGE}`);
    return EVAL_EXIT.refused;
  };
  const options = await read(argv);
  if (typeof options === "string") return usage(options);
  if (options.help) {
    // With --json the standard output has one document or nothing.
    (json ? io.stderr : io.stdout)(EVAL_USAGE);
    return EVAL_EXIT.pass;
  }
  const { apiKey, baseUrl: url } = resolvePlatformConfig({}, io.env);
  if (!apiKey) return usage("RATEL_API_KEY is not set.");
  if (!isAddress(url)) return usage("RATEL_BASE_URL is not an http or https address.");
  if (!/^[\x21-\x7e]+$/.test(apiKey)) return usage("RATEL_API_KEY is not a key.");

  let run: { experimentId: string; url: string } | null = null;
  const withParams = <T extends object>(value: T) =>
    options.params ? { ...value, params: options.params } : value;
  const document = <T extends Experiment>(experiment: T) => {
    const rows = evalRowsOf(experiment);
    const { evals: _, ...rest } = experiment;
    return withParams(rows ? { ...rest, evals: rows.map(summary) } : rest);
  };
  try {
    if (options.pin) {
      const pinned = await pinBaseline(options.pin, { url, apiKey });
      if (json) print(pinned);
      else io.stdout(`${oneLine(pinned.baseline.experimentId)}\n`);
      return EVAL_EXIT.pass;
    }
    if (options.compare) await requireBaseline({ url, apiKey });
    const started = await startEvalRun({ ...options, url, apiKey });
    if (options.compare) run = { experimentId: started.experimentId, url: started.url };
    if (!options.wait) {
      if (json) print(withParams(started));
      else io.stdout(`${oneLine(started.experimentId)}\n`);
      return EVAL_EXIT.pass;
    }
    io.stderr(`ratel: experiment ${oneLine(started.experimentId)} started\n`);
    const experiment = await waitForEvalRun(
      started.experimentId,
      { url, apiKey },
      { timeoutMs: options.timeoutMs },
    );
    const exitCode = exitCodeOf(experiment);
    if (options.compare) {
      const comparison = await compareEvalRun(started.experimentId, { url, apiKey });
      if (json) print(document({ ...experiment, comparison }));
      else io.stdout(report(experiment) + compared(comparison));
      return VERDICT_EXIT[comparison.verdict];
    }
    if (json) print(document(experiment));
    else io.stdout(report(experiment));
    return exitCode;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const body = error instanceof EvalCallError ? error.body : {};
    if (json) print({ error: "eval_run_failed", ...body, message, ...run });
    io.stderr(`ratel: ${oneLine(message)}\n`);
    return error instanceof EvalCallError ? error.exitCode : EVAL_EXIT.unavailable;
  }
}

type Options = {
  sandboxVersion: string;
  trials?: number;
  params?: Record<string, ParamValue>;
  label?: string;
  name?: string;
  /** The names of the evals to run. Absent: the suite. */
  evals?: string[];
  wait: boolean;
  compare: boolean;
  /** The longest wait for the end of the run. */
  timeoutMs: number;
  /** The experiment that `baseline pin` pins. */
  pin?: string;
  help: boolean;
};

/** The options, or what is wrong with them. */
async function read(argv: string[]): Promise<Options | string> {
  let parsed: ReturnType<typeof parse>;
  try {
    parsed = parse(argv);
  } catch (error) {
    return (error as Error).message;
  }
  const { values, positionals } = parsed;
  const base = {
    sandboxVersion: values.version ?? "latest",
    timeoutMs: 0,
    wait: !values["no-wait"],
    compare: values["compare-baseline"] ?? false,
    help: values.help ?? false,
  };
  if (base.help) return base;
  if (positionals[0] === "baseline") {
    const [, command, pin, ...rest] = positionals;
    if (command !== "pin" || !pin || rest.length > 0) return "Unknown command.";
    const lists = ["json", "param", "eval"];
    const other = Object.keys(values).find((name) => !lists.includes(name));
    if (other) return `--${other} is not an option of baseline pin.`;
    if (values.param.length > 0) return "--param is not an option of baseline pin.";
    if (values.eval.length > 0) return "--eval is not an option of baseline pin.";
    return { ...base, pin };
  }
  if (positionals.length !== 1 || positionals[0] !== "run") return "Unknown command.";
  if (base.compare && !base.wait) return "--compare-baseline needs the end of the run.";
  const trials = values.trials === undefined ? undefined : Number(values.trials);
  if (trials !== undefined && !(Number.isInteger(trials) && trials >= 1 && trials <= MAX_TRIALS)) {
    return `--trials is a whole number from 1 to ${MAX_TRIALS}.`;
  }
  const timeout = values.timeout === undefined ? undefined : Number(values.timeout);
  if (timeout !== undefined && !(Number.isInteger(timeout) && timeout >= 1)) {
    return "--timeout is a whole number of seconds from 1.";
  }
  for (const flag of ["label", "name"] as const) {
    if (values[flag] !== undefined && values[flag].trim() === "") return `--${flag} is empty.`;
  }
  if (values.eval.some((name) => name.trim() === "")) return "--eval is empty.";
  if (values.eval.some((name) => name.length > MAX_EVAL_NAME)) {
    return `--eval is a name of at most ${MAX_EVAL_NAME} characters.`;
  }
  if (values.eval.length > MAX_EVALS) return `--eval is given at most ${MAX_EVALS} times.`;
  let config: Awaited<ReturnType<typeof readConfig>>;
  try {
    config = await readConfig(values.file, values.param);
  } catch (error) {
    if (error instanceof CliError) return error.message;
    throw error;
  }
  const { params, runTimeoutSeconds } = config;
  const waitSeconds =
    timeout ?? (trials ?? DEFAULT_TRIALS) * runTimeoutSeconds + WAIT_MARGIN_SECONDS;
  return {
    ...base,
    timeoutMs: waitSeconds * 1000,
    ...(trials ? { trials } : {}),
    ...(params ? { params } : {}),
    ...(values.label ? { label: values.label } : {}),
    ...(values.name ? { name: values.name } : {}),
    ...(values.eval.length > 0 ? { evals: values.eval } : {}),
  };
}

/**
 * The parameter values of the run: the defaults of `ratel.yaml` with each `--param` applied. With
 * no `ratel.yaml` and no `--param`, none: the version keeps its own defaults. Also the
 * `run.timeoutSeconds` of `ratel.yaml`, for the default of `--timeout`.
 */
async function readConfig(
  file: string | undefined,
  flags: string[],
): Promise<{ params?: Record<string, ParamValue>; runTimeoutSeconds: number }> {
  const path = file ?? "ratel.yaml";
  if (file === undefined && flags.length === 0 && !existsSync(path)) {
    return { runTimeoutSeconds: DEFAULT_RUN_TIMEOUT_SECONDS };
  }
  if (file === undefined && !existsSync(path)) {
    throw new CliError(
      "--param needs the ratel.yaml that declares it: give it with -f.",
      3,
      "usage",
    );
  }
  const { config } = await loadConfig(path);
  return {
    params: parseParams(config, flags),
    runTimeoutSeconds: config.run?.timeoutSeconds ?? DEFAULT_RUN_TIMEOUT_SECONDS,
  };
}

function parse(argv: string[]) {
  return parseArgs({
    args: argv,
    allowPositionals: true,
    options: {
      version: { type: "string" },
      trials: { type: "string" },
      file: { type: "string", short: "f" },
      param: { type: "string", multiple: true, default: [] as string[] },
      eval: { type: "string", multiple: true, default: [] as string[] },
      label: { type: "string" },
      name: { type: "string" },
      timeout: { type: "string" },
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

function report(experiment: Experiment): string {
  const { cases, url } = experiment;
  const lines = cases.map(
    ({ name, number, counts }) =>
      `${oneLine(name)} (v${number}): ${counts.pass} pass, ${counts.fail} fail, ${counts.invalid} invalid`,
  );
  const evals = (evalRowsOf(experiment) ?? []).map(evalLine);
  return `${[...lines, ...evals, oneLine(url)].join("\n")}\n`;
}

/** `eval <name> (v<n>): <score>% score, <pass>/<total> pass, <delta> points`, or why no delta. */
function evalLine({ name, version, score, counts, delta, deltaRefusal }: EvalRow): string {
  const parts = [score === null ? "no score" : `${Math.round(score * 100)}% score`];
  if (counts) parts.push(`${counts.pass}/${counts.pass + counts.fail + counts.invalid} pass`);
  if (delta && delta.score !== null) parts.push(`${points(delta.score)} points`);
  else if (deltaRefusal) parts.push(`no delta: ${oneLine(deltaRefusal)}`);
  return `eval ${oneLine(name)}${version === null ? "" : ` (v${version})`}: ${parts.join(", ")}`;
}

/** A difference of two scores (0 to 1) in percentage points, with its sign and one decimal. */
function points(difference: number): string {
  const value = Math.round(difference * 1000) / 10;
  return `${value > 0 ? "+" : ""}${value}`;
}

/** The eval as `--json` prints it. */
function summary({ name, score, counts, scorers, delta }: EvalRow) {
  return { name, score, counts, scorers, delta };
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
