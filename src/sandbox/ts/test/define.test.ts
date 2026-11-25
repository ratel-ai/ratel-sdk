import { describe, expect, it } from "vitest";
import { defineSandbox, type SandboxDefinition, toManifest } from "../src/index.js";

const base = { name: "a", entry: "agent.ts" };
const problem = (definition: unknown): string => {
  try {
    toManifest(definition as SandboxDefinition);
  } catch (error) {
    return (error as Error).message;
  }
  return "";
};

describe("types (checked by the typecheck script)", () => {
  it("refuses what is not a definition", () => {
    // @ts-expect-error the entry point is missing
    defineSandbox({ name: "a" });
    // @ts-expect-error a secret value in place of a name
    defineSandbox({ ...base, secrets: ["sk-live-abc123"] });
    // @ts-expect-error a name with a value
    defineSandbox({ ...base, secrets: ["OPENAI_API_KEY=abc"] });
    // @ts-expect-error a reserved prefix
    defineSandbox({ ...base, secrets: ["MODAL_TOKEN_ID"] });
    // @ts-expect-error names and values in place of names
    defineSandbox({ ...base, secrets: { OPENAI_API_KEY: "abc" } });
    defineSandbox({
      ...base,
      // @ts-expect-error a tool with side effects and no fake handler
      tools: { refund: { sideEffect: true, execute: () => 1 } },
    });
    defineSandbox({ ...base, secrets: ["OPENAI_API_KEY", "DB_URL_2"] });
  });
});

describe("toManifest", () => {
  it("fills the defaults", () => {
    expect(toManifest(base)).toEqual({
      v: 1,
      name: "a",
      entry: "agent.ts",
      base: "node:24-slim",
      install: [],
      secrets: [],
      tools: [],
    });
  });

  it("refuses a tool with side effects that has no fake handler", () => {
    expect(problem({ ...base, tools: { refund: { sideEffect: true, execute: () => 1 } } })).toMatch(
      /tool refund has side effects and no fake handler/,
    );
  });

  it.each([
    "",
    "/etc/x.ts",
    "../x.ts",
    "a/../../x.ts",
    "C:\\x.ts",
  ])("refuses the entry %j", (entry) => {
    expect(problem({ ...base, entry })).toMatch(/entry/);
  });

  it("names a bad secret by its position and never shows its text", () => {
    const message = problem({ ...base, secrets: ["OK_KEY", "sk-private-1"] });
    expect(message).toMatch(/secrets\[1\] is not a name/);
    expect(message).not.toMatch(/sk-private/);
  });

  it("refuses reserved, repeated and missing parts", () => {
    expect(problem({ ...base, secrets: ["AWS_KEY"] })).toMatch(/reserved/);
    expect(problem({ ...base, secrets: ["AWS_KEY"] })).not.toMatch(/MODAL/);
    expect(problem({ ...base, secrets: ["A_KEY", "A_KEY"] })).toMatch(/two times/);
    expect(problem({ name: "Bad Name", entry: "a.ts", tools: { x: {} } })).toMatch(
      /name must be.*execute.*sideEffect/,
    );
  });
});
