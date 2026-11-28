import { recordUsage, serve, tool } from "@ratel-ai/sdk/serve";
import { answer } from "./support-agent.js";
import { flush, parentOf } from "./telemetry.js";
import { lookupOrder, refundOrder } from "./tools.js";

type OrderArgs = { order_id: string };

/**
 * The tools with their modes. ratel.yaml sets the mode of each run: `refund_order` is simulated,
 * so a run on Ratel answers from `fake` and the real refund never runs.
 */
const tools = {
  lookup_order: tool<OrderArgs, unknown>("lookup_order", { execute: lookupOrder, fake: lookupOrder }),
  refund_order: tool<OrderArgs, unknown>("refund_order", {
    execute: refundOrder,
    fake: ({ order_id }) => ({ order_id, status: "refunded" }),
  }),
};

/** The entry point of the image. The input is `{ "message": "..." }`. */
serve(async ({ input, params, traceparent }) => {
  const { message } = (input ?? {}) as { message?: unknown };
  if (typeof message !== "string") throw new Error('The input must be { "message": "..." }.');
  try {
    return await answer(message, {
      tools,
      parent: parentOf(traceparent),
      onUsage: recordUsage,
      ...(typeof params.model === "string" ? { model: params.model } : {}),
      ...(typeof params.tone === "string" ? { tone: params.tone } : {}),
    });
  } finally {
    // A lost trace must not lose the answer.
    await flush().catch(() => {});
  }
}).then(({ port }) => console.log(`support-demo listens on port ${port}`));
