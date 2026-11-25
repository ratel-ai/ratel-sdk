import { spawn } from "node:child_process";
import type { RunContext } from "@ratel-ai/sandbox";

export default async function run({ input, tools, reportUsage }: RunContext) {
  await tools.ping();
  await new Promise((done) => setTimeout(done, 50));
  if (input === "throw") throw new TypeError(`bad key ${process.env.MODEL_KEY}`);
  if (input === "exit") process.exit(3);
  if (input === "kill") process.kill(process.pid, "SIGKILL");
  if (input === "uncaught") {
    setTimeout(() => {
      throw new Error(`uncaught ${process.env.MODEL_KEY}`);
    }, 1);
    await new Promise(() => setInterval(() => {}, 1000));
  }
  if (input === "large") return "x".repeat(5 * 1024 * 1024);
  // Longer than the two seconds that the test allows for the answer.
  if (input === "open") setTimeout(() => {}, 4_000);
  if (input === "linger") setInterval(() => {}, 1000);
  if (input === "odd-error") throw Object.assign(new Error(), { message: { code: 1 } });
  if (input === "deep" || input === "deep-tool") {
    let deep: unknown = 1;
    for (let level = 0; level < 2_500; level += 1) deep = { deep };
    if (input === "deep") return deep;
    await tools.ping(deep);
  }
  if (input === "odd-messages") {
    for (const message of [
      { type: "call", call: { name: 7 } },
      { type: "error", error: {} },
    ])
      process.send?.(message);
    await new Promise((done) => setTimeout(done, 300));
  }
  if (input === "usage") {
    reportUsage({ inputTokens: 5 });
    reportUsage({ inputTokens: -9, outputTokens: -1 });
  }
  if (input === "print") console.log(`${process.env.MODEL_KEY}\nand ${process.env.MODEL_KEY}`);
  if (input === "leave") {
    spawn("sleep", ["25"], { stdio: "inherit", detached: true }).unref();
    spawn("sleep", ["25"], { stdio: "inherit" }).unref();
    return "left";
  }
  if (input === "message") {
    for (const message of ["ready", null, { type: "ready" }, { type: "call" }, { type: "error" }])
      process.send?.(message);
    await new Promise((done) => setTimeout(done, 300));
    return "after";
  }
  return "ok";
}
