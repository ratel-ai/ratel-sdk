import assert from "node:assert/strict";
import { type ChildProcess, spawn } from "node:child_process";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { after, before, test } from "node:test";
import { fileURLToPath } from "node:url";

/** A model that asks for refund_order first, then answers with text. */
let calls = 0;
const model = createServer((req, res) => {
  req.resume();
  calls += 1;
  const content =
    calls === 1
      ? [{ type: "tool_use", id: "toolu_1", name: "refund_order", input: { order_id: "A100" } }]
      : [{ type: "text", text: "Your refund of order A100 is done." }];
  res.setHeader("content-type", "application/json");
  res.end(
    JSON.stringify({
      id: `msg_${calls}`,
      type: "message",
      role: "assistant",
      model: "fake",
      content,
      stop_reason: calls === 1 ? "tool_use" : "end_turn",
      stop_sequence: null,
      usage: { input_tokens: 10, output_tokens: 5 },
    }),
  );
});

let agent: ChildProcess;
let logs = "";
let base = "";

before(async () => {
  await new Promise<void>((done) => model.listen(0, "127.0.0.1", done));
  agent = spawn("npx", ["tsx", fileURLToPath(new URL("../src/agent.ts", import.meta.url))], {
    env: {
      ...process.env,
      PORT: "0",
      ANTHROPIC_API_KEY: "test-key",
      ANTHROPIC_BASE_URL: `http://127.0.0.1:${(model.address() as AddressInfo).port}`,
      RATEL_OTLP_ENDPOINT: "",
    },
  });
  base = await new Promise<string>((resolve, reject) => {
    agent.stdout?.on("data", (chunk) => {
      logs += chunk;
      const port = /listens on port (\d+)/.exec(logs)?.[1];
      if (port) resolve(`http://127.0.0.1:${port}`);
    });
    agent.stderr?.on("data", (chunk) => {
      logs += chunk;
    });
    agent.on("exit", (code) => reject(new Error(`the agent exited with ${code}: ${logs}`)));
  });
});

after(() => {
  agent.kill();
  model.closeAllConnections();
  model.close();
});

test("refund_order in mode simulated answers from its fake and the real refund never runs", async () => {
  const res = await fetch(`${base}/ratel/run`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      v: 1,
      runId: "run_test",
      input: { message: "My lamp of order A100 broke. Refund it." },
      params: { model: "claude-sonnet-5-5", tone: "dry" },
      tools: { lookup_order: { mode: "live" }, refund_order: { mode: "simulated" } },
      fixtures: [],
    }),
  });
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.output, "Your refund of order A100 is done.");
  assert.deepEqual(body.toolCalls, [
    { name: "refund_order", args: { order_id: "A100" }, result: { order_id: "A100", status: "refunded" }, mode: "simulated" },
  ]);
  assert.deepEqual(body.usage, { inputTokens: 20, outputTokens: 10 });
  assert.doesNotMatch(logs, /REAL REFUND/);
});

test("GET /ratel/health answers 200", async () => {
  assert.equal((await fetch(`${base}/ratel/health`)).status, 200);
});
