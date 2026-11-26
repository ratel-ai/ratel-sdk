/**
 * Catalog snapshot hashes: reproduce every vector in the shared
 * `protocol/v1/conformance/snapshot-hash-vectors.json`. The Python SDK reads the
 * same file, so a failure here means the two publishers disagree with Cloud.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import type { RuntimeCatalogSnapshot, RuntimeCatalogToolDefinition } from "../types.js";
import { hashCatalogSnapshot } from "./hash.js";

interface WireTool {
  readonly id: string;
  readonly name: string;
  readonly description?: string;
  readonly searchable_description?: string;
  readonly input_schema?: Record<string, unknown> | null;
  readonly output_schema?: Record<string, unknown> | null;
  readonly metadata?: Record<string, unknown> | null;
}

interface HashVector {
  readonly name: string;
  readonly snapshot: { readonly source_id: string; readonly tools: readonly WireTool[] };
  readonly hash: string;
}

interface HashVectors {
  readonly vectors: readonly HashVector[];
  readonly equalHashes: readonly (readonly string[])[];
  readonly distinctHashes: readonly (readonly string[])[];
}

const vectors: HashVectors = JSON.parse(
  readFileSync(
    new URL(
      "../../../../../../protocol/v1/conformance/snapshot-hash-vectors.json",
      import.meta.url,
    ),
    "utf8",
  ),
);

describe("hashCatalogSnapshot", () => {
  for (const vector of vectors.vectors) {
    it(`reproduces the shared vector ${vector.name}`, () => {
      expect(hashCatalogSnapshot(toSnapshot(vector))).toBe(vector.hash);
    });
  }

  for (const group of vectors.equalHashes) {
    it(`gives one hash to ${group.join(" = ")}`, () => {
      expect(new Set(group.map((name) => hashCatalogSnapshot(toSnapshot(byName(name))))).size).toBe(
        1,
      );
    });
  }

  for (const group of vectors.distinctHashes) {
    it(`gives distinct hashes to ${group.join(", ")}`, () => {
      const hashes = group.map((name) => hashCatalogSnapshot(toSnapshot(byName(name))));
      expect(new Set(hashes).size).toBe(group.length);
    });
  }

  it("preserves JSON omission and array-null semantics for undefined values", () => {
    expect(
      hashCatalogSnapshot({
        source_id: "worker-exotic",
        tools: [
          {
            id: "undefined",
            name: "undefined",
            inputSchema: {
              properties: { kept: { type: "string" }, omitted: undefined },
              required: ["kept", undefined],
            },
          },
        ],
      }),
    ).toBe(byName("null-in-array").hash);
  });

  it("preserves JSON coercion semantics for exotic schema values", () => {
    expect(
      hashCatalogSnapshot({
        source_id: "worker-exotic",
        tools: [
          {
            id: "exotic",
            name: "exotic",
            inputSchema: {
              createdAt: new Date("2026-08-14T00:00:00.000Z"),
              nan: Number.NaN,
              infinity: Number.POSITIVE_INFINITY,
              pattern: /ratel/,
            },
          },
        ],
      }),
    ).toBe(byName("json-coerced-values").hash);
  });
});

function byName(name: string): HashVector {
  const vector = vectors.vectors.find((candidate) => candidate.name === name);
  if (vector === undefined) throw new Error(`no snapshot hash vector named ${name}`);
  return vector;
}

/** Map the wire field names of a vector to the TypeScript snapshot input. */
function toSnapshot(vector: HashVector): RuntimeCatalogSnapshot {
  return {
    source_id: vector.snapshot.source_id,
    tools: vector.snapshot.tools.map(
      (tool): RuntimeCatalogToolDefinition => ({
        id: tool.id,
        name: tool.name,
        ...(tool.description === undefined ? {} : { description: tool.description }),
        ...(tool.searchable_description === undefined
          ? {}
          : { experimentalSearchableDescription: tool.searchable_description }),
        ...(tool.input_schema === undefined ? {} : { inputSchema: tool.input_schema }),
        ...(tool.output_schema === undefined ? {} : { outputSchema: tool.output_schema }),
        ...(tool.metadata === undefined ? {} : { metadata: tool.metadata }),
      }),
    ),
  };
}
