import { describe, expect, it } from "vitest";
import type { ToolCall } from "../src/contract.js";
import { createRedactor } from "../src/redact.js";
import { normalize, policyTools } from "../src/tools.js";

describe("policyTools", () => {
  it("equal fixtures answer in order and the last one repeats", async () => {
    const tools = policyTools(
      { send: { sideEffect: true, execute: () => "real", fake: () => "fake" } },
      [
        { tool: "send", arguments: { a: 1, b: { c: 2, d: 3 } }, result: 1 },
        { tool: "send", arguments: { b: { d: 3, c: 2 }, a: 1 }, result: 2 },
      ],
      () => {},
    );
    const send = () => tools.send?.({ b: { c: 2, d: 3 }, a: 1 });
    expect([await send(), await send(), await send()]).toEqual([1, 2, 2]);
    expect(await tools.send?.({ a: 2 })).toBe("fake");
  });

  it("a fixture does not replace a read-only tool", async () => {
    const tools = policyTools(
      { get: { sideEffect: false, execute: () => "real" } },
      [{ tool: "get", arguments: {}, result: "fixture" }],
      () => {},
    );
    expect(await tools.get?.()).toBe("real");
  });

  it("a tool that does not say sideEffect: false never runs for real", async () => {
    const calls: ToolCall[] = [];
    const unsafe = { execute: () => "real" } as unknown as Parameters<
      typeof policyTools
    >[0][string];
    const tools = policyTools({ send: unsafe }, [], (call) => calls.push(call));
    await expect(tools.send?.()).rejects.toThrow(/no fake handler/);
    expect(calls[0]).toMatchObject({ source: "fake", error: "tool send has no fake handler" });
  });

  it("normalizes the order of keys, not of list items", () => {
    expect(normalize({ b: 1, a: [2, 1] })).toBe(normalize({ a: [2, 1], b: 1 }));
    expect(normalize([1, 2])).not.toBe(normalize([2, 1]));
  });
});

describe("createRedactor", () => {
  it("replaces a secret in each form", () => {
    const secret = 'p"a/ss w0rd';
    const { text } = createRedactor([secret]);
    const base64 = Buffer.from(secret).toString("base64");
    for (const form of [secret, JSON.stringify(secret), encodeURIComponent(secret), base64]) {
      expect(text(`<${form}>`)).toContain("[redacted]");
      expect(text(`<${form}>`)).not.toContain("w0rd");
    }
    expect(createRedactor(["123456"]).json({ pin: 123456 })).toEqual({ pin: "[redacted]" });
  });
});
