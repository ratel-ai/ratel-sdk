/** The definition of a sandbox: the default export of `ratel.sandbox.ts`. */

export const RESERVED_SECRET_PREFIXES = ["RATEL_", "MODAL_", "AWS_"] as const;

// biome-ignore lint/suspicious/noExplicitAny: a tool keeps the argument type of the host's function
type ToolFunction = (args: any) => unknown;

/** A tool that changes nothing outside the agent. It runs for real. */
export type ReadOnlyTool = {
  sideEffect: false;
  description?: string;
  execute: ToolFunction;
  fake?: never;
};

/** A tool that changes something. In a run it answers from a fixture, else from `fake`. */
export type SideEffectTool = {
  sideEffect: true;
  description?: string;
  execute: ToolFunction;
  fake: ToolFunction;
};

export type SandboxTool = ReadOnlyTool | SideEffectTool;

type NameChar =
  | "A"
  | "B"
  | "C"
  | "D"
  | "E"
  | "F"
  | "G"
  | "H"
  | "I"
  | "J"
  | "K"
  | "L"
  | "M"
  | "N"
  | "O"
  | "P"
  | "Q"
  | "R"
  | "S"
  | "T"
  | "U"
  | "V"
  | "W"
  | "X"
  | "Y"
  | "Z"
  | "_"
  | "0"
  | "1"
  | "2"
  | "3"
  | "4"
  | "5"
  | "6"
  | "7"
  | "8"
  | "9";
type AllNameChars<S extends string> = S extends ""
  ? true
  : S extends `${NameChar}${infer Rest}`
    ? AllNameChars<Rest>
    : false;

/** The name of an environment variable (`OPENAI_API_KEY`). A value does not have this type. */
export type SecretName<S extends string> =
  S extends `${(typeof RESERVED_SECRET_PREFIXES)[number]}${string}`
    ? `The prefix of ${S} is reserved`
    : AllNameChars<S> extends true
      ? S
      : "A secret is a name such as OPENAI_API_KEY, not a value";

export type SandboxDefinition<Secrets extends readonly string[] = readonly string[]> = {
  /** Letters a to z, digits and `-`. */
  name: string;
  /** The module with the agent, relative to this file's folder. Its default export is the run function. */
  entry: string;
  /** The base image. `node:24-slim` by default. It must have Node 22.18 or later. */
  base?: string;
  /** Shell commands that run in the project folder when the image is built. */
  install?: readonly string[];
  /** The names of the secrets that the agent reads from its environment. Never values. */
  secrets?: Secrets;
  tools?: Readonly<Record<string, SandboxTool>>;
};

/** What the run function gets. */
export type RunContext = {
  input: unknown;
  params: Record<string, unknown>;
  /** The declared tools, with the tool policy applied. Call tools only through it. */
  // biome-ignore lint/suspicious/noExplicitAny: the host knows the types of its own tools
  tools: Record<string, (args?: any) => Promise<any>>;
  /** The W3C trace context of the run, for the agent's own spans. */
  traceparent: string;
  /** Adds to the usage in the run result. */
  reportUsage(usage: { inputTokens?: number; outputTokens?: number; costUsd?: number }): void;
};

/** The default export of the entry point. Its result is the output of the run. */
export type RunFunction = (context: RunContext) => unknown;

export function defineSandbox<const Secrets extends readonly string[] = readonly []>(
  definition: SandboxDefinition<Secrets> & {
    secrets?: { readonly [K in keyof Secrets]: SecretName<Secrets[K] & string> };
  },
): SandboxDefinition {
  return definition;
}
