import { randomBytes } from "node:crypto";
import { existsSync, mkdtempSync } from "node:fs";
import { createServer, type Server } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { RunInput, RunResult } from "../src/contract.js";

// The runner starts a process of the built package, so the tests use the build.
const { runSandbox, MAX_RESULT_BYTES } = (await import(
  new URL("../dist/runner.js", import.meta.url).href
)) as typeof import("../src/runner.js");

const SECRET = `sk-${randomBytes(12).toString("hex")}`;
const TOKEN = `rt_${randomBytes(12).toString("hex")}`;
const RUN_ID = "3f2c1a52-5f0d-4c6e-9a55-0c9d7f1f7e11";
const dir = (name: string) => fileURLToPath(new URL(`./fixtures/${name}`, import.meta.url));

type Post = { path: string; auth: string | undefined; body: unknown };
let server: Server;
let url: string;
let posts: Post[];
let answers: number[];
let tracesHang: boolean;

beforeEach(async () => {
  posts = [];
  answers = [];
  tracesHang = false;
  server = createServer((req, res) => {
    let text = "";
    req.on("data", (chunk) => {
      text += chunk;
    });
    req.on("end", () => {
      posts.push({ path: req.url ?? "", auth: req.headers.authorization, body: JSON.parse(text) });
      if (tracesHang && req.url === "/v1/traces") return;
      res.statusCode = (req.url?.endsWith("/result") ? answers.shift() : undefined) ?? 202;
      if (res.statusCode === 307) res.setHeader("location", "/elsewhere");
      res.end("{}");
    });
  });
  await new Promise<void>((done) => server.listen(0, "127.0.0.1", done));
  url = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
});
afterEach(() => {
  server.closeAllConnections();
  return new Promise((done) => server.close(done));
});

async function run(
  project: string,
  over: Partial<RunInput> = {},
  env: Record<string, string> = {},
) {
  const runInput: RunInput = {
    v: 1,
    runId: RUN_ID,
    input: { message: "hi" },
    params: {},
    toolPolicy: { sideEffects: "fake" },
    fixtures: [],
    ...over,
  };
  let logs = "";
  const log = (text: string) => {
    logs += text;
  };
  const result = runSandbox({
    projectDir: dir(project),
    secretNames: ["MODEL_KEY"],
    env: {
      PATH: process.env.PATH,
      RATEL_URL: url,
      RATEL_RUN_TOKEN: TOKEN,
      RATEL_RUN_INPUT: gzipSync(JSON.stringify(runInput)).toString("base64"),
      MODEL_KEY: SECRET,
      ...env,
    },
    stdout: log,
    stderr: log,
    retryDelaysMs: [1, 1],
  });
  await result.catch(() => {});
  return { result: await result, logs: () => logs };
}

const posted = () => posts.find((post) => post.path.endsWith("/result"))?.body as RunResult;

