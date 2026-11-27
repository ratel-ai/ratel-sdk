import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { parseInput, parseParamFlags, parseParams, parseTools, UsageError } from "./args.js";
import type { RatelConfig } from "./config.js";

const config: RatelConfig = {
  name: "a",
  input: { example: { message: "hi" } },
  params: {
    model: { type: "enum", options: ["sonnet", "haiku"], default: "sonnet" },
    tone: { type: "string", default: "friendly" },
    maxTurns: { type: "number" },
    verbose: { type: "boolean", default: false },
  },
  tools: { lookup_order: { mode: "live" }, refund_order: {} },
};

describe("parseParams", () => {
  it("starts from the defaults and applies each --param with its type", () => {
    expect(parseParams(config, ["model=haiku", "maxTurns=3", "verbose=true"])).toEqual({
      model: "haiku",
      tone: "friendly",
      maxTurns: 3,
      verbose: true,
    });
  });

  it("refuses a parameter that ratel.yaml does not declare", () => {
    expect(() => parseParams(config, ["temperature=1"])).toThrow(
      new UsageError(
        "--param temperature: ratel.yaml declares no such parameter (model, tone, maxTurns, verbose)",
      ),
    );
  });

  it("refuses a value of the wrong type and an option that is not declared", () => {
    expect(() => parseParams(config, ["maxTurns=many"])).toThrow(
      "--param maxTurns: must be a number",
    );
    expect(() => parseParams(config, ["verbose=yes"])).toThrow(
      "--param verbose: must be true or false",
    );
    expect(() => parseParams(config, ["model=gpt"])).toThrow(
      "--param model: must be one of sonnet, haiku",
    );
  });

  it("refuses a flag without =", () => {
    expect(() => parseParams(config, ["model"])).toThrow("--param model: write name=value");
  });
});

describe("parseParamFlags", () => {
  it("a value with commas is a list of typed values in matrix, and leaves params", () => {
    expect(
      parseParamFlags(config, [
        "model=sonnet,haiku",
        "maxTurns=1,2",
        "verbose=true,false",
        "tone=dry",
      ]),
    ).toEqual({
      params: { tone: "dry" },
      matrix: { model: ["sonnet", "haiku"], maxTurns: [1, 2], verbose: [true, false] },
    });
  });

  it("without commas, it is parseParams", () => {
    expect(parseParamFlags(config, ["model=haiku"])).toEqual({
      params: parseParams(config, ["model=haiku"]),
      matrix: {},
    });
  });

  it("refuses an item of the wrong type, and a parameter that is not declared", () => {
    expect(() => parseParamFlags(config, ["maxTurns=1,many"])).toThrow(
      "--param maxTurns: must be a number",
    );
    expect(() => parseParamFlags(config, ["temperature=1,2"])).toThrow(
      "--param temperature: ratel.yaml declares no such parameter",
    );
  });
});

describe("parseInput", () => {
  it("defaults to input.example", () => {
    expect(parseInput(config, undefined)).toEqual({ message: "hi" });
  });

  it("reads JSON, else takes the text", () => {
    expect(parseInput(config, '{"a":1}')).toEqual({ a: 1 });
    expect(parseInput(config, "hello there")).toBe("hello there");
  });

  it("reads @file", () => {
    const dir = mkdtempSync(join(tmpdir(), "ratel-input-"));
    writeFileSync(join(dir, "in.json"), '{"from":"file"}');
    expect(parseInput(config, `@${join(dir, "in.json")}`)).toEqual({ from: "file" });
    expect(() => parseInput(config, "@/nope.json")).toThrow(
      "--input @/nope.json: the file does not exist",
    );
  });
});

describe("parseTools", () => {
  it("gives each declared tool its mode, simulated by default, and applies --tool", () => {
    expect(parseTools(config, [])).toEqual({
      lookup_order: { mode: "live" },
      refund_order: { mode: "simulated" },
    });
    expect(parseTools(config, ["refund_order=live", "other=recorded"])).toEqual({
      lookup_order: { mode: "live" },
      refund_order: { mode: "live" },
      other: { mode: "recorded" },
    });
  });

  it("refuses an unknown mode", () => {
    expect(() => parseTools(config, ["refund_order=real"])).toThrow(
      "--tool refund_order: the mode must be simulated, recorded or live",
    );
  });
});
