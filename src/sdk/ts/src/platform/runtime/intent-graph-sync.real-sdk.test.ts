import { createRequire } from "node:module";
import { afterEach, describe, expect, it, vi } from "vitest";
import { IntentGraph, ratel } from "../../index.js";
import { attachIntentGraphSync, type IntentGraphSyncError } from "./intent-graph-sync.js";

const require = createRequire(import.meta.url);
const installedSdkVersion: string = (require("@ratel-ai/sdk/package.json") as { version: string })
  .version;

/**
 * GA-only major.minor.patch comparison: a prerelease suffix (e.g. "0.13.0-rc.11")
 * never satisfies the floor, regardless of its numeric value. Verified against
 * 0.13.0-rc.11 (2026-10): `experimentalEnableAdaptiveRanking(graph, { learn: false })`
 * silently ignores the unknown `learn` key and learns anyway (the graph's rev moves
 * on search/invoke) — a naive numeric compare would have un-skipped the test below
 * onto an SDK that doesn't actually have the feature, and the assertion caught it.
 * Only trust a clean (non-prerelease) version at or above `min`.
 */
function meetsMinVersion(version: string, min: readonly [number, number, number]): boolean {
  if (version.includes("-")) return false;
  const parts = version.split(".").map((part) => Number.parseInt(part, 10));
  for (let index = 0; index < 3; index += 1) {
    const actual = parts[index] ?? 0;
    const required = min[index] ?? 0;
    if (actual !== required) return actual > required;
  }
  return true;
}

// TODO(RC-204): @ratel-ai/sdk 0.13.0 is only published as -rc.* as of this writing (rc.11
// confirmed to still lack `learn`, see meetsMinVersion's comment), so the `learn: false`
// test below is permanently skipped until a GA 0.13.0 (or newer) ships. Once it does: bump
// the devDependency, drop the `@ts-expect-error` on
// `experimentalEnableAdaptiveRanking(..., { learn: false })`, and confirm this test
// actually runs (not just typechecks) before relying on its coverage.
const supportsLearnFalse = meetsMinVersion(installedSdkVersion, [0, 13, 0]);

/**
 * Unlike intent-graph-sync.test.ts (which mocks `@ratel-ai/sdk` entirely for
 * fast, deterministic unit tests), this file uses the real native SDK — no
 * `vi.mock` — so `IntentGraph.fromJson`'s actual parser/validation and real
 * `rev`/`clusterCount` semantics are genuinely exercised, not stood in for.
 * `fetch` is still mocked: this needs no live Platform server (unlike
 * attach.integration.test.ts, which is RATEL_E2E-gated for exactly that
 * reason), so it runs ungated in the normal suite, matching how attach.test.ts
 * already runs its own real-`ratel()` tests without any gate.
 */

function jsonResponse(
  body: unknown,
  init: { status?: number; headers?: Record<string, string> } = {},
): Response {
  return Response.json(body, {
    status: init.status ?? 200,
    ...(init.headers === undefined ? {} : { headers: init.headers }),
  });
}

async function buildSeedGraph(): Promise<{
  runtime: ReturnType<typeof ratel>;
  seedGraph: IntentGraph;
}> {
  const runtime = ratel({ events: { sourceId: "billing-agent" } });
  await runtime.tools.register({
    id: "deploy_rollback",
    name: "deploy_rollback",
    description: "Roll back the last deploy.",
    inputSchema: { type: "object" },
    outputSchema: { type: "string" },
    execute: () => "rolled back",
  });
  const seedGraph = new IntentGraph();
  runtime.tools.catalog.experimentalEnableAdaptiveRanking(seedGraph);
  for (let i = 0; i < 3; i += 1) {
    runtime.tools.catalog.experimentalRecordBaselineTurn({
      query: "how do I roll back a deploy",
      invoked: ["deploy_rollback"],
    });
  }
  runtime.tools.catalog.experimentalDisableAdaptiveRanking();
  return { runtime, seedGraph };
}

