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

/** The exit codes of `ratel eval run`. They are a contract: see the README. */
export const EVAL_EXIT = { pass: 0, fail: 1, invalid: 2, refused: 3, unavailable: 4 } as const;

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
  { url, apiKey }: Pick<EvalRunOptions, "url" | "apiKey">,
  sleep: (ms: number) => Promise<void> = (ms) => new Promise((done) => setTimeout(done, ms)),
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
      const again = error instanceof EvalCallError && error.exitCode === EVAL_EXIT.unavailable;
      if (!again || failures >= POLL_FAILURES) throw error;
    }
  }
}

/** A case with a failed trial is a fail. Then a case with an invalid trial is invalid. */
export function exitCodeOf({ cases }: Pick<Experiment, "cases">): number {
  if (cases.some(({ counts }) => counts.fail > 0)) return EVAL_EXIT.fail;
  if (cases.some(({ counts }) => counts.invalid > 0)) return EVAL_EXIT.invalid;
  return EVAL_EXIT.pass;
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
