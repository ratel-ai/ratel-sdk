/**
 * Guards the packaging contract of the Platform subpaths of `@ratel-ai/sdk`.
 *
 * `@ratel-ai/sdk/platform`, `@ratel-ai/sdk/otel` and `@ratel-ai/sdk/platform/testing` are pure
 * HTTP and OpenTelemetry code. An import of one of them must not load the native addon:
 * a Platform-only consumer (or a platform with no prebuilt binary) must still load them.
 * One convenient value import of the SDK root breaks that, so it is asserted here.
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

const PKG = JSON.parse(
  readFileSync(resolve(import.meta.dirname, "../../package.json"), "utf8"),
) as { exports: Record<string, unknown> };

const NATIVE_ADDON = "../../native/index.cjs";

afterEach(() => {
  vi.doUnmock(NATIVE_ADDON);
  vi.resetModules();
});

describe("the Platform subpaths", () => {
  it.each([
    ["./platform", "platform/index"],
    ["./otel", "platform/otel/index"],
    ["./platform/testing", "platform/testing/index"],
  ])("exports %s from package.json", (subpath, dist) => {
    expect(PKG.exports[subpath]).toEqual({
      types: `./dist/${dist}.d.ts`,
      default: `./dist/${dist}.js`,
    });
  });

  it.each([
    ["./platform", "./index.js"],
    ["./otel", "./otel/index.js"],
    ["./platform/testing", "./testing/index.js"],
  ])("%s loads without the native addon", async (_subpath, entry) => {
    vi.resetModules();
    vi.doMock(NATIVE_ADDON, () => {
      throw new Error("the native addon was loaded");
    });

    await expect(import(entry)).resolves.toBeDefined();
  });

  it("detects a load of the native addon", async () => {
    vi.resetModules();
    vi.doMock(NATIVE_ADDON, () => {
      throw new Error("the native addon was loaded");
    });

    await expect(import("../index.js")).rejects.toThrow();
  });
});
