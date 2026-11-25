import { spawn, spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { createServer, type Server } from "node:http";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

const CLI = fileURLToPath(new URL("../dist/cli.js", import.meta.url));
const VALID = new URL("../../../../protocol/evals/v1/conformance/valid/", import.meta.url);
const experiment = JSON.parse(readFileSync(new URL("experiment.completed.json", VALID), "utf8"));
const ID = experiment.experimentId;
const comparison = JSON.parse(readFileSync(new URL("comparison.regressed.json", VALID), "utf8"));
const pinned = JSON.parse(readFileSync(new URL("baseline.pinned.json", VALID), "utf8"));
const differ = JSON.parse(readFileSync(new URL("error.scorers-differ.json", VALID), "utf8"));

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
      env: { PATH: process.env.PATH, RATEL_URL: url, RATEL_API_KEY: "rtl_key", ...env },
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
    [["run", "--params", "[]"]],
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
    { RATEL_URL: undefined },
    { RATEL_URL: "not an address" },
    { RATEL_URL: "localhost:3000" },
    { RATEL_URL: "ftp://x" },
    { RATEL_URL: "http://user:pass@x" },
    { RATEL_API_KEY: "a\nb" },
  ])("3: a setting is wrong %j", async (env) => {
    expect((await ratel(["run"], env)).code).toBe(3);
  });

  it("4: no answer", async () => {
    expect((await ratel(["run"], { RATEL_URL: "http://127.0.0.1:9" })).code).toBe(4);
  });

  it.each([500, 503, 429])("4: the server answers %s", async (status) => {
    start = { status, body: "oops" };
    expect((await ratel(["run"])).code).toBe(4);
  });

  it("4: the answer is not readable", async () => {
    start = { status: 200, body: "<html>" };
    expect((await ratel(["run"])).code).toBe(4);
  });
});

it("sends the options with the key", async () => {
  await ratel(["run", "--sandbox-version", ID, "--trials", "5", "--params", '{"model":"x"}']);
  expect(calls.map(({ method, path, auth }) => [method, path, auth])).toEqual([
    ["POST", "/api/v1/evals/experiments", "Bearer rtl_key"],
    ["GET", `/api/v1/evals/experiments/${ID}`, "Bearer rtl_key"],
  ]);
  expect(JSON.parse(calls[0]?.body ?? "")).toEqual({
    sandboxVersion: ID,
    trials: 5,
    params: { model: "x" },
  });
});

it("the default is the latest version and the trials of Ratel", async () => {
  await ratel(["run"]);
  expect(JSON.parse(calls[0]?.body ?? "")).toEqual({ sandboxVersion: "latest" });
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
    start = { status: 409, body: { error: "no_cases", message: "No case is approved." } };
    const { code, out } = await ratel(["run", "--json"]);
    expect(code).toBe(3);
    expect(JSON.parse(out)).toEqual({ error: "no_cases", message: "No case is approved." });
  });

  it("wrong usage and no answer", async () => {
    expect(JSON.parse((await ratel(["run", "--json", "--trials", "x"])).out).error).toBe("usage");
    const { out } = await ratel(["run", "--json"], { RATEL_URL: "http://127.0.0.1:9" });
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
  expect(out).toContain("--sandbox-version");
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
    env: { PATH: process.env.PATH, RATEL_URL: url, RATEL_API_KEY: "rtl_key" },
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
      "POST /api/v1/evals/experiments",
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
  ])("4: a comparison that is not readable", async (body) => {
    comparisons = [{ status: 200, body }];
    const { code, out } = await ratel([...COMPARE, "--json"]);
    expect(code).toBe(4);
    expect(JSON.parse(out)).toMatchObject({ error: "eval_run_failed", experimentId: ID });
  }, 30_000);

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
