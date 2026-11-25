import type { RunContext } from "@ratel-ai/sandbox";

export default async function run({ input, tools }: RunContext) {
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
  if (input === "linger") setInterval(() => {}, 1000);
  return "ok";
}
