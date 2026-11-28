import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  currentRun,
  type RunRequest,
  recordUsage,
  type ServeHandle,
  serve,
  tool,
  validateRunRequest,
} from "./index.js";

const handles: ServeHandle[] = [];

afterEach(async () => {
  await Promise.all(handles.splice(0).map((h) => h.close()));
  vi.unstubAllEnvs();
});

async function start(run: Parameters<typeof serve>[0], options: Parameters<typeof serve>[1] = {}) {
  const handle = await serve(run, { port: 0, host: "127.0.0.1", ...options });
  handles.push(handle);
  return `http://127.0.0.1:${handle.port}`;
}

function request(overrides: Partial<RunRequest> = {}): RunRequest {
  return {
    v: 1,
    runId: "run_1",
    input: { message: "hi" },
    params: {},
    tools: {},
    fixtures: [],
    ...overrides,
  };
}

async function post(base: string, body: unknown) {
  const res = await fetch(`${base}/ratel/run`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });
  return { status: res.status, body: await res.json() };
}

describe("serve", () => {
  it("answers GET /ratel/health with 200", async () => {
    const base = await start(async () => "ok");
    const res = await fetch(`${base}/ratel/health`);
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ ok: true });
  });

  it("reads $PORT when no port is given", async () => {
    vi.stubEnv("PORT", "0");
    const handle = await serve(async () => "ok", { host: "127.0.0.1" });
    handles.push(handle);
    expect(handle.port).toBeGreaterThan(0);
  });

  it("calls run with the input, the params, the tools and the run id, and answers the output", async () => {
    const run = vi.fn(
      async ({ input, params }: { input: unknown; params: Record<string, unknown> }) => ({
        echo: input,
        tone: params.tone,
      }),
    );
    const base = await start(run);
    const tools = { lookup_order: { mode: "live" as const } };
    const { status, body } = await post(base, request({ params: { tone: "dry" }, tools }));

    expect(status).toBe(200);
    expect(body).toEqual({ output: { echo: { message: "hi" }, tone: "dry" }, toolCalls: [] });
    expect(run).toHaveBeenCalledWith(
      expect.objectContaining({
        input: { message: "hi" },
        params: { tone: "dry" },
        tools,
        runId: "run_1",
      }),
    );
  });

  it("gives the run id and the traceparent to the run scope", async () => {
    const traceparent = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01";
    const base = await start(async () => {
      await new Promise((r) => setTimeout(r, 1));
      return currentRun();
    });
    const { body } = await post(base, request({ traceparent }));
    expect(body.output).toEqual({ runId: "run_1", traceparent });
  });

  it("adds the usage that the run records", async () => {
    const base = await start(async () => {
      recordUsage({ inputTokens: 10, outputTokens: 2, costUsd: 0.01 });
      recordUsage({ inputTokens: 5, outputTokens: 1 });
      return "done";
    });
    const { body } = await post(base, request());
    expect(body.usage).toEqual({ inputTokens: 15, outputTokens: 3, costUsd: 0.01 });
  });

  it("refuses an invalid request with 400 and the error shape", async () => {
    const run = vi.fn();
    const base = await start(run);
    const { status, body } = await post(base, {
      v: 1,
      input: 1,
      params: {},
      tools: {},
      fixtures: [],
    });
    expect(status).toBe(400);
    expect(body).toEqual({ error: { code: "invalid_request", message: "/runId is required" } });
    expect(run).not.toHaveBeenCalled();
  });

  it("refuses a body that is not JSON with 400", async () => {
    const base = await start(async () => "ok");
    const { status, body } = await post(base, "{not json");
    expect(status).toBe(400);
    expect(body.error.code).toBe("invalid_request");
  });

  it("answers 500 with the error shape when the run throws", async () => {
    const base = await start(async () => {
      throw new Error("model is down");
    });
    const { status, body } = await post(base, request());
    expect(status).toBe(500);
    expect(body).toEqual({ error: { code: "run_failed", message: "model is down" } });
  });

  it("keeps the code of a thrown error when it is a valid code", async () => {
    const base = await start(async () => {
      throw Object.assign(new Error("too slow"), { code: "timeout" });
    });
    const { body } = await post(base, request());
    expect(body.error).toEqual({ code: "timeout", message: "too slow" });
  });

  it("answers 404 with the error shape for another path", async () => {
    const base = await start(async () => "ok");
    const res = await fetch(`${base}/other`);
    expect(res.status).toBe(404);
    expect((await res.json()).error.code).toBe("not_found");
  });

  it("redacts the values of the secrets in the response", async () => {
    vi.stubEnv("RATEL_SECRET_NAMES", "ANTHROPIC_API_KEY");
    vi.stubEnv("ANTHROPIC_API_KEY", "sk-ant-very-secret");
    const base = await start(async () => {
      throw new Error("bad key sk-ant-very-secret");
    });
    const { body } = await post(base, request());
    expect(body.error.message).toBe("bad key [redacted]");
  });
});

