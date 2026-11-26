/**
 * The types of the sandbox/v1 run contract (`protocol/sandbox/v1`) and a validator of the run
 * request. The JSON schemas in `protocol/sandbox/v1/schema` are normative. A test checks this
 * validator against the conformance vectors of the protocol.
 */

/** The mode of a tool for one run. */
export type ToolMode = "simulated" | "recorded" | "live";

/** The setting of one tool for a run. */
export interface ToolSetting {
  /** The mode of the tool: `simulated`, `recorded` or `live`. */
  mode: ToolMode;
}

/** A recorded result of a tool. A tool in mode `recorded` answers from it. */
export interface ToolFixture {
  /** The name of the tool. */
  tool: string;
  /** The arguments of the call. They match after normalization (sorted keys). */
  args: Record<string, unknown>;
  /** Any JSON: the answer of the tool. */
  result: unknown;
}

/** The body of `POST /ratel/run`, or the stdin of a command-mode container. */
export interface RunRequest {
  /** The major version of the contract. */
  v: 1;
  /** The id of this run. */
  runId: string;
  /** Any JSON. Conversation history belongs here, never in `params`. */
  input: unknown;
  /** A flat object of the parameters that `ratel.yaml` declares. */
  params: Record<string, string | number | boolean>;
  /** The mode of each tool for this run. */
  tools: Record<string, ToolSetting>;
  /** The recorded results for the tools in mode `recorded`. */
  fixtures: ToolFixture[];
  /** A W3C trace context. The run continues this trace. */
  traceparent?: string;
}

/** The body of each non-200 answer, and the error of a failed tool call. */
export interface ErrorBody {
  /** A stable code, e.g. `invalid_request`, `run_failed`, `no_fake`, `no_fixture`, `tool_failed`. */
  code: string;
  /** A sentence for a person. */
  message: string;
}

/** One tool call of a run, with the mode that answered it. */
export interface ToolCallRecord {
  /** The name of the tool. */
  name: string;
  /** The arguments of the call, as JSON. */
  args: Record<string, unknown>;
  /** The mode that answered the call. */
  mode: ToolMode;
  /** The answer, as JSON. Absent when the call failed. */
  result?: unknown;
  /** Why the call failed. Absent when it succeeded. */
  error?: ErrorBody;
}

/** The model usage of a run. */
export interface Usage {
  /** The input tokens of the model calls. */
  inputTokens?: number;
  /** The output tokens of the model calls. */
  outputTokens?: number;
  /** The cost of the model calls, in US dollars. */
  costUsd?: number;
}

/** The body of a 200 answer to `POST /ratel/run`. */
export interface RunResponse {
  /** Any JSON: the output of the agent. */
  output: unknown;
  /** Each tool call of the run, in order. */
  toolCalls?: ToolCallRecord[];
  /** The model usage of the run. */
  usage?: Usage;
}

/** A violation of the contract, at a JSON pointer. */
export interface ContractError {
  /** The JSON pointer of the wrong field. `""` is the whole document. */
  path: string;
  /** What is wrong. */
  message: string;
}

/** The three tool modes. */
export const TOOL_MODES: readonly ToolMode[] = ["simulated", "recorded", "live"];
/** The pattern of an error code. */
export const ERROR_CODE = /^[a-z][a-z0-9_]{0,63}$/;

const REQUIRED = ["v", "runId", "input", "params", "tools", "fixtures"] as const;
const PARAM_NAME = /^[A-Za-z_][A-Za-z0-9_]{0,63}$/;
const TOOL_NAME = /^[A-Za-z0-9_.-]{1,128}$/;
const TRACEPARENT = /^[0-9a-f]{2}-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$/;

/**
 * The violations of `run-request.schema.json` in a document, in the order of the reference
 * verifier, so the first error has the same path. Empty: the document is a valid request.
 */
export function validateRunRequest(doc: unknown): ContractError[] {
  if (!isObject(doc)) return [{ path: "", message: "must be object" }];
  const errors: ContractError[] = [];
  const fail = (path: string, message: string) => errors.push({ path, message });
  for (const name of REQUIRED) if (!(name in doc)) fail(`/${name}`, "is required");

  if ("v" in doc && doc.v !== 1) fail("/v", "must be 1");
  if ("runId" in doc && (typeof doc.runId !== "string" || doc.runId.length === 0)) {
    fail("/runId", "must be a non-empty string");
  }
  if ("params" in doc) {
    if (!isObject(doc.params)) fail("/params", "must be object");
    else {
      for (const [name, value] of Object.entries(doc.params)) {
        if (!PARAM_NAME.test(name)) fail(`/params/${esc(name)}`, "is not a valid name");
        else if (!["string", "number", "boolean"].includes(typeof value)) {
          fail(`/params/${esc(name)}`, "must be string or number or boolean");
        }
      }
    }
  }
  if ("tools" in doc) {
    if (!isObject(doc.tools)) fail("/tools", "must be object");
    else {
      for (const [name, value] of Object.entries(doc.tools)) {
        const path = `/tools/${esc(name)}`;
        if (!TOOL_NAME.test(name)) fail(path, "is not a valid name");
        else if (!isObject(value)) fail(path, "must be object");
        else if (!("mode" in value)) fail(`${path}/mode`, "is required");
        else if (!TOOL_MODES.includes(value.mode as ToolMode)) {
          fail(`${path}/mode`, `must be one of ${TOOL_MODES.join(", ")}`);
        }
      }
    }
  }
  if ("fixtures" in doc) {
    if (!Array.isArray(doc.fixtures)) fail("/fixtures", "must be array");
    else {
      for (const [i, fixture] of doc.fixtures.entries()) {
        const path = `/fixtures/${i}`;
        if (!isObject(fixture)) {
          fail(path, "must be object");
          continue;
        }
        for (const name of ["tool", "args", "result"]) {
          if (!(name in fixture)) fail(`${path}/${name}`, "is required");
        }
        if (
          "tool" in fixture &&
          !(typeof fixture.tool === "string" && TOOL_NAME.test(fixture.tool))
        ) {
          fail(`${path}/tool`, "is not a valid tool name");
        }
        if ("args" in fixture && !isObject(fixture.args)) fail(`${path}/args`, "must be object");
      }
    }
  }
  if (
    "traceparent" in doc &&
    !(typeof doc.traceparent === "string" && TRACEPARENT.test(doc.traceparent))
  ) {
    fail("/traceparent", "must be a W3C traceparent");
  }
  return errors;
}

/** @internal */
export function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function esc(part: string): string {
  return part.replaceAll("~", "~0").replaceAll("/", "~1");
}
