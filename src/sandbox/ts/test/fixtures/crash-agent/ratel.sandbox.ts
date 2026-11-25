import { defineSandbox } from "@ratel-ai/sandbox";

export default defineSandbox({
  name: "crash-agent",
  entry: "agent.ts",
  secrets: ["MODEL_KEY"],
  tools: { ping: { sideEffect: false, execute: () => "pong" } },
});