describe("tool modes", () => {
  const refund = vi.fn(async ({ id }: { id: string }) => ({ refunded: id, real: true }));
  const refundOrder = tool("refund_order", {
    execute: refund,
    fake: async ({ id }) => ({ refunded: id, real: false }),
  });
  const cancelOrder = tool("cancel_order", { execute: async () => ({ cancelled: true }) });

  async function runWith(tools: RunRequest["tools"], fixtures: RunRequest["fixtures"] = []) {
    refund.mockClear();
    const base = await start(async () => {
      const results: unknown[] = [];
      for (const call of [() => refundOrder({ id: "42" }), () => refundOrder({ id: "42" })]) {
        try {
          results.push(await call());
        } catch (error) {
          results.push({ thrown: (error as Error).message });
        }
      }
      return results;
    });
    return post(base, request({ tools, fixtures }));
  }

  it("simulated answers from the fake handler and never runs the real function", async () => {
    const { body } = await runWith({ refund_order: { mode: "simulated" } });
    expect(refund).not.toHaveBeenCalled();
    expect(body.output[0]).toEqual({ refunded: "42", real: false });
    expect(body.toolCalls[0]).toEqual({
      name: "refund_order",
      args: { id: "42" },
      result: { refunded: "42", real: false },
      mode: "simulated",
    });
  });

  it("simulated is the default for a tool that the request does not name", async () => {
    const { body } = await runWith({});
    expect(refund).not.toHaveBeenCalled();
    expect(body.toolCalls[0].mode).toBe("simulated");
  });

  it("recorded answers from the fixture with the same normalized arguments, in order", async () => {
    const fixtures = [
      { tool: "refund_order", args: { id: "42" }, result: { refunded: "42", from: "first" } },
      { tool: "refund_order", args: { id: "42" }, result: { refunded: "42", from: "second" } },
      { tool: "refund_order", args: { id: "7" }, result: { refunded: "7" } },
    ];
    const { body } = await runWith({ refund_order: { mode: "recorded" } }, fixtures);
    expect(refund).not.toHaveBeenCalled();
    expect(body.output).toEqual([
      { refunded: "42", from: "first" },
      { refunded: "42", from: "second" },
    ]);
    expect(body.toolCalls.map((c: { mode: string }) => c.mode)).toEqual(["recorded", "recorded"]);
  });

  it("recorded with no fixture fails the call with no_fixture", async () => {
    const { body } = await runWith({ refund_order: { mode: "recorded" } }, []);
    expect(body.toolCalls[0]).toMatchObject({ mode: "recorded", error: { code: "no_fixture" } });
    expect(body.output[0].thrown).toContain("refund_order");
  });

  it("live runs the real function", async () => {
    const { body } = await runWith({ refund_order: { mode: "live" } });
    expect(refund).toHaveBeenCalledTimes(2);
    expect(body.toolCalls[0]).toEqual({
      name: "refund_order",
      args: { id: "42" },
      result: { refunded: "42", real: true },
      mode: "live",
    });
  });

  it("simulated with no fake handler fails the call with an error that names the tool", async () => {
    const base = await start(async () => {
      try {
        return await cancelOrder({ id: "42" });
      } catch (error) {
        return { thrown: (error as Error).message };
      }
    });
    const { body } = await post(base, request({ tools: { cancel_order: { mode: "simulated" } } }));
    expect(body.output.thrown).toBe("The tool cancel_order is simulated and has no fake handler.");
    expect(body.toolCalls[0]).toEqual({
      name: "cancel_order",
      args: { id: "42" },
      mode: "simulated",
      error: {
        code: "no_fake",
        message: "The tool cancel_order is simulated and has no fake handler.",
      },
    });
  });

  it("a failing real function is recorded with tool_failed and rethrown", async () => {
    const broken = tool("broken", {
      execute: async () => {
        throw new Error("db down");
      },
    });
    const base = await start(async () => {
      await broken({}).catch(() => undefined);
      return "survived";
    });
    const { body } = await post(base, request({ tools: { broken: { mode: "live" } } }));
    expect(body.output).toBe("survived");
    expect(body.toolCalls[0]).toMatchObject({
      mode: "live",
      error: { code: "tool_failed", message: "db down" },
    });
  });

  it("outside a run, a tool runs the real function and records nothing", async () => {
    refund.mockClear();
    await expect(refundOrder({ id: "1" })).resolves.toEqual({ refunded: "1", real: true });
    expect(refund).toHaveBeenCalledOnce();
  });

  it("concurrent runs keep their own modes and calls", async () => {
    const base = await start(async ({ input }) => {
      await new Promise((r) => setTimeout(r, input === "slow" ? 20 : 1));
      return refundOrder({ id: String(input) });
    });
    const [a, b] = await Promise.all([
      post(base, request({ runId: "a", input: "slow", tools: { refund_order: { mode: "live" } } })),
      post(
        base,
        request({ runId: "b", input: "fast", tools: { refund_order: { mode: "simulated" } } }),
      ),
    ]);
    expect(a.body.toolCalls).toHaveLength(1);
    expect(a.body.toolCalls[0].mode).toBe("live");
    expect(b.body.toolCalls).toHaveLength(1);
    expect(b.body.toolCalls[0].mode).toBe("simulated");
  });
});

