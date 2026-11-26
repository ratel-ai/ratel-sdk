/**
 * `@ratel-ai/sdk/serve`: make the Docker image of an agent answer the Ratel run contract
 * (`protocol/sandbox/v1`). Node built-ins only: it never loads the native addon.
 *
 * ```ts
 * import { serve, tool } from "@ratel-ai/sdk/serve";
 *
 * const refundOrder = tool("refund_order", { execute: refund, fake: async ({ id }) => ({ refunded: id }) });
 * serve(async ({ input, params }) => agent.reply(input, { model: params.model, tools: { refundOrder } }));
 * ```
 *
 * @packageDocumentation
 */

export type {
  ContractError,
  ErrorBody,
  RunRequest,
  RunResponse,
  ToolCallRecord,
  ToolFixture,
  ToolMode,
  ToolSetting,
  Usage,
} from "./contract.js";
export { validateRunRequest } from "./contract.js";
export { currentRun, type RunInfo, recordUsage } from "./scope.js";
export {
  createRunHandler,
  type RunArgs,
  type RunFn,
  type ServeHandle,
  type ServeOptions,
  serve,
} from "./serve.js";
export { ToolCallError, type ToolHandlers, tool } from "./tool.js";
