/** Starts an eval run over the API of `protocol/evals/v1` and waits for its end. */
export type EvalRunOptions = {
  url: string;
  apiKey: string;
  /** A version id, or `latest`. */
  sandboxVersion: string;
  trials?: number;
  params?: Record<string, unknown>;
};

export type Counts = { pass: number; fail: number; invalid: number; open: number };

export type StartedExperiment = { experimentId: string; state: string; url: string };

export type Experiment = {
  experimentId: string;
  state: "running" | "completed";
  url: string;
  counts: Counts;
  cases: Array<{ caseId: string; name: string; number: number; counts: Counts }>;
  [field: string]: unknown;
};

export type Baseline = { experimentId: string; url: string; pinnedBy: string; pinnedAt: string };

export type CaseSide = { number: number; status: string; counts: Counts };
export type Comparison = {
  verdict: keyof typeof VERDICT_EXIT;
  rule: string;
  url: string;
  baseline: Baseline;
  changedCases: Array<{
    caseId: string;
    name: string;
    change: string;
    baseline: CaseSide | null;
    candidate: CaseSide | null;
  }>;
  [field: string]: unknown;
};

type Address = Pick<EvalRunOptions, "url" | "apiKey">;
type Sleep = (ms: number) => Promise<void>;

/** The exit codes of `ratel eval run`. They are a contract: see the README. */
export const EVAL_EXIT = { pass: 0, fail: 1, invalid: 2, refused: 3, unavailable: 4 } as const;

/** With `--compare-baseline` the verdict gives the exit code. */
export const VERDICT_EXIT = {
  improved: 0,
  unchanged: 0,
  regressed: 1,
  inconclusive: 2,
} as const;

