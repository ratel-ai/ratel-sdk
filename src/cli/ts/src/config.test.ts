import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { ConfigError, loadConfig } from "./config.js";

function file(text: string): string {
  const dir = mkdtempSync(join(tmpdir(), "ratel-config-"));
  const path = join(dir, "ratel.yaml");
  writeFileSync(path, text);
  return path;
}

describe("loadConfig", () => {
  it("parses a valid ratel.yaml and gives its folder", async () => {
    const path = file(
      "name: support-agent\ninput:\n  example: { message: hi }\nsecrets: [ANTHROPIC_API_KEY]\n",
    );
    const loaded = await loadConfig(path);
    expect(loaded.config).toEqual({
      name: "support-agent",
      input: { example: { message: "hi" } },
      secrets: ["ANTHROPIC_API_KEY"],
    });
    expect(loaded.dir).toBe(join(path, ".."));
  });

  it("refuses an invalid field with its path and its line", async () => {
    const path = file("name: a\ninput:\n  example: 1\nparams:\n  model:\n    type: enum\n");
    const error = await loadConfig(path).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ConfigError);
    expect((error as ConfigError).path).toBe("/params/model/options");
    expect((error as Error).message).toBe(`${path}:5:3: /params/model/options is required`);
  });

  it("refuses an unknown field at its own line", async () => {
    const path = file("name: a\ninput:\n  example: 1\nentry: src/agent.ts\n");
    const error = (await loadConfig(path).catch((e: unknown) => e)) as ConfigError;
    expect(error.message).toBe(`${path}:4:1: /entry is not a known field`);
  });

  it("refuses YAML that does not parse", async () => {
    const path = file("name: [a\n");
    const error = (await loadConfig(path).catch((e: unknown) => e)) as ConfigError;
    expect(error).toBeInstanceOf(ConfigError);
    expect(error.message).toMatch(/^.*ratel\.yaml:\d+:\d+: /);
  });

  it("refuses a missing file with a clear message", async () => {
    const error = (await loadConfig("/nope/ratel.yaml").catch((e: unknown) => e)) as ConfigError;
    expect(error.message).toBe("/nope/ratel.yaml: the file does not exist");
  });
});
