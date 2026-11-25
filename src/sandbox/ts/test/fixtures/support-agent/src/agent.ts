import type { RunContext } from "@ratel-ai/sandbox";
import { shout } from "./shout.js";

enum Kind {
  Order = "order",
}

export default async function run({ input, params, tools, reportUsage, traceparent }: RunContext) {
  const order = await tools.lookup_order({ id: "7" });
  const pinned = await tools.refund_order({ reason: "late", id: "7" });
  const faked = await tools.refund_order({ id: "8" });
  reportUsage({ inputTokens: 5, outputTokens: 2 });
  reportUsage({ inputTokens: 1 });
  console.log(`key is ${process.env.MODEL_KEY}`);
  console.error(`err ${process.env.MODEL_KEY}`);
  process.stdout.write("split ");
  process.stdout.write((process.env.MODEL_KEY ?? "").slice(0, 5));
  process.stdout.write(`${(process.env.MODEL_KEY ?? "").slice(5)}\n`);
  return {
    kind: Kind.Order,
    text: shout((input as { message: string }).message),
    params,
    order,
    pinned,
    faked,
    leak: process.env.MODEL_KEY,
    [process.env.MODEL_KEY ?? "k"]: 1,
    runInput: process.env.RATEL_RUN_INPUT ?? null,
    runToken: process.env.RATEL_RUN_TOKEN ?? null,
    endpoint: process.env.RATEL_OTLP_ENDPOINT,
    joined: traceparent === process.env.TRACEPARENT,
  };
}
