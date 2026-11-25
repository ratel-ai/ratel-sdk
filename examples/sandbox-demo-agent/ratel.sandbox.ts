import { defineSandbox } from "@ratel-ai/sandbox";
import { lookupOrder, refundOrder } from "./src/tools.js";

export default defineSandbox({
  name: "support-demo",
  entry: "src/agent.ts",
  // The image needs no dev dependency, and the workspace link of one does not resolve outside this
  // repository.
  install: ["npm pkg delete devDependencies", "npm install --no-audit --no-fund"],
  secrets: ["ANTHROPIC_API_KEY"],
  tools: {
    lookup_order: { sideEffect: false, execute: lookupOrder },
    refund_order: {
      sideEffect: true,
      execute: refundOrder,
      fake: ({ order_id }: { order_id: string }) => ({ order_id, status: "refunded" }),
    },
  },
});
