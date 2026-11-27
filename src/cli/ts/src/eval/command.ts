import { existsSync } from "node:fs";
import { parseArgs } from "node:util";
import { resolvePlatformConfig } from "@ratel-ai/sdk/platform";
import { type ParamValue, parseParamFlags } from "../args.js";
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
  isRefused,
  pinBaseline,
  type RefusedRun,
  type RunPart,
  type RunSummary,
  requireBaselines,
  runSummaryOf,
  type StartedRun,
  startEvalRun,
  startEvalRuns,
  type Variant,
  VERDICT_EXIT,
  waitForEvalRun,
} from "./run.js";

/**
 * The most trials of a case. The default --timeout uses it when --trials is not given: then each
 * eval has its own trials, which the CLI does not know before the run.
 */
const MAX_TRIALS = 20;
/** The most --eval names of one run, and the longest name (protocol/evals/v1 start-runs). */
const MAX_EVALS = 100;
const MAX_EVAL_NAME = 200;
/** The most combinations of the values of the --param lists: the variants of one run. */
const MAX_COMBINATIONS = 12;
/** The run.timeoutSeconds of ratel.yaml when it is not given (protocol/sandbox/v1). */
const DEFAULT_RUN_TIMEOUT_SECONDS = 300;
/** The time on top of the trials: the queue and the scorers. */
const WAIT_MARGIN_SECONDS = 600;

export const EVAL_USAGE = `Usage: ratel eval run [--eval <name>]... [--version <id|latest>] [--dataset <name|id>] [--trials <n>]
                      [-f ratel.yaml] [--param name=value[,value]...]... [--label <label>] [--name <name>]
                      [--timeout <seconds>] [--json] [--no-wait] [--compare-baseline]
       ratel eval baseline pin <experiment-id> [--json]

Starts one run for each eval, or only for each --eval (a name or an id), and waits for the end of
each. The default version is the newest ready version of the sandbox of ratel.yaml (its name). A
version of another sandbox does not apply to an eval: the eval runs on its own sandbox, and the
command prints the note of Ratel. It prints the counts of each case and the score of each run. The
exit code is the highest of the runs. With --compare-baseline, each run is compared with the baseline
of its eval, and the verdict gives the exit code. --dataset runs the cases of a dataset in place of
the cases of each eval, for this run only. --trials is the trials of each case. Default: the trials
of each eval. --param takes a parameter that ratel.yaml declares. The defaults of ratel.yaml apply.
A value with commas is a list: each combination of the lists is a variant of the run (at most
${MAX_COMBINATIONS}). --timeout is the longest wait for the end of the runs. Default: trials (20, the
most, when not given) times run.timeoutSeconds of ratel.yaml (300 when not given), plus 600. At the
deadline it exits with 4.

  RATEL_API_KEY   a project key with the permissions evals:read and evals:write
  RATEL_BASE_URL  the API root of Ratel, with /api/v1 (default https://app.ratel.sh/api/v1)
`;

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

  const address = { url, apiKey };
  let run: { experimentId: string; url: string } | null = null;
  const withParams = <T extends object>(value: T) => ({
    ...value,
    ...(options.params ? { params: options.params } : {}),
    ...(options.matrix ? { matrix: options.matrix } : {}),
  });
  try {
    if (options.pin) {
      const pinned = await pinBaseline(options.pin, address);
      if (json) print(pinned);
      else io.stdout(`${oneLine(pinned.baseline.experimentId)}\n`);
      return EVAL_EXIT.pass;
    }
    if (options.compare) await requireBaselines(options.evals, address);
    const [only, ...more] = options.evals ?? [];
    const parts: RunPart[] =
      only !== undefined && more.length === 0
        ? [await startEvalRun(only, { ...options, ...address })]
        : await startEvalRuns({ ...options, ...address });
    for (const part of parts) {
      if (!isRefused(part) && part.note) {
        io.stderr(`ratel: ${oneLine(part.eval)}: ${oneLine(part.note)}\n`);
      }
    }
    const [part, ...others] = parts;
    if (part === undefined) {
      throw new EvalCallError("Ratel started no run: the project has no eval.", EVAL_EXIT.refused, {
        error: "no_evals",
      });
    }
    if (others.length > 0)
      return await several(parts, options, { address, io, json, print, withParams });
    // One run: the document and the exit code of one run, as before the runs of each eval.
    if (isRefused(part)) throw new EvalCallError(part.message, EVAL_EXIT.refused, part);
    if (options.compare) run = { experimentId: part.experimentId, url: part.url };
    if (!options.wait) {
      if (json) print(withParams(part));
      else io.stdout(`${oneLine(part.experimentId)}\n`);
      return EVAL_EXIT.pass;
    }
    io.stderr(`ratel: experiment ${oneLine(part.experimentId)} started\n`);
    const experiment = await waitForEvalRun(part.experimentId, address, {
      timeoutMs: options.timeoutMs,
    });
    const exitCode = exitCodeOf(experiment);
    const document = (value: Experiment) =>
      withParams({ ...single(value), ...(part.note ? { note: part.note } : {}) });
    if (options.compare) {
      const comparison = await compareEvalRun(part.experimentId, address);
      if (json) print(document({ ...experiment, comparison }));
      else io.stdout(report(experiment, part) + compared(comparison));
      return VERDICT_EXIT[comparison.verdict];
    }
    if (json) print(document(experiment));
    else io.stdout(report(experiment, part));
    return exitCode;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const body = error instanceof EvalCallError ? error.body : {};
    if (json) print({ error: "eval_run_failed", ...body, message, ...run });
    io.stderr(`ratel: ${oneLine(message)}\n`);
    return error instanceof EvalCallError ? error.exitCode : EVAL_EXIT.unavailable;
  }
}

