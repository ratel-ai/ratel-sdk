import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { createServer } from "node:http";
import { type AddressInfo } from "node:net";
import { after, before, test } from "node:test";
import { fileURLToPath } from "node:url";

/** A model that answers one time, and a trace endpoint that refuses each trace. */
const server = createServer((req, res) => {
  req.resume();
  if (req.url?.startsWith("/v1/messages")) {
    res.setHeader("content-type", "application/json");
    res.end(
      JSON.stringify({
        id: "msg_1",
        type: "message",
        role: "assistant",
        model: "fake",
        content: [{ type: "text", text: "All good." }],
        stop_reason: "end_turn",
        stop_sequence: null,
        usage: { input_tokens: 1, output_tokens: 1 },
      }),
    );
    return;
  }
  traces += 1;
  res.statusCode = 400;
  res.end();
});
let traces = 0;

before(() => new Promise<void>((done) => server.listen(0, "127.0.0.1", done)));
after(() => {
  server.closeAllConnections();
  server.close();
});

const url = () => `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
const env = () => ({
  PATH: process.env.PATH,
  ANTHROPIC_API_KEY: "not-a-key",
  ANTHROPIC_BASE_URL: url(),
  RATEL_OTLP_ENDPOINT: `${url()}/v1/traces`,
});

function run(args: string[]): Promise<{ code: unknown; out: string; err: string }> {
  const cwd = fileURLToPath(new URL("..", import.meta.url));
  return new Promise((done) => {
    execFile(process.execPath, ["--import", "tsx", ...args], { cwd, env: env() }, (error, out, err) =>
      done({ code: error ? error.code : 0, out, err }),
    );
  });
}

test("the sandbox entry gives its answer when the traces are refused", async () => {
  const code =
    'const { default: run } = await import("./src/agent.ts");' +
    'console.log(await run({ input: { message: "hi" }, params: {}, tools: {}, reportUsage() {} }));';
  const result = await run(["--input-type=module", "-e", code]);
  assert.equal(result.code, 0, result.err);
  assert.match(result.out, /All good\./);
  assert.ok(traces > 0, "no trace was sent");
});

test("the local run exits with 0 when the traces are refused", async () => {
  const sent = traces;
  const result = await run(["src/main.ts", "hi"]);
  assert.equal(result.code, 0, result.err);
  assert.match(result.out, /All good\./);
  assert.ok(traces > sent, "no trace was sent");
});
