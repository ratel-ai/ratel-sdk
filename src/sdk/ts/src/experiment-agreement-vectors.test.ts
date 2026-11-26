/**
 * Served-vs-shadow agreement: reproduce every vector in the shared
 * `protocol/v1/conformance/experiment-agreement-vectors.json`. The Python SDK reads the
 * same file, so a failure here means the two SDKs score one comparison differently.
 */
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  compareRankings,
  type ExperimentEvaluationRankedItem,
  type ExperimentRankingAgreement,
  type ExperimentRankingComparisonOptions,
} from "./experiment-evaluation.js";

interface AgreementVectors {
  readonly vectors: readonly {
    readonly name: string;
    readonly served: ExperimentEvaluationRankedItem[];
    readonly shadow: ExperimentEvaluationRankedItem[];
    readonly options: ExperimentRankingComparisonOptions;
    readonly agreement: ExperimentRankingAgreement;
  }[];
}

const vectors: AgreementVectors = JSON.parse(
  readFileSync(
    new URL(
      "../../../../protocol/v1/conformance/experiment-agreement-vectors.json",
      import.meta.url,
    ),
    "utf8",
  ),
);

describe("experiment agreement vectors", () => {
  it.each(vectors.vectors)("scores $name", (vector) => {
    expect(compareRankings(vector.served, vector.shadow, vector.options)).toEqual(vector.agreement);
  });
});