describe("validateRunRequest", () => {
  type Vector = { schema: string; name: string; valid: boolean; path?: string; document: unknown };
  const { vectors } = JSON.parse(
    readFileSync(
      resolve(import.meta.dirname, "../../../../../protocol/sandbox/v1/conformance/vectors.json"),
      "utf8",
    ),
  ) as { vectors: Vector[] };

  it.each(
    vectors.filter((v) => v.schema === "run-request").map((v) => [v.name, v] as const),
  )("agrees with the protocol vector %s", (_name, vector) => {
    const errors = validateRunRequest(vector.document);
    if (vector.valid) expect(errors).toEqual([]);
    else expect(errors[0]?.path).toBe(vector.path);
  });
});

describe("the serve subpath", () => {
  it("is exported from package.json", () => {
    const pkg = JSON.parse(
      readFileSync(resolve(import.meta.dirname, "../../package.json"), "utf8"),
    );
    expect(pkg.exports["./serve"]).toEqual({
      types: "./dist/serve/index.d.ts",
      default: "./dist/serve/index.js",
    });
  });

  it("loads without the native addon", async () => {
    vi.resetModules();
    vi.doMock("../../native/index.cjs", () => {
      throw new Error("the native addon was loaded");
    });
    await expect(import("./index.js")).resolves.toBeDefined();
    vi.doUnmock("../../native/index.cjs");
  });
});
