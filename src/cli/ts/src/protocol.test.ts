import { readdirSync, readFileSync } from "node:fs";
import { join, resolve } from "node:path";
import { describe, expect, it } from "vitest";

const COPY = resolve(import.meta.dirname, "../protocol");
const SOURCE = resolve(import.meta.dirname, "../../../../protocol/sandbox/v1");

describe("the copy of protocol/sandbox/v1", () => {
  it("has the same verify.mjs as the protocol (run pnpm sync-protocol)", () => {
    expect(readFileSync(join(COPY, "verify.mjs"), "utf8")).toBe(
      readFileSync(join(SOURCE, "verify.mjs"), "utf8"),
    );
  });

  it("has the same schemas as the protocol (run pnpm sync-protocol)", () => {
    const names = readdirSync(join(SOURCE, "schema")).sort();
    expect(readdirSync(join(COPY, "schema")).sort()).toEqual(names);
    for (const name of names) {
      expect(readFileSync(join(COPY, "schema", name), "utf8"), name).toBe(
        readFileSync(join(SOURCE, "schema", name), "utf8"),
      );
    }
  });
});
