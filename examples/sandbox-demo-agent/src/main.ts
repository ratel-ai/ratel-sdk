import { answer } from "./support-agent.js";
import { flush, inSpan, parentOf } from "./telemetry.js";
import { lookupOrder, refundOrder } from "./tools.js";

/** A run on this machine, with the real tools. */
const message = process.argv[2] ?? "My desk lamp of order A100 never worked. Can I have a refund?";
const attributes = {
  "gen_ai.operation.name": "invoke_agent",
  "gen_ai.agent.name": "support-demo",
  "gen_ai.input.messages": text("user", message),
};

try {
  const reply = await inSpan("invoke_agent support-demo", attributes, parentOf(undefined), async (span, parent) => {
    const tools = { lookup_order: lookupOrder, refund_order: refundOrder };
    const output = await answer(message, { tools, parent });
    span.setAttribute("gen_ai.output.messages", text("assistant", output));
    console.log(`trace ${span.spanContext().traceId}`);
    return output;
  });
  console.log(reply);
} finally {
  // A lost trace must not lose the answer.
  await flush().catch(() => {});
}

function text(role: string, content: string): string {
  return JSON.stringify([{ role, parts: [{ type: "text", content }] }]);
}