type Context = {
  address: { url: string; apiKey: string };
  io: EvalIo;
  json: boolean;
  print: (value: unknown) => void;
  withParams: <T extends object>(value: T) => T;
};

/** The result of the run of one eval, as `--json` prints it in `runs`. */
type RunResult = Record<string, unknown> & { eval: string; exitCode: number };

/**
 * The runs of more than one eval. They run at the same time on Ratel: the command reads each in
 * turn, up to one deadline. The exit code is the highest of the runs; a refused eval is 3.
 */
async function several(
  parts: RunPart[],
  options: Options,
  { address, io, json, print, withParams }: Context,
): Promise<number> {
  const deadline = Date.now() + options.timeoutMs;
  const results: Array<{ result: RunResult; text: string }> = [];
  if (options.wait) {
    for (const part of parts) {
      if (!isRefused(part)) {
        io.stderr(
          `ratel: experiment ${oneLine(part.experimentId)} of ${oneLine(part.eval)} started\n`,
        );
      }
    }
  }
  for (const part of parts) {
    if (isRefused(part)) {
      results.push({ result: { ...part, exitCode: EVAL_EXIT.refused }, text: refusal(part) });
    } else if (!options.wait) {
      results.push({ result: { ...part, exitCode: EVAL_EXIT.pass }, text: "" });
    } else {
      results.push(await settle(part, deadline, options.compare, address));
    }
  }
  const exitCode = Math.max(...results.map(({ result }) => result.exitCode));
  if (json) {
    print(withParams({ v: 1, runs: results.map(({ result }) => result), exitCode }));
  } else if (!options.wait) {
    for (const { result } of results) {
      if (typeof result.experimentId === "string") io.stdout(`${oneLine(result.experimentId)}\n`);
      else io.stderr(`ratel: ${oneLine(result.eval)}: ${oneLine(String(result.message))}\n`);
    }
  } else {
    io.stdout(results.map(({ result, text }) => `== ${oneLine(result.eval)}\n${text}`).join(""));
  }
  return exitCode;
}

/** Waits for the end of one run and reads its comparison. A failure is the result of that run. */
async function settle(
  part: StartedRun,
  deadline: number,
  compare: boolean,
  address: Context["address"],
): Promise<{ result: RunResult; text: string }> {
  const { eval: name, evalId, experimentId, url, note } = part;
  const head = { eval: name, evalId, experimentId, url, ...(note ? { note } : {}) };
  try {
    const timeoutMs = Math.max(deadline - Date.now(), 0);
    const experiment = await waitForEvalRun(experimentId, address, { timeoutMs });
    let exitCode: number = exitCodeOf(experiment);
    const { score, counts, delta, variants } = runSummaryOf(experiment, evalId);
    const result: RunResult = { ...head, exitCode, score, counts, delta, variants };
    let text = report(experiment, part);
    if (compare) {
      const comparison = await compareEvalRun(experimentId, address);
      exitCode = VERDICT_EXIT[comparison.verdict];
      Object.assign(result, { exitCode, comparison });
      text += compared(comparison);
    }
    return { result, text };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    const body = error instanceof EvalCallError ? error.body : {};
    const exitCode = error instanceof EvalCallError ? error.exitCode : EVAL_EXIT.unavailable;
    const result = { ...head, exitCode, error: "eval_run_failed", ...body, message };
    const word = exitCode === EVAL_EXIT.refused ? "refused" : "failed";
    return { result, text: `${word}: ${oneLine(message)}\n` };
  }
}

function refusal({ message }: RefusedRun): string {
  return `refused: ${oneLine(message)}\n`;
}

/** The document of one run: the experiment, with each eval of `evals` as `summary` prints it. */
function single<T extends Experiment>(experiment: T) {
  const rows = evalRowsOf(experiment);
  const { evals: _, ...rest } = experiment;
  return rows ? { ...rest, evals: rows.map(summary) } : rest;
}

