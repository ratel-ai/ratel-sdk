import { writeFileSync } from "node:fs";
import { defineSandbox } from "@ratel-ai/sandbox";

const marker = process.env.MARKER_FILE ?? "";

export default defineSandbox({
  name: "support-agent",
  entry: "src/agent.ts",
  secrets: ["MODEL_KEY"],
  tools: {
    lookup_order: { sideEffect: false, execute: ({ id }: { id: string }) => ({ id, total: 12 }) },
    refund_order: {
      sideEffect: true,
      execute: ({ id }: { id: string }) => {
        writeFileSync(marker, `refunded ${id}`);
        return { refunded: true, real: true };
      },
      fake: ({ id }: { id: string }) => ({ refunded: true, fakeFor: id }),
    },
  },
});