/** Ratel refused the call (3), or did not answer it (4). */
export class EvalCallError extends Error {
  constructor(
    message: string,
    readonly exitCode: typeof EVAL_EXIT.refused | typeof EVAL_EXIT.unavailable,
    readonly body: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

const CALL_TIMEOUT_MS = 30_000;
const POLL_MS = 2_000;
/** Reads that fail one after the other before the wait ends. */
const POLL_FAILURES = 5;

export async function startEvalRun({
  url,
  apiKey,
  sandboxVersion,
  trials,
  params,
}: EvalRunOptions): Promise<StartedExperiment> {
  const body = { sandboxVersion, ...(trials ? { trials } : {}), ...(params ? { params } : {}) };
  const answer = await call("POST", `${origin(url)}/api/v1/evals/experiments`, apiKey, body);
  if (typeof answer.experimentId !== "string") throw unreadable();
  return answer as StartedExperiment;
}

/** Reads the experiment until it is completed. */
export async function waitForEvalRun(
  experimentId: string,
  { url, apiKey }: Address,
  sleep: Sleep = wait,
): Promise<Experiment> {
  const address = `${origin(url)}/api/v1/evals/experiments/${experimentId}`;
  for (let failures = 0; ; await sleep(POLL_MS)) {
    try {
      const answer = await call("GET", address, apiKey);
      if (!Array.isArray(answer.cases) || !answer.counts) throw unreadable();
      failures = 0;
      if (answer.state === "completed") return answer as Experiment;
    } catch (error) {
      failures += 1;
      if (!mayTryAgain(error) || failures >= POLL_FAILURES) throw error;
    }
  }
}

/** Throws when no baseline is pinned: a run costs money, and it would have nothing to compare. */
export async function requireBaseline({ url, apiKey }: Address): Promise<void> {
  const answer = await call("GET", `${origin(url)}/api/v1/evals/baseline`, apiKey);
  if (answer.baseline === null) {
    const message = "The project has no pinned baseline. Pin an experiment first.";
    throw new EvalCallError(message, EVAL_EXIT.refused, { error: "no_baseline" });
  }
  if (!isBaseline(answer.baseline)) throw unreadable();
}

/** The experiment against the pinned baseline. The run is paid, so a failed read is tried again. */
export async function compareEvalRun(
  experimentId: string,
  { url, apiKey }: Address,
  sleep: Sleep = wait,
): Promise<Comparison> {
  const address = `${origin(url)}/api/v1/evals/experiments/${experimentId}/comparison`;
  for (let failures = 1; ; failures += 1) {
    try {
      const answer = await call("GET", address, apiKey);
      if (!isComparison(answer, experimentId)) throw unreadable();
      return answer;
    } catch (error) {
      if (!mayTryAgain(error) || failures >= POLL_FAILURES) throw error;
    }
    await sleep(POLL_MS);
  }
}

export async function pinBaseline(
  experimentId: string,
  { url, apiKey }: Address,
): Promise<{ baseline: Baseline }> {
  const body = { experimentId };
  const answer = await call("PUT", `${origin(url)}/api/v1/evals/baseline`, apiKey, body);
  if (!isBaseline(answer.baseline)) throw unreadable();
  return { ...answer, baseline: answer.baseline };
}

/** A case with a failed trial is a fail. Then a case with an invalid trial is invalid. */
export function exitCodeOf({ cases }: Pick<Experiment, "cases">): number {
  const complete = cases.every(
    ({ counts }) =>
      [counts?.pass, counts?.fail, counts?.invalid].every(Number.isInteger) && counts.open === 0,
  );
  if (cases.length === 0 || !complete) throw unreadable();
  if (cases.some(({ counts }) => counts.fail > 0)) return EVAL_EXIT.fail;
  if (cases.some(({ counts }) => counts.invalid > 0)) return EVAL_EXIT.invalid;
  return EVAL_EXIT.pass;
}

/** The gate must not read a verdict of another run, or one that the contract does not have. */
function isComparison(answer: Record<string, unknown>, experimentId: string): answer is Comparison {
  const { verdict, rule, url, baseline, candidate, changedCases, otherCases } = answer;
  return (
    typeof verdict === "string" &&
    Object.hasOwn(VERDICT_EXIT, verdict) &&
    typeof rule === "string" &&
    typeof url === "string" &&
    isBaseline(baseline) &&
    isObject(candidate) &&
    candidate.experimentId === experimentId &&
    [changedCases, otherCases].every((rows) => Array.isArray(rows) && rows.every(isComparedCase))
  );
}

function isBaseline(value: unknown): value is Baseline {
  return isObject(value) && typeof value.experimentId === "string";
}

function isComparedCase(row: unknown): boolean {
  if (!isObject(row) || typeof row.name !== "string" || typeof row.change !== "string")
    return false;
  const sides = [row.baseline, row.candidate];
  return (
    sides.some((side) => side !== null) && sides.every((side) => side === null || isSide(side))
  );
}

function isSide(value: unknown): boolean {
  return (
    isObject(value) &&
    Number.isInteger(value.number) &&
    isObject(value.counts) &&
    [value.counts.pass, value.counts.fail, value.counts.invalid].every(Number.isInteger)
  );
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function mayTryAgain(error: unknown): boolean {
  return error instanceof EvalCallError && error.exitCode === EVAL_EXIT.unavailable;
}

function wait(ms: number): Promise<void> {
  return new Promise((done) => setTimeout(done, ms));
}

async function call(
  method: string,
  url: string,
  apiKey: string,
  body?: unknown,
): Promise<Record<string, unknown>> {
  let response: Response;
  let text: string;
  try {
    response = await fetch(url, {
      method,
      headers: {
        authorization: `Bearer ${apiKey}`,
        ...(body === undefined ? {} : { "content-type": "application/json" }),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      redirect: "error",
      signal: AbortSignal.timeout(CALL_TIMEOUT_MS),
    });
    text = await response.text();
  } catch {
    const message = `${method} ${new URL(url).pathname} got no answer from ${new URL(url).host}.`;
    throw new EvalCallError(message, EVAL_EXIT.unavailable);
  }
  const answer = parse(text);
  if (response.ok) {
    if (!answer) throw unreadable();
    return answer;
  }
  const message =
    answer?.message ??
    answer?.error ??
    `${method} ${new URL(url).pathname} answered ${response.status}`;
  const refused = response.status >= 400 && response.status < 500 && response.status !== 429;
  throw new EvalCallError(
    String(message),
    refused ? EVAL_EXIT.refused : EVAL_EXIT.unavailable,
    answer ?? {},
  );
}

function unreadable(): EvalCallError {
  return new EvalCallError("The answer of Ratel is not readable.", EVAL_EXIT.unavailable);
}

function origin(url: string): string {
  return url.replace(/\/+$/, "");
}

function parse(text: string): Record<string, unknown> | null {
  try {
    const value: unknown = JSON.parse(text);
    return value !== null && typeof value === "object" ? (value as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}
