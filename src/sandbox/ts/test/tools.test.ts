import { describe, expect, it } from "vitest";
import type { ToolCall } from "../src/contract.js";
import { createRedactor, redactLines } from "../src/redact.js";
import { normalize, policyTools } from "../src/tools.js";
import { typeScriptFlags } from "../src/typescript.js";

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

  it("a change to a fixture result does not change the next answer", async () => {
    const tools = policyTools(
      { send: { sideEffect: true, execute: () => [], fake: () => [] } },
      [{ tool: "send", arguments: {}, result: [1] }],
      () => {},
    );
    ((await tools.send?.()) as number[]).push(2);
    expect(await tools.send?.()).toEqual([1]);
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

describe("typeScriptFlags", () => {
  it("transforms types where Node can, and gives Node no flag that it does not know", () => {
    const flag = "--experimental-transform-types";
    expect(typeScriptFlags(new Set([flag]))).toContain(flag);
    expect(typeScriptFlags(new Set())).not.toContain(flag);
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

describe("redactLines", () => {
  it("takes a long output with no newline in linear time, and redacts a secret in it", () => {
    const secret = "secret-value-1234";
    const written: string[] = [];
    const lines = redactLines(createRedactor([secret]), (line) => written.push(line));
    const chunk = "x".repeat(16 * 1024);
    const started = performance.now();
    for (let i = 0; i < 2_560; i++) lines.push(chunk);
    lines.push(secret.slice(0, 5));
    lines.push(secret.slice(5));
    lines.end();
    // 40 MB: the old code scanned all of it again for each chunk, which took many seconds.
    expect(performance.now() - started).toBeLessThan(2_000);
    expect(written.join("").endsWith("x[redacted]")).toBe(true);
  });
});