type Options = {
  sandboxVersion: string;
  /** The sandbox of ratel.yaml, sent with `latest`: a project can have more than one sandbox. */
  sandbox?: string;
  /** The id or the name of the dataset of the run. */
  dataset?: string;
  trials?: number;
  params?: Record<string, ParamValue>;
  /** The parameters with a list of values. */
  matrix?: Record<string, ParamValue[]>;
  label?: string;
  name?: string;
  /** The names or the ids of the evals to run. Absent: each eval. */
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
  for (const flag of ["label", "name", "dataset"] as const) {
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
  const { params, matrix, runTimeoutSeconds, sandbox } = config;
  const combinations = Object.values(matrix ?? {}).reduce((n, values) => n * values.length, 1);
  if (combinations > MAX_COMBINATIONS) {
    return `The --param lists make ${combinations} combinations of values. The most is ${MAX_COMBINATIONS}.`;
  }
  const waitSeconds =
    // With no --trials each eval uses its own trials, up to MAX_TRIALS: the wait covers the most.
    timeout ?? (trials ?? MAX_TRIALS) * runTimeoutSeconds + WAIT_MARGIN_SECONDS;
  return {
    ...base,
    timeoutMs: waitSeconds * 1000,
    ...(trials ? { trials } : {}),
    ...(params ? { params } : {}),
    ...(matrix && Object.keys(matrix).length > 0 ? { matrix } : {}),
    ...(sandbox && base.sandboxVersion === "latest" ? { sandbox } : {}),
    ...(values.dataset ? { dataset: values.dataset } : {}),
    ...(values.label ? { label: values.label } : {}),
    ...(values.name ? { name: values.name } : {}),
    ...(values.eval.length > 0 ? { evals: values.eval } : {}),
  };
}

/**
 * The parameter values of the run: the defaults of `ratel.yaml` with each `--param` applied. With
 * no `ratel.yaml` and no `--param`, none: the version keeps its own defaults. Also the
 * `run.timeoutSeconds` of `ratel.yaml`, for the default of `--timeout`, and its `name`: the sandbox.
 */
async function readConfig(
  file: string | undefined,
  flags: string[],
): Promise<{
  params?: Record<string, ParamValue>;
  matrix?: Record<string, ParamValue[]>;
  runTimeoutSeconds: number;
  sandbox?: string;
}> {
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
    ...parseParamFlags(config, flags),
    runTimeoutSeconds: config.run?.timeoutSeconds ?? DEFAULT_RUN_TIMEOUT_SECONDS,
    sandbox: config.name,
  };
}

function parse(argv: string[]) {
  return parseArgs({
    args: argv,
    allowPositionals: true,
    options: {
      version: { type: "string" },
      dataset: { type: "string" },
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

/**
 * The counts of each case, the score of the run (or of each eval of an older Ratel) and the
 * address. `part` names the eval of the run.
 */
function report(experiment: Experiment, part: { eval?: string; evalId?: string }): string {
  const { cases, url } = experiment;
  const lines = cases.map(
    ({ name, number, counts }) =>
      `${oneLine(name)} (v${number}): ${counts.pass} pass, ${counts.fail} fail, ${counts.invalid} invalid`,
  );
  const scores =
    experiment.run && typeof experiment.run === "object" && part.eval !== undefined
      ? runLines(part.eval, runSummaryOf(experiment, part.evalId))
      : (evalRowsOf(experiment) ?? []).map(evalLine);
  return `${[...lines, ...scores, oneLine(url)].join("\n")}\n`;
}

/** The line of the run of one eval, then one line for each variant. */
function runLines(name: string, summary: RunSummary): string[] {
  const { score, counts, delta, deltaRefusal, version, variants } = summary;
  const line = evalLine({
    name,
    version,
    score,
    counts,
    delta,
    deltaRefusal,
    scorers: [],
    evalId: null,
  });
  // A run with no matrix has one variant with no index: the eval line says it all.
  const shown = variants.filter((variant) => variant.index !== null);
  return [line, ...shown.map(variantLine)];
}

/** `  variant <index> (<name>=<value>, ...): <score>% score, <pass>/<total> pass`. */
function variantLine({ index, params, score, counts }: Variant): string {
  const values = Object.entries(params).map(([name, value]) => `${name}=${String(value)}`);
  const parts = [score === null ? "no score" : `${percent(score)}% score`];
  if (counts) parts.push(`${counts.pass}/${counts.pass + counts.fail + counts.invalid} pass`);
  const label = `variant${index === null ? "" : ` ${index}`} (${values.join(", ")})`;
  return `  ${oneLine(label)}: ${parts.join(", ")}`;
}

/** `eval <name> (v<n>): <score>% score, <pass>/<total> pass, <delta> points`, or why no delta. */
function evalLine({ name, version, score, counts, delta, deltaRefusal }: EvalRow): string {
  const parts = [score === null ? "no score" : `${percent(score)}% score`];
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

/** A rate as a percent with one decimal. Only a rate of 1 shows 100: 0.9996 is "99.9". */
function percent(rate: number): string {
  const rounded = Math.round(rate * 1000) / 10;
  return String(rate < 1 ? Math.min(rounded, 99.9) : rounded);
}
