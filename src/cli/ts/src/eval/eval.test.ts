import { spawn, spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { createServer, type Server } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

const CLI = fileURLToPath(new URL("../../dist/cli.js", import.meta.url));
const VECTORS = new URL(
  "../../../../../protocol/evals/v1/conformance/vectors.json",
  import.meta.url,
);
const { vectors } = JSON.parse(readFileSync(VECTORS, "utf8")) as {
  vectors: { name: string; document: unknown }[];
};
// biome-ignore lint/suspicious/noExplicitAny: the vectors are JSON
const vector = (name: string): any =>
  structuredClone(vectors.find((v) => v.name === name)?.document);
const experiment = vector("experiment.completed");
const ID = experiment.experimentId;
const comparison = vector("comparison.regressed");
comparison.candidate.experimentId = ID;
const pinned = vector("baseline.pinned");
const differ = vector("error.scorers-differ");
/** A folder with no ratel.yaml: the run sends no params. */
const EMPTY = mkdtempSync(join(tmpdir(), "ratel-eval-"));
/** A ratel.yaml that declares the parameters model and tone. */
const YAML = join(mkdtempSync(join(tmpdir(), "ratel-eval-")), "ratel.yaml");
writeFileSync(
  YAML,
  "name: support-demo\ninput:\n  example: hi\nparams:\n  model: { type: enum, options: [x, y], default: y }\n  tone: { type: string, default: friendly }\n",
);

type Call = { method: string; path: string; auth: string | undefined; body: string };
type Answer = { status: number; body: unknown };
let server: Server;
let url: string;
let calls: Call[];
/** The answers of the reads, in order. The last one repeats. */
let reads: Answer[];
let start: Answer;
/** The answers of `/baseline` and, in order, of `/comparison`. */
let baseline: Answer;
let comparisons: Answer[];

beforeEach(async () => {
  calls = [];
  start = { status: 201, body: { experimentId: ID, state: "running", url: experiment.url } };
  reads = [{ status: 200, body: experiment }];
  baseline = { status: 200, body: pinned };
  comparisons = [{ status: 200, body: comparison }];
  server = createServer((req, res) => {
    let body = "";
    req.on("data", (chunk) => {
      body += chunk;
    });
    req.on("end", () => {
      const { method = "", url: path = "", headers } = req;
      calls.push({ method, path, auth: headers.authorization, body });
      const next = (answers: Answer[]) =>
        (answers.length > 1 ? answers.shift() : answers[0]) as Answer;
      const answer = path.endsWith("/baseline")
        ? baseline
        : path.endsWith("/comparison")
          ? next(comparisons)
          : method === "POST"
            ? start
            : next(reads);
      res.statusCode = answer.status;
      res.end(typeof answer.body === "string" ? answer.body : JSON.stringify(answer.body));
    });
  });
  await new Promise<void>((done) => server.listen(0, "127.0.0.1", done));
  url = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
});
afterEach(() => new Promise((done) => server.close(done)));

function ratel(args: string[], env: Record<string, string | undefined> = {}) {
  return new Promise<{ code: number | null; out: string; err: string }>((done) => {
    const child = spawn(process.execPath, [CLI, "eval", ...args], {
      cwd: EMPTY,
      env: {
        PATH: process.env.PATH,
        RATEL_BASE_URL: `${url}/api/v1`,
        RATEL_API_KEY: "rtl_key",
        ...env,
      },
    });
    let out = "";
    let err = "";
    child.stdout.on("data", (chunk) => {
      out += chunk;
    });
    child.stderr.on("data", (chunk) => {
      err += chunk;
    });
    child.on("close", (code) => done({ code, out, err }));
  });
}

/** The completed experiment with these counts for each case. */
const withCounts = (...counts: Array<[pass: number, fail: number, invalid: number]>) => ({
  ...experiment,
  cases: counts.map(([pass, fail, invalid], index) => ({
    ...experiment.cases[0],
    name: `case ${index}`,
    counts: { pass, fail, invalid, open: 0 },
  })),
});
const ended = (...counts: Parameters<typeof withCounts>) => {
  reads = [{ status: 200, body: withCounts(...counts) }];
};

describe("exit codes", () => {
  it("0: all cases pass", async () => {
    ended([3, 0, 0], [3, 0, 0]);
    expect((await ratel(["run"])).code).toBe(0);
  });

  it("1: a case fails, also with an invalid one", async () => {
    ended([2, 1, 0], [2, 0, 1]);
    expect((await ratel(["run"])).code).toBe(1);
  });

  it("2: a case is invalid and none fails", async () => {
    ended([3, 0, 0], [2, 0, 1]);
    expect((await ratel(["run"])).code).toBe(2);
  });

  it.each([
    [400, "invalid"],
    [401, "unauthorized"],
    [403, "forbidden"],
    [404, "not_found"],
    [409, "no_cases"],
  ])("3: refused with %s", async (status, error) => {
    start = { status, body: { error, message: `refused: ${error}` } };
    const { code, err } = await ratel(["run"]);
    expect(code).toBe(3);
    expect(err).toContain(`refused: ${error}`);
  });

  it.each([
    [["run", "--trials", "0"]],
    [["run", "--trials", "x"]],
    [["run", "--trials", "21"]],
    [["run", "--param", "model=x"]],
    [["run", "-f", YAML, "--param", "temperature=1"]],
    [["run", "-f", YAML, "--param", "model=z"]],
    [["run", "--label", ""]],
    [["run", "--nope"]],
    [["run", "more"]],
    [["start"]],
    [[]],
  ])("3: wrong usage %j", async (args) => {
    expect((await ratel(args)).code).toBe(3);
    expect(calls).toEqual([]);
  });

  it.each([
    { RATEL_API_KEY: undefined },
    { RATEL_BASE_URL: "not an address" },
    { RATEL_BASE_URL: "localhost:3000" },
    { RATEL_BASE_URL: "ftp://x" },
    { RATEL_BASE_URL: "http://user:pass@x" },
    { RATEL_API_KEY: "a\nb" },
  ])("3: a setting is wrong %j", async (env) => {
    expect((await ratel(["run"], env)).code).toBe(3);
  });

  it("4: no answer", async () => {
    expect((await ratel(["run"], { RATEL_BASE_URL: "http://127.0.0.1:9/api/v1" })).code).toBe(4);
  });

  it.each([500, 503, 429])("4: the server answers %s", async (status) => {
    start = { status, body: "oops" };
    expect((await ratel(["run"])).code).toBe(4);
  });

  it("4: the answer is not readable", async () => {
    start = { status: 200, body: "<html>" };
    expect((await ratel(["run"])).code).toBe(4);
  });

  it("4: a run that is still running at --timeout, with wait_timeout and the id of the run", async () => {
    reads = [{ status: 200, body: { ...experiment, state: "running" } }];
    const { code, out, err } = await ratel(["run", "--timeout", "1", "--json"]);
    expect(code).toBe(4);
    expect(JSON.parse(out)).toMatchObject({ error: "wait_timeout", experimentId: ID });
    expect(err).toMatch(/did not end in 1 s/);
  }, 15_000);

  it.each(["0", "-1", "x", "1.5"])("3: --timeout %s is not a number of seconds", async (value) => {
    expect((await ratel(["run", "--timeout", value])).code).toBe(3);
    expect(calls).toHaveLength(0);
  });
});

it("sends the options with the key", async () => {
  await ratel(["run", "--version", ID, "--trials", "5", "-f", YAML, "--param", "model=x"]);
  expect(calls.map(({ method, path, auth }) => [method, path, auth])).toEqual([
    ["POST", "/api/v1/evals/runs", "Bearer rtl_key"],
    ["GET", `/api/v1/evals/experiments/${ID}`, "Bearer rtl_key"],
  ]);
  expect(JSON.parse(calls[0]?.body ?? "")).toEqual({
    sandboxVersion: ID,
    trials: 5,
    params: { model: "x", tone: "friendly" },
  });
});

it("sends --label and --name, and the defaults of ./ratel.yaml", async () => {
  const folder = mkdtempSync(join(tmpdir(), "ratel-eval-"));
  writeFileSync(join(folder, "ratel.yaml"), readFileSync(YAML));
  await ratel([
    "run",
    "--label",
    "refunds",
    "--name",
    "tone dry",
    "-f",
    join(folder, "ratel.yaml"),
  ]);
  expect(JSON.parse(calls[0]?.body ?? "")).toEqual({
    sandbox: "support-demo",
    params: { model: "y", tone: "friendly" },
    label: "refunds",
    name: "tone dry",
  });
});

it("--json has the parameter values of the run", async () => {
  const { out } = await ratel(["run", "--json", "-f", YAML, "--param", "tone=dry"]);
  expect(JSON.parse(out).params).toEqual({ model: "y", tone: "dry" });
});

it("--json with --compare-baseline has the parameter values, the verdict and the changed cases", async () => {
  const { out } = await ratel(["run", "--json", "--compare-baseline", "-f", YAML]);
  const document = JSON.parse(out);
  expect(document.params).toEqual({ model: "y", tone: "friendly" });
  expect(document.comparison.verdict).toBe("regressed");
  expect(document.comparison.changedCases).toEqual(comparison.changedCases);
});

it("the sandbox of ratel.yaml chooses the evals, and each eval keeps its own runtime", async () => {
  // A project can have more than one sandbox: the run names the one of its ratel.yaml.
  for (const version of [[], ["--version", "latest"]]) {
    calls = [];
    await ratel(["run", ...version, "-f", YAML]);
    const body = JSON.parse(calls[0]?.body ?? "");
    expect(body).toMatchObject({ sandbox: "support-demo" });
    expect(body).not.toHaveProperty("sandboxVersion");
  }
  // A version id names its sandbox itself.
  calls = [];
  await ratel(["run", "--version", ID, "-f", YAML]);
  expect(JSON.parse(calls[0]?.body ?? "")).not.toHaveProperty("sandbox");
});

it("the default is the runtime and the trials of each eval", async () => {
  await ratel(["run"]);
  expect(JSON.parse(calls[0]?.body ?? "")).toEqual({});
});

it("waits for the end, also after a read that failed", async () => {
  reads = [
    { status: 503, body: "" },
    { status: 200, body: { ...experiment, state: "running" } },
    { status: 200, body: withCounts([3, 0, 0]) },
  ];
  expect((await ratel(["run"])).code).toBe(0);
  expect(calls).toHaveLength(4);
}, 15_000);

it("prints the counts of each case and the address of the page", async () => {
  ended([2, 1, 0], [2, 0, 1]);
  const { out } = await ratel(["run"]);
  expect(out).toBe(
    `case 0 (v1): 2 pass, 1 fail, 0 invalid\ncase 1 (v1): 2 pass, 0 fail, 1 invalid\n${experiment.url}\n`,
  );
});

describe("--json prints one document on the standard output", () => {
  it.each([
    ["pass", [[3, 0, 0]], 0],
    ["fail", [[0, 3, 0]], 1],
    ["invalid", [[0, 0, 3]], 2],
  ] as const)("%s", async (_name, counts, expected) => {
    ended(...(counts as unknown as Parameters<typeof withCounts>));
    const { code, out } = await ratel(["run", "--json"]);
    expect(code).toBe(expected);
    expect(JSON.parse(out).cases[0].counts.open).toBe(0);
    expect(out.trim().split("\n")).toHaveLength(1);
  });

  it("refusal", async () => {
    start = { status: 409, body: { error: "no_cases", message: "The eval has no case." } };
    const { code, out } = await ratel(["run", "--json"]);
    expect(code).toBe(3);
    expect(JSON.parse(out)).toEqual({ error: "no_cases", message: "The eval has no case." });
  });

  it("wrong usage and no answer", async () => {
    expect(JSON.parse((await ratel(["run", "--json", "--trials", "x"])).out).error).toBe("usage");
    const { out } = await ratel(["run", "--json"], { RATEL_BASE_URL: "http://127.0.0.1:9/api/v1" });
    expect(JSON.parse(out).error).toBe("eval_run_failed");
  });
});

it("--no-wait prints the experiment id and exits with 0", async () => {
  const { code, out } = await ratel(["run", "--no-wait"]);
  expect([code, out]).toEqual([0, `${ID}\n`]);
  expect(calls).toHaveLength(1);
  const json = await ratel(["run", "--no-wait", "--json"]);
  expect(JSON.parse(json.out).experimentId).toBe(ID);
});

it("--help prints the usage", async () => {
  const { code, out } = await ratel(["run", "--help"]);
  expect(code).toBe(0);
  expect(out).toContain("--version");
});

it("4 and one document: a completed run with no readable counts, no case or an open trial", async () => {
  for (const body of [
    { ...experiment, cases: [{}] },
    { ...experiment, cases: [] },
    {
      ...experiment,
      cases: [{ ...experiment.cases[0], counts: { pass: 0, fail: 0, invalid: 0, open: 3 } }],
    },
  ]) {
    reads = [{ status: 200, body }];
    const { code, out } = await ratel(["run", "--json"]);
    expect(code).toBe(4);
    expect(out.trim().split("\n")).toHaveLength(1);
    expect(JSON.parse(out).error).toBe("eval_run_failed");
  }
}, 60_000);

it("--json with a wrong value prints one document, and help goes to the error output", async () => {
  const wrong = await ratel(["run", "--json=true"]);
  expect([wrong.code, JSON.parse(wrong.out).error]).toEqual([3, "usage"]);
  const help = await ratel(["run", "--json", "--help"]);
  expect([help.code, help.out]).toEqual([0, ""]);
});

it("runs in one process, so a signal to it leaves no other process that waits", async () => {
  reads = [{ status: 200, body: { ...experiment, state: "running" } }];
  const parent = spawn(process.execPath, [CLI, "eval", "run"], {
    cwd: EMPTY,
    env: { PATH: process.env.PATH, RATEL_BASE_URL: `${url}/api/v1`, RATEL_API_KEY: "rtl_key" },
    stdio: "ignore",
  });
  const ended = new Promise((done) => parent.on("close", (_code, signal) => done(signal)));
  while (calls.length < 2) await new Promise((done) => setTimeout(done, 50));
  const { stdout } = spawnSync("pgrep", ["-P", String(parent.pid)], { encoding: "utf8" });
  parent.kill("SIGTERM");
  expect(stdout).toBe("");
  expect(await ended).toBe("SIGTERM");
});

describe("--compare-baseline", () => {
  const COMPARE = ["run", "--compare-baseline"];
  const verdict = (value: string) => {
    comparisons = [{ status: 200, body: { ...comparison, verdict: value } }];
  };

  it.each([
    ["improved", 0],
    ["unchanged", 0],
    ["regressed", 1],
    ["inconclusive", 2],
  ])("%s exits with %s, whatever the cases of the run are", async (value, expected) => {
    verdict(value);
    ended([0, 3, 0]);
    const { code, out } = await ratel([...COMPARE, "--json"]);
    expect(code).toBe(expected);
    expect(out.trim().split("\n")).toHaveLength(1);
    const document = JSON.parse(out);
    expect(document.experimentId).toBe(ID);
    expect(document.comparison).toEqual({ ...comparison, verdict: value });
  });

  it("reads the baseline, starts the run and reads the comparison", async () => {
    await ratel(COMPARE);
    expect(calls.map(({ method, path }) => `${method} ${path}`)).toEqual([
      "GET /api/v1/evals/baseline",
      "POST /api/v1/evals/runs",
      `GET /api/v1/evals/experiments/${ID}`,
      `GET /api/v1/evals/experiments/${ID}/comparison`,
    ]);
  });

  it("prints the verdict, the rule and the changed cases", async () => {
    const { code, out } = await ratel(COMPARE);
    expect(code).toBe(1);
    expect(out).toContain(`Regressed against the baseline ${comparison.baseline.experimentId}`);
    expect(out).toContain(comparison.rule);
    expect(out).toContain("regressed: Late order is refused (v1 to v1): 3/3 pass to 2/3 pass");
    expect(out).toContain(comparison.url);
  });

  it("a case of one side has no counts on the other", async () => {
    const [row] = comparison.changedCases;
    const changedCases = [
      { ...row, change: "added", baseline: null },
      { ...row, change: "removed", candidate: null },
    ];
    comparisons = [{ status: 200, body: { ...comparison, verdict: "unchanged", changedCases } }];
    const { code, out } = await ratel(COMPARE);
    expect(code).toBe(0);
    expect(out).toContain("added: Late order is refused (v1): 2/3 pass");
    expect(out).toContain("removed: Late order is refused (v1): 3/3 pass");
  });

  it("3: other scorers print the reason", async () => {
    comparisons = [{ status: 409, body: differ }];
    const text = await ratel(COMPARE);
    expect(text.code).toBe(3);
    expect(text.err).toContain(differ.message);
    const json = await ratel([...COMPARE, "--json"]);
    expect(json.code).toBe(3);
    expect(JSON.parse(json.out)).toEqual({ ...differ, experimentId: ID, url: experiment.url });
  });

  it("3: with no baseline it starts no run", async () => {
    baseline = { status: 200, body: { baseline: null } };
    const { code, out } = await ratel([...COMPARE, "--json"]);
    expect(code).toBe(3);
    expect(JSON.parse(out).error).toBe("no_baseline");
    expect(calls).toHaveLength(1);
  });

  it("3: it cannot be used with --no-wait", async () => {
    expect((await ratel([...COMPARE, "--no-wait"])).code).toBe(3);
    expect(calls).toEqual([]);
  });

  it("a read of the comparison that fails is tried again", async () => {
    comparisons = [
      { status: 503, body: "" },
      { status: 200, body: comparison },
    ];
    expect((await ratel(COMPARE)).code).toBe(1);
  }, 15_000);

  it.each([
    { ...comparison, verdict: "better" },
    { ...comparison, changedCases: null },
    { ...comparison, changedCases: [null] },
    { ...comparison, changedCases: [{}] },
    { ...comparison, rule: undefined },
    { ...comparison, baseline: null },
    { ...comparison, verdict: ["regressed"] },
    { ...comparison, otherCases: undefined },
    { ...comparison, candidate: undefined },
    { ...comparison, candidate: { ...comparison.candidate, experimentId: "another" } },
    {
      ...comparison,
      changedCases: [{ ...comparison.changedCases[0], baseline: null, candidate: null }],
    },
  ])("4: a comparison that is not readable", async (body) => {
    comparisons = [{ status: 200, body }];
    const { code, out } = await ratel([...COMPARE, "--json"]);
    expect(code).toBe(4);
    expect(JSON.parse(out)).toMatchObject({ error: "eval_run_failed", experimentId: ID });
  }, 30_000);

  it.each([["--json"], []])("4: an experiment that is not readable %j", async (...args) => {
    for (const cases of [[{}], []]) {
      reads = [{ status: 200, body: { ...experiment, cases } }];
      const { code, err } = await ratel([...COMPARE, ...args]);
      expect(code).toBe(4);
      expect(err).toContain("not readable");
    }
  });

  it.each([
    [false],
    ["x"],
    [{}],
  ])("4: a baseline %j that is not readable starts no run", async (value) => {
    baseline = { status: 200, body: { baseline: value } };
    expect((await ratel(COMPARE)).code).toBe(4);
    expect(calls).toHaveLength(1);
  });

  it.each([
    [404, "not_found", 3],
    [409, "no_baseline", 3],
    [409, "same_experiment", 3],
    [409, "not_completed", 3],
    [429, "rate_limited", 4],
  ])(
    "after the run %s %s exits with the id of the run",
    async (status, error, expected) => {
      comparisons = [{ status, body: { error, message: error } }];
      const { code, out } = await ratel([...COMPARE, "--json"]);
      expect(code).toBe(expected);
      expect(JSON.parse(out)).toMatchObject({ error, experimentId: ID, url: experiment.url });
    },
    30_000,
  );

  it("3: an old server with no baseline route gets no run", async () => {
    baseline = { status: 404, body: { error: "not_found", message: "Not found." } };
    expect((await ratel(COMPARE)).code).toBe(3);
    expect(calls).toHaveLength(1);
  });

  it("a wait that fails has the id of the paid run", async () => {
    reads = [{ status: 404, body: { error: "not_found", message: "Not found." } }];
    const { code, out } = await ratel([...COMPARE, "--json"]);
    expect(code).toBe(3);
    expect(JSON.parse(out)).toMatchObject({ experimentId: ID, url: experiment.url });
  });

  it("the text has no control character of a name or of a message", async () => {
    const [row] = comparison.changedCases;
    const name = "x\u001b[2J\u0007\nUnchanged: fake\u009b";
    comparisons = [{ status: 200, body: { ...comparison, changedCases: [{ ...row, name }] } }];
    // The counts of the run print the same name.
    reads = [
      {
        status: 200,
        body: {
          ...withCounts([3, 0, 0]),
          cases: [
            { ...experiment.cases[0], name, counts: { pass: 3, fail: 0, invalid: 0, open: 0 } },
          ],
        },
      },
    ];
    const { out } = await ratel(COMPARE);
    expect(out).toContain("x [2J  Unchanged: fake");
    // biome-ignore lint/suspicious/noControlCharactersInRegex: the test is about them
    expect(out).not.toMatch(/[\u0000-\u0009\u000b-\u001f\u007f-\u009f]|\nUnchanged: fake/);
    comparisons = [{ status: 409, body: { error: "x", message: "a\n::error::b" } }];
    expect((await ratel(COMPARE)).err).not.toContain("\n::error::");
  });

  it("4: no answer about the baseline", async () => {
    baseline = { status: 500, body: "" };
    expect((await ratel(COMPARE)).code).toBe(4);
  }, 30_000);
});

describe("baseline pin", () => {
  it("0: pins the experiment", async () => {
    const { code, out } = await ratel(["baseline", "pin", ID]);
    expect([code, out]).toEqual([0, `${pinned.baseline.experimentId}\n`]);
    expect(calls.map(({ method, path, auth, body }) => [method, path, auth, body])).toEqual([
      ["PUT", "/api/v1/evals/baseline", "Bearer rtl_key", JSON.stringify({ experimentId: ID })],
    ]);
    const json = await ratel(["baseline", "pin", ID, "--json"]);
    expect(JSON.parse(json.out)).toEqual(pinned);
  });

  it("3: refused, and 4: the server fails", async () => {
    baseline = { status: 409, body: { error: "not_completed", message: "It has not ended." } };
    const refused = await ratel(["baseline", "pin", ID]);
    expect(refused.code).toBe(3);
    expect(refused.err).toContain("It has not ended.");
    baseline = { status: 500, body: "" };
    expect((await ratel(["baseline", "pin", ID])).code).toBe(4);
    baseline = { status: 200, body: { baseline: null } };
    expect((await ratel(["baseline", "pin", ID])).code).toBe(4);
  });

  it.each([
    [["baseline"]],
    [["baseline", "pin"]],
    [["baseline", "pin", ID, "more"]],
    [["baseline", "pin", ID, "--compare-baseline"]],
    [["baseline", "pin", ID, "--trials", "3"]],
    [["baseline", "nope", ID]],
  ])("3: wrong usage %j", async (args) => {
    expect((await ratel(args)).code).toBe(3);
    expect(calls).toEqual([]);
  });
});

describe("the suite and --eval", () => {
  const withDelta = vector("experiment.suite-delta");
  const withRefusal = vector("experiment.suite-delta-refusal");

  it("runs the suite: the body has no evals and no trials", async () => {
    reads = [{ status: 200, body: withDelta }];
    const { code } = await ratel(["run"]);
    expect(code).toBe(1);
    expect(JSON.parse(calls[0]?.body ?? "")).toEqual(vector("start-runs.request.each"));
  });

  it("--eval sends the names of the evals, in order", async () => {
    reads = [{ status: 200, body: withDelta }];
    await ratel(["run", "--eval", "Refunds", "--eval", "Tone", "--label", "refunds"]);
    expect(JSON.parse(calls[0]?.body ?? "")).toEqual(vector("start-runs.request.evals"));
  });

  it("--eval --json prints each eval with its score, counts, scorers and delta", async () => {
    reads = [{ status: 200, body: withDelta }];
    const { code, out } = await ratel(["run", "--eval", "Refunds", "--json"]);
    expect(code).toBe(1);
    expect(out.trim().split("\n")).toHaveLength(1);
    const [row] = withDelta.evals;
    expect(JSON.parse(out).evals).toEqual([
      {
        name: "Refunds",
        score: row.score,
        counts: row.counts,
        scorers: row.scorers,
        delta: row.delta,
      },
    ]);
  });

  it("--json has a null delta when the eval has no delta", async () => {
    reads = [{ status: 200, body: withRefusal }];
    const { out } = await ratel(["run", "--json"]);
    expect(
      JSON.parse(out).evals.map(({ name, delta }: { name: string; delta: unknown }) => [
        name,
        delta,
      ]),
    ).toEqual([
      ["Refunds", null],
      ["Tone", null],
    ]);
  });

  it("--json without evals from Ratel has no evals", async () => {
    const { out } = await ratel(["run", "--json"]);
    expect(JSON.parse(out)).not.toHaveProperty("evals");
  });

  it("prints one line for each eval: the score, pass/total and the delta in points", async () => {
    reads = [{ status: 200, body: withDelta }];
    const { out } = await ratel(["run"]);
    expect(out).toBe(
      `Late refund (v1): 2 pass, 1 fail, 0 invalid\neval Refunds (v2): 66.7% score, 2/3 pass, +33.3 points\n${withDelta.url}\n`,
    );
  });

  it("prints the reason when an eval has no delta, and no score with no valid trial", async () => {
    reads = [{ status: 200, body: withRefusal }];
    const { out } = await ratel(["run"]);
    expect(out).toContain("eval Refunds (v2): 66.7% score, 2/3 pass, no delta: scorers_differ\n");
    expect(out).toContain("eval Tone (v1): no score, 0/3 pass, no delta: not_in_baseline\n");
  });

  it("the eval line has no control character of a name", async () => {
    const [row] = withDelta.evals;
    reads = [
      { status: 200, body: { ...withDelta, evals: [{ ...row, name: "x\u001b[2J\nfake" }] } },
    ];
    const { out } = await ratel(["run"]);
    expect(out).toContain("eval x [2J fake (v2)");
  });

  it("evals that are not readable change nothing: the counts of the cases give the exit code", async () => {
    for (const evals of ["x", [null], [{}], [{ name: "Refunds", score: "high", counts: null }]]) {
      reads = [{ status: 200, body: { ...withCounts([3, 0, 0]), evals } }];
      const { code, out } = await ratel(["run", "--json"]);
      expect(code).toBe(0);
      expect(JSON.parse(out).experimentId).toBe(ID);
    }
  }, 30_000);

  it.each([
    [["run", "--eval", ""]],
    [["run", "--eval", "  "]],
    [["run", "--eval", "x".repeat(201)]],
    [["run", ...Array.from({ length: 101 }, (_, i) => ["--eval", `e${i}`]).flat()]],
    [["baseline", "pin", ID, "--eval", "Refunds"]],
  ])("3: wrong --eval %#", async (args) => {
    expect((await ratel(args)).code).toBe(3);
    expect(calls).toEqual([]);
  });

  it("3: wrong_sandbox is a refusal, like no_cases", async () => {
    start = {
      status: 409,
      body: { error: "wrong_sandbox", message: "The eval Tone is of another agent." },
    };
    const { code, out } = await ratel(["run", "--eval", "Tone", "--json"]);
    expect(code).toBe(3);
    expect(JSON.parse(out)).toEqual({
      error: "wrong_sandbox",
      message: "The eval Tone is of another agent.",
    });
  });

  it("3: an unknown eval name is a refusal", async () => {
    start = { status: 404, body: { error: "not_found", message: "No eval Nope." } };
    expect((await ratel(["run", "--eval", "Nope"])).code).toBe(3);
  });

  it("--compare-baseline: an excluded case that went from pass to fail does not decide the exit code", async () => {
    const excluded = vector("comparison.excluded");
    excluded.candidate.experimentId = ID;
    comparisons = [{ status: 200, body: excluded }];
    reads = [{ status: 200, body: withDelta }];
    const { code, out } = await ratel(["run", "--compare-baseline"]);
    expect(code).toBe(0);
    expect(out).toContain("excluded: Tone stays friendly");
    expect(out).toContain("eval Refunds (v2): 66.7% score");
  });
});

describe("a run of each eval (POST /evals/runs)", () => {
  const runs = vector("start-runs.response.each");
  const [refunds, tone] = runs.runs;
  const answer = (parts: unknown[], skipped: unknown[] = []) => {
    start = { status: 201, body: { runs: parts, skipped } };
  };

  it("--json prints one document with a part for each eval, and the worst exit code", async () => {
    answer([refunds, { ...tone, experimentId: ID }]);
    ended([3, 0, 0]);
    const passed = await ratel(["run", "--json"]);
    expect(passed.code).toBe(0);
    expect(passed.out.trim().split("\n")).toHaveLength(1);
    const document = JSON.parse(passed.out);
    expect(document.runs.map(({ eval: name }: { eval: string }) => name)).toEqual([
      "Refunds",
      "Tone",
    ]);
    expect(document.runs[0]).toMatchObject({ evalId: refunds.evalId, experimentId: ID });
    expect(document.runs[0].cases[0].counts.pass).toBe(3);
    ended([2, 1, 0]);
    expect((await ratel(["run", "--json"])).code).toBe(1);
    ended([2, 0, 1]);
    expect((await ratel(["run"])).code).toBe(2);
  });

  it("3: a refused eval is a part with its reason; the other evals still run", async () => {
    answer(runs.runs, runs.skipped);
    ended([3, 0, 0]);
    const { code, out } = await ratel(["run", "--json"]);
    expect(code).toBe(3);
    const document = JSON.parse(out);
    expect(document.runs[1]).toEqual({
      eval: "Tone",
      evalId: tone.evalId,
      error: tone.error,
      message: tone.message,
    });
    expect(document.skipped[0].eval).toBe(runs.skipped[0].eval);
    const text = await ratel(["run"]);
    expect(text.out).toContain(`eval Refunds:\ncase 0 (v1): 3 pass, 0 fail, 0 invalid`);
    expect(text.out).toContain(`eval Tone: refused: ${tone.message}`);
    expect(text.out).toContain(`eval ${runs.skipped[0].eval}: left out:`);
  });

  it("a version of another sandbox does not apply: the note says so", async () => {
    const note = "The version is of the sandbox support-demo. This eval runs on billing-demo.";
    answer([{ ...refunds, note }]);
    ended([3, 0, 0]);
    const { out } = await ratel(["run", "--version", ID]);
    expect(out).toContain(`eval Refunds: ${note}`);
    expect(JSON.parse(calls.find(({ method }) => method === "POST")?.body ?? "")).toEqual({
      sandboxVersion: ID,
    });
  });

  it("--dataset and --param name=a,b send another dataset and a matrix", async () => {
    answer([refunds]);
    await ratel([
      "run",
      "-f",
      YAML,
      "--dataset",
      "Golden",
      "--param",
      "tone=dry,warm",
      "--param",
      "model=x,y",
    ]);
    expect(JSON.parse(calls[0]?.body ?? "")).toEqual(vector("start-runs.request.matrix"));
  });

  it.each([
    [["run", "-f", YAML, "--param", "model=x,z"]],
    [["run", "-f", YAML, "--param", "tone=a,a"]],
    [["run", "-f", YAML, "--param", "tone=a,b,c,d,e,f,g", "--param", "model=x,y"]],
    [["run", "--dataset", ""]],
    [["baseline", "pin", ID, "--dataset", "Golden"]],
  ])("3: wrong %j", async (args) => {
    expect((await ratel(args)).code).toBe(3);
    expect(calls).toEqual([]);
  });

  it("--compare-baseline reads the comparison of each run; a pin of an eval is a baseline", async () => {
    baseline = { status: 200, body: vector("baseline.of-evals") };
    answer([refunds]);
    ended([3, 0, 0]);
    const { code, out } = await ratel(["run", "--compare-baseline", "--json"]);
    expect(code).toBe(1);
    expect(JSON.parse(out).runs[0].comparison.verdict).toBe("regressed");
  });

  it("--no-wait prints the id of each run", async () => {
    answer(runs.runs);
    const { code, out } = await ratel(["run", "--no-wait"]);
    expect(code).toBe(3);
    expect(out).toContain(`eval Refunds: ${refunds.experimentId}`);
  });
});