describe("the runner", () => {
  it("applies the tool policy, posts the result and sends a trace with the run token", async () => {
    const marker = join(mkdtempSync(join(tmpdir(), "sandbox-")), "refunded");
    await run(
      "support-agent",
      {
        params: { temperature: 0 },
        fixtures: [
          { tool: "refund_order", arguments: { id: "7", reason: "late" }, result: { pinned: 1 } },
        ],
      },
      { MARKER_FILE: marker },
    );

    const result = posted();
    expect(result.status).toBe("completed");
    expect(
      result.toolCalls.map(({ name, arguments: args, result, source }) => [
        name,
        args,
        result,
        source,
      ]),
    ).toEqual([
      ["lookup_order", { id: "7" }, { id: "7", total: 12 }, "real"],
      ["refund_order", { reason: "late", id: "7" }, { pinned: 1 }, "fixture"],
      ["refund_order", { id: "8" }, { refunded: true, fakeFor: "8" }, "fake"],
    ]);
    expect(existsSync(marker)).toBe(false);
    expect(result.output).toMatchObject({
      kind: "order",
      text: "HI",
      params: { temperature: 0 },
      runInput: null,
      runToken: null,
      endpoint: `${url}/v1/traces`,
      joined: true,
    });
    expect(result.usage).toMatchObject({ inputTokens: 6, outputTokens: 2 });
    expect(posts.map(({ path, auth }) => [path, auth])).toEqual([
      ["/v1/traces", `Bearer ${TOKEN}`],
      [`/api/v1/sandbox-runs/${RUN_ID}/result`, `Bearer ${TOKEN}`],
    ]);
    const trace = posts[0]?.body as {
      resourceSpans: [{ scopeSpans: [{ spans: [{ traceId: string; name: string }] }] }];
    };
    const span = trace.resourceSpans[0].scopeSpans[0].spans[0];
    expect(span.traceId).toBe(result.traceId);
    expect(span.name).toBe("invoke_agent support-agent");
  });

  it("never puts a secret value or the run token in the result, the trace or the logs", async () => {
    const { logs } = await run("support-agent");
    const sent = JSON.stringify(posts.map((post) => post.body));
    for (const text of [sent, logs()]) {
      expect(text).not.toContain(SECRET);
      expect(text).not.toContain(TOKEN);
      expect(text).toContain("[redacted]");
    }
    expect(logs()).toContain("split [redacted]");
  });

  it.each([
    ["throw", "TypeError", /bad key \[redacted\]/],
    ["exit", "AgentExit", /exit code 3/],
    ["kill", "AgentExit", /SIGKILL/],
    ["uncaught", "AgentExit", /exit code/],
  ])("an agent that ends with %s gives a failed result", async (input, type, message) => {
    const { logs } = await run("crash-agent", { input });
    const result = posted();
    expect(result.status).toBe("failed");
    expect(result.error?.type).toBe(type);
    expect(result.error?.message).toMatch(message);
    expect(result.toolCalls.map((call) => call.name)).toEqual(["ping"]);
    expect(JSON.stringify(posts) + logs()).not.toContain(SECRET);
  });

  it("redacts each line of a secret with more than one line", async () => {
    const lines = ["-----BEGIN KEY-----", randomBytes(16).toString("hex"), "-----END KEY-----"];
    const { logs } = await run("crash-agent", { input: "print" }, { MODEL_KEY: lines.join("\n") });
    for (const line of lines) expect(logs() + JSON.stringify(posts)).not.toContain(line);
  });

  it("a secret that is part of a tool name or is a number does not change the shape", async () => {
    await run("crash-agent", { input: "x" }, { MODEL_KEY: "ping" });
    expect(posted().toolCalls[0]?.name).toBe("ping");
    expect(typeof posted().usage.durationMs).toBe("number");
  });

  it("a process that the agent leaves does not hold the run", async () => {
    const started = Date.now();
    await run("crash-agent", { input: "leave" });
    expect(posted()).toMatchObject({ status: "completed", output: "left" });
    expect(Date.now() - started).toBeLessThan(8_000);
  });

  it("a message that the agent sends itself is not the answer", async () => {
    await run("crash-agent", { input: "message" });
    expect(posted()).toMatchObject({ status: "completed", output: "after" });
  });

  it("a trace endpoint that does not answer delays the result for a short time only", async () => {
    tracesHang = true;
    const started = Date.now();
    await run("crash-agent", { input: "x" });
    expect(posted().status).toBe("completed");
    expect(Date.now() - started).toBeLessThan(15_000);
  }, 90_000);

  it("a negative usage does not change the sum", async () => {
    await run("crash-agent", { input: "usage" });
    expect(posted().usage).toMatchObject({ inputTokens: 5 });
    expect(posted().usage.outputTokens).toBeUndefined();
  });

  it("the post of the result does not follow a redirect", async () => {
    answers = [307, 307, 307];
    await expect(run("crash-agent", { input: "x" })).rejects.toThrow(/result/);
  });

  it("a result over the size limit becomes a failed result", async () => {
    await run("crash-agent", { input: "large" });
    expect(posted().error?.type).toBe("ResultTooLarge");
    expect(JSON.stringify(posted()).length).toBeLessThan(MAX_RESULT_BYTES);
  });

  it("tries again when Ratel is not available, and stops when Ratel refuses", async () => {
    answers = [503, 503, 202];
    await run("crash-agent", { input: "x" });
    expect(posts.filter((post) => post.path.endsWith("/result"))).toHaveLength(3);
    posts = [];
    answers = [403];
    await expect(run("crash-agent", { input: "x" })).rejects.toThrow(/answered 403/);
    expect(posts.filter((post) => post.path.endsWith("/result"))).toHaveLength(1);
  });

  it("refuses an input with another tool policy", async () => {
    const toolPolicy = { sideEffects: "real" } as unknown as RunInput["toolPolicy"];
    await expect(run("support-agent", { toolPolicy })).rejects.toThrow(/tool policy/);
    expect(posts).toEqual([]);
  });
});
