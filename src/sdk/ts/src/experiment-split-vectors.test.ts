/**
 * Deterministic arm assignment: reproduce every vector in the shared
 * `protocol/v1/conformance/experiment-split-vectors.json`. The Python SDK reads the
 * same file, so a failure here means the two SDKs assign the same unit to different arms.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { experimentalDefineExperiment } from "./index.js";

interface SplitVectors {
  readonly splits: Record<string, readonly { arm: string; weight: number }[]>;
  readonly vectors: readonly {
    readonly experiment_id: string;
    readonly unit_id: string;
    readonly split: string;
    readonly arm: string;
  }[];
}

const vectors: SplitVectors = JSON.parse(
  readFileSync(
    new URL("../../../../protocol/v1/conformance/experiment-split-vectors.json", import.meta.url),
    "utf8",
  ),
);

describe("experiment split vectors", () => {
  it.each(vectors.vectors)("assigns $experiment_id / $unit_id to $arm", async (vector) => {
    const split = vectors.splits[vector.split] ?? [];
    const experiment = experimentalDefineExperiment({
      id: vector.experiment_id,
      arms: Object.fromEntries(
        split.map(({ arm }) => [arm, { select: async () => [] as string[] }]),
      ),
      split,
      ranking: (ids: string[]) => ids.map((id) => ({ id })),
      evaluation: { references: ["peer-selection"] },
    });

    const selection = await experiment.select({}, { unitId: vector.unit_id });

    expect(selection.assignedArm).toBe(vector.arm);
  });
});
