import Anthropic from "@anthropic-ai/sdk";
import type { Context } from "@opentelemetry/api";
import {
  EXECUTE_TOOL,
  GEN_AI_OPERATION_NAME,
  GEN_AI_TOOL_CALL_ARGUMENTS,
  GEN_AI_TOOL_CALL_ID,
  GEN_AI_TOOL_CALL_RESULT,
  GEN_AI_TOOL_NAME,
} from "@ratel-ai/telemetry";
import { SYSTEM_PROMPT } from "./prompt.js";
import { inSpan } from "./telemetry.js";

export type Tools = Record<string, (args: never) => unknown>;
export type Usage = { inputTokens: number; outputTokens: number };
export type AnswerOptions = {
  tools: Tools;
  parent: Context;
  model?: string;
  onUsage?: (usage: Usage) => void;
};

const MODEL = "claude-opus-5-5";
const MAX_STEPS = 8;
const ORDER_ID = {
  type: "object" as const,
  properties: { order_id: { type: "string" } },
  required: ["order_id"],
  additionalProperties: false,
};
const TOOLS: Anthropic.Tool[] = [
  { name: "lookup_order", description: "Reads one order.", strict: true, input_schema: ORDER_ID },
  { name: "refund_order", description: "Refunds one order.", strict: true, input_schema: ORDER_ID },
];

export async function answer(
  message: string,
  { tools, parent, model = MODEL, onUsage }: AnswerOptions,
): Promise<string> {
  const client = new Anthropic();
  const messages: Anthropic.MessageParam[] = [{ role: "user", content: message }];
  for (let step = 0; step < MAX_STEPS; step++) {
    const response = await inSpan(
      `chat ${model}`,
      {
        [GEN_AI_OPERATION_NAME]: "chat",
        "gen_ai.provider.name": "anthropic",
        "gen_ai.request.model": model,
      },
      parent,
      async (span) => {
        const result = await client.messages.create({
          model,
          max_tokens: 16000,
          system: SYSTEM_PROMPT,
          output_config: { effort: "low" },
          tools: TOOLS,
          messages,
        });
        span.setAttributes({
          "gen_ai.response.model": result.model,
          "gen_ai.usage.input_tokens": result.usage.input_tokens,
          "gen_ai.usage.output_tokens": result.usage.output_tokens,
        });
        return result;
      },
    );
    onUsage?.({
      inputTokens: response.usage.input_tokens,
      outputTokens: response.usage.output_tokens,
    });
    if (response.stop_reason !== "tool_use") {
      if (response.stop_reason !== "end_turn") throw new Error(`The model stopped: ${response.stop_reason}`);
      return response.content.flatMap((block) => (block.type === "text" ? [block.text] : [])).join("\n");
    }
    messages.push({ role: "assistant", content: response.content });
    const calls = response.content.filter((block) => block.type === "tool_use");
    const results = [];
    for (const call of calls) results.push(await runTool(call, tools, parent));
    messages.push({ role: "user", content: results });
  }
  throw new Error(`The agent needs more than ${MAX_STEPS} steps.`);
}

function runTool(
  call: Anthropic.ToolUseBlock,
  tools: Tools,
  parent: Context,
): Promise<Anthropic.ToolResultBlockParam> {
  const attributes = {
    [GEN_AI_OPERATION_NAME]: EXECUTE_TOOL,
    [GEN_AI_TOOL_NAME]: call.name,
    [GEN_AI_TOOL_CALL_ID]: call.id,
    [GEN_AI_TOOL_CALL_ARGUMENTS]: JSON.stringify(call.input),
  };
  return inSpan(`${EXECUTE_TOOL} ${call.name}`, attributes, parent, async (span) => {
    const tool = tools[call.name];
    if (!tool) throw new Error(`There is no tool ${call.name}.`);
    const content = JSON.stringify(await tool(call.input as never));
    span.setAttribute(GEN_AI_TOOL_CALL_RESULT, content);
    return { type: "tool_result", tool_use_id: call.id, content };
  });
}
