import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import { createServer, type Server } from "node:http";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

const CLI = fileURLToPath(new URL("../dist/cli.js", import.meta.url));
const VALID = new URL("../../../../protocol/evals/v1/conformance/valid/", import.meta.url);
const experiment = JSON.parse(readFileSync(new URL("experiment.completed.json", VALID), "utf8"));
const ID = experiment.experimentId;

type Call = { method: string; path: string; auth: string | undefined; body: string };
type Answer = { status: number; body: unknown };
let server: Server;
let url: string;
let calls: Call[];
/** The answers of the reads, in order. The last one repeats. */
let reads: Answer[];
let start: Answer;

beforeEach(async () => {
  calls = [];
  start = { status: 201, body: { experimentId: ID, state: "running", url: experiment.url } };
  reads = [{ status: 200, body: experiment }];
  server = createServer((req, res) => {
    let body = "";
    req.on("data", (chunk) => {
      body += chunk;
    });
    req.on("end", () => {
      const { method = "", url: path = "", headers } = req;
      calls.push({ method, path, auth: headers.authorization, body });
      const answer =
        method === "POST" ? start : ((reads.length > 1 ? reads.shift() : reads[0]) as Answer);
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