describe("attachIntentGraphSync against the real @ratel-ai/sdk", () => {
  it("loads a real graph and its rev/clusterCount round-trip through the native fromJson", async () => {
    const { runtime, seedGraph } = await buildSeedGraph();
    expect(seedGraph.rev).toBeGreaterThan(0);

    const fetchImpl = (async () =>
      jsonResponse(
        {
          sourceId: "billing-agent",
          rev: seedGraph.rev,
          graph: JSON.parse(seedGraph.toJson()),
        },
        { headers: { ETag: '"e1"' } },
      )) as typeof fetch;

    const sync = await attachIntentGraphSync(runtime, { apiKey: "rtl_test", fetch: fetchImpl });

    expect(sync.graph).toBeInstanceOf(IntentGraph);
    expect(sync.graph.rev).toBe(seedGraph.rev);
    expect(sync.graph.clusterCount).toBe(seedGraph.clusterCount);
    await sync.close();
  });

  it("a debounced save embeds a graph the native fromJson genuinely accepts", async () => {
    const { runtime, seedGraph } = await buildSeedGraph();
    const bodies: string[] = [];
    let call = 0;
    const fetchImpl = (async (_url: RequestInfo | URL, init?: RequestInit) => {
      call += 1;
      if (call === 1) {
        return jsonResponse(
          { sourceId: "billing-agent", rev: seedGraph.rev, graph: JSON.parse(seedGraph.toJson()) },
          { headers: { ETag: '"e1"' } },
        );
      }
      bodies.push(String(init?.body ?? ""));
      return jsonResponse({ rev: 0 });
    }) as typeof fetch;

    const sync = await attachIntentGraphSync(runtime, {
      apiKey: "rtl_test",
      fetch: fetchImpl,
      debounceMs: 10,
    });
    const revAtLoad = sync.graph.rev;

    // Wire the real, now-adopted graph back into the catalog and record more
    // real usage on it — exactly the README's onReplaced re-wiring pattern.
    runtime.tools.catalog.experimentalEnableAdaptiveRanking(sync.graph);
    runtime.tools.catalog.experimentalRecordBaselineTurn({
      query: "roll back the production deploy right now",
      invoked: ["deploy_rollback"],
    });
    expect(sync.graph.rev).toBeGreaterThan(revAtLoad);

    await sync.flush();

    expect(bodies).toHaveLength(1);
    const body = bodies[0] ?? "";
    const graphJson = body.slice(body.indexOf('"graph":') + '"graph":'.length, -1);
    const parsedBack = IntentGraph.fromJson(graphJson);
    expect(parsedBack.rev).toBe(sync.graph.rev);

    await sync.close();
  });

  it("fromJson throws on malformed JSON and on an unknown schema version", () => {
    expect(() => IntentGraph.fromJson("not json")).toThrow();
    expect(() => IntentGraph.fromJson('{"v":999,"rev":0,"clusters":[]}')).toThrow();
  });

  describe("a stored graph this build cannot read (e.g. a newer schema version)", () => {
    /** The seed graph re-labelled as a future wire version, which native fromJson rejects. */
    function futureVersionGraph(seedGraph: IntentGraph): unknown {
      return { ...(JSON.parse(seedGraph.toJson()) as Record<string, unknown>), v: 2 };
    }

    function captureUnhandledRejections(): { reasons: unknown[]; stop: () => void } {
      const reasons: unknown[] = [];
      const listener = (reason: unknown): void => {
        reasons.push(reason);
      };
      process.on("unhandledRejection", listener);
      return { reasons, stop: () => process.off("unhandledRejection", listener) };
    }

    afterEach(() => {
      vi.useRealTimers();
    });

    it("fails the initial load open and retries, instead of rejecting attach", async () => {
      vi.useFakeTimers();
      const { runtime, seedGraph } = await buildSeedGraph();
      const errors: IntentGraphSyncError[] = [];
      let call = 0;
      const fetchImpl = (async () => {
        call += 1;
        const graph = call === 1 ? futureVersionGraph(seedGraph) : JSON.parse(seedGraph.toJson());
        return jsonResponse({ sourceId: "billing-agent", rev: seedGraph.rev, graph });
      }) as typeof fetch;

      const sync = await attachIntentGraphSync(runtime, {
        apiKey: "rtl_test",
        fetch: fetchImpl,
        random: () => 1,
        onError: (error) => errors.push(error),
      });

      expect(sync.status).toBe("error");
      expect(errors).toEqual([expect.objectContaining({ kind: "network", status: null })]);
      expect(sync.graph.rev).toBe(0);

      await vi.advanceTimersByTimeAsync(1_000);
      expect(call).toBe(2);
      expect(sync.status).toBe("idle");
      expect(sync.graph.rev).toBe(seedGraph.rev);
      await sync.close();
    });

    it("a consume-mode poll keeps the current graph and polls again, with no unhandled rejection", async () => {
      vi.useFakeTimers();
      const unhandled = captureUnhandledRejections();
      try {
        const { runtime, seedGraph } = await buildSeedGraph();
        let call = 0;
        const fetchImpl = (async () => {
          call += 1;
          const graph = call === 2 ? futureVersionGraph(seedGraph) : JSON.parse(seedGraph.toJson());
          return jsonResponse(
            { sourceId: "platform", rev: seedGraph.rev, graph },
            { headers: { ETag: `"e${call}"` } },
          );
        }) as typeof fetch;
        const replaced: IntentGraph[] = [];

        const sync = await attachIntentGraphSync(runtime, {
          apiKey: "rtl_test",
          fetch: fetchImpl,
          mode: "consume",
          graphKey: "platform",
          pollIntervalMs: 15_000,
          random: () => 1,
          onReplaced: (graph) => replaced.push(graph),
        });
        const loaded = sync.graph;

        await vi.advanceTimersByTimeAsync(15_000);
        expect(call).toBe(2);
        expect(sync.status).toBe("error");
        expect(sync.graph).toBe(loaded);
        expect(replaced).toHaveLength(0);

        await vi.advanceTimersByTimeAsync(15_000);
        expect(call).toBe(3);
        expect(sync.status).toBe("idle");
        expect(replaced).toHaveLength(1);

        await sync.close();
        await vi.advanceTimersByTimeAsync(0);
        expect(unhandled.reasons).toEqual([]);
      } finally {
        unhandled.stop();
      }
    });

    it("a push-mode re-fetch after a 409 retries on backoff, with no unhandled rejection", async () => {
      vi.useFakeTimers();
      const unhandled = captureUnhandledRejections();
      try {
        const { runtime, seedGraph } = await buildSeedGraph();
        const methods: string[] = [];
        let gets = 0;
        const fetchImpl = (async (_url: RequestInfo | URL, init?: RequestInit) => {
          const method = init?.method ?? "GET";
          methods.push(method);
          if (method === "PUT") return jsonResponse({ error: "stale_graph" }, { status: 409 });
          gets += 1;
          if (gets === 1) return jsonResponse({ error: "not_found" }, { status: 404 });
          const graph = gets === 2 ? futureVersionGraph(seedGraph) : JSON.parse(seedGraph.toJson());
          return jsonResponse(
            { sourceId: "billing-agent", rev: seedGraph.rev, graph },
            { headers: { ETag: '"platform"' } },
          );
        }) as typeof fetch;
        const replaced: IntentGraph[] = [];

        const sync = await attachIntentGraphSync(runtime, {
          apiKey: "rtl_test",
          fetch: fetchImpl,
          debounceMs: 10,
          random: () => 1,
          onReplaced: (graph) => replaced.push(graph),
        });
        runtime.tools.catalog.experimentalEnableAdaptiveRanking(sync.graph);
        runtime.tools.catalog.experimentalRecordBaselineTurn({
          query: "roll back the production deploy right now",
          invoked: ["deploy_rollback"],
        });

        await sync.flush();
        expect(methods).toEqual(["GET", "PUT", "GET"]);
        expect(sync.status).toBe("error");
        expect(replaced).toHaveLength(0);

        // The backoff retry re-sends the save; Platform rejects it as stale again,
        // and this time the re-fetched graph is readable and adopted.
        await vi.advanceTimersByTimeAsync(1_000);
        expect(methods).toEqual(["GET", "PUT", "GET", "PUT", "GET"]);
        expect(sync.status).toBe("idle");
        expect(replaced).toHaveLength(1);
        expect(sync.graph.rev).toBe(seedGraph.rev);

        await sync.close();
        await vi.advanceTimersByTimeAsync(0);
        expect(unhandled.reasons).toEqual([]);
      } finally {
        unhandled.stop();
      }
    });
  });

  (supportsLearnFalse ? it : it.skip)(
    `consume mode + learn: false keeps the adopted graph inert during search/invoke ` +
      `(requires @ratel-ai/sdk >=0.13.0, installed ${installedSdkVersion})`,
    async () => {
      const { runtime, seedGraph } = await buildSeedGraph();
      const graphJson = seedGraph.toJson();

      const fetchImpl = (async () =>
        jsonResponse(
          { sourceId: "platform", rev: seedGraph.rev, graph: JSON.parse(graphJson) },
          { headers: { ETag: '"platform-e1"' } },
        )) as typeof fetch;

      const sync = await attachIntentGraphSync(runtime, {
        apiKey: "rtl_test",
        fetch: fetchImpl,
        mode: "consume",
        graphKey: "platform",
      });

      runtime.tools.catalog.experimentalEnableAdaptiveRanking(sync.graph, { learn: false });
      const revBefore = sync.graph.rev;
      const jsonBefore = sync.graph.toJson();

      runtime.tools.search("how do I roll back a deploy", 1);
      await runtime.tools.invoke("deploy_rollback", {});

      expect(sync.graph.rev).toBe(revBefore);
      expect(sync.graph.toJson()).toBe(jsonBefore);

      await sync.close();
    },
  );
});
