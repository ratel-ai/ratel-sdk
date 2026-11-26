import { afterEach, describe, expect, it, vi } from "vitest";
import { ratel } from "../index.js";
import { referenceAdapter } from "../testkit/reference-adapter.js";
import { CloudSdkError } from "./errors.js";
import { ratelCloud } from "./runtime/tool-picker.js";
import { MockCloud } from "./testing/mock-cloud.js";

const DEPLOY = {
  id: "deploy",
  name: "deploy",
  description: "Prepare a production release.",
  inputSchema: { type: "object" },
  outputSchema: { type: "string" },
  execute: () => "deployed",
} as const;

const ROLLBACK = {
  id: "rollback",
  name: "rollback",
  description: "Roll back the last deploy.",
  inputSchema: { type: "object" },
  outputSchema: { type: "string" },
  execute: () => "rolled back",
} as const;

/** A MockCloud whose fetch also records each request URL and method. */
function recordingCloud(): { mock: MockCloud; requests: string[]; fetch: typeof fetch } {
  const mock = new MockCloud();
  const requests: string[] = [];
  const fetchImpl = ((input: RequestInfo | URL, init?: RequestInit) => {
    requests.push(`${(init?.method ?? "GET").toUpperCase()} ${String(input)}`);
    return mock.fetch(input, init);
  }) as typeof fetch;
  return { mock, requests, fetch: fetchImpl };
}

const BASE_URL = "https://mock.ratel.test/api/v1";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("ratel({ cloud })", () => {
  it("publishes events and a catalog snapshot with cloud: true and RATEL_API_KEY", async () => {
    const mock = new MockCloud();
    vi.stubEnv("RATEL_API_KEY", mock.apiKey);
    vi.stubEnv("RATEL_BASE_URL", BASE_URL);
    vi.stubGlobal("fetch", mock.fetch);

    const r = ratel({ cloud: true });
    await r.tools.register(DEPLOY, ROLLBACK);
    r.tools.search("roll back the deploy", 3);
    await r.tools.invoke("rollback", {});
    await r.cloud.flush();

    expect(mock.runtimeEvents.length).toBeGreaterThan(0);
    expect(mock.runtimeEvents.map((event) => event.type)).toContain("search");
    expect(
      mock.catalogSnapshots
        .at(-1)
        ?.tools.map((tool) => tool.id)
        .sort(),
    ).toEqual(["deploy", "rollback"]);
    expect(r.cloud.status().events.accepted).toBeGreaterThan(0);
    await r.cloud.close();
  });

  it("reaches the management API with the key from the same config", async () => {
    const mock = new MockCloud();
    const r = ratel({ cloud: { apiKey: mock.apiKey, baseUrl: BASE_URL, fetch: mock.fetch } });

    const created = await r.cloud.skills.create({
      name: "deploy-checklist",
      description: "How to deploy safely.",
      body: "# Deploy",
    });
    const listed = await r.cloud.skills.list();

    expect(mock.skills.get(created.id)?.name).toBe("deploy-checklist");
    expect(listed.skills.map((skill) => skill.name)).toEqual(["deploy-checklist"]);
    await r.cloud.close();
  });

  it("throws at construction with no API key, and names RATEL_API_KEY", () => {
    vi.stubEnv("RATEL_API_KEY", "");

    expect(() => ratel({ cloud: true })).toThrow(CloudSdkError);
    expect(() => ratel({ cloud: true })).toThrow(/RATEL_API_KEY/);
  });

  it("publishes nothing with events: false", async () => {
    const { mock, requests, fetch } = recordingCloud();
    const r = ratel({ cloud: { apiKey: mock.apiKey, baseUrl: BASE_URL, fetch, events: false } });
    await r.tools.register(DEPLOY);
    r.tools.search("deploy", 3);
    await r.cloud.flush();

    expect(requests).toEqual([]);
    expect(r.cloud.status().events.accepted).toBe(0);
    await r.cloud.close();
  });

  it("publishes no runtime events with RATEL_CLOUD_EVENTS=off", async () => {
    vi.stubEnv("RATEL_CLOUD_EVENTS", "off");
    const { mock, requests, fetch } = recordingCloud();
    const r = ratel({ cloud: { apiKey: mock.apiKey, baseUrl: BASE_URL, fetch } });
    await r.tools.register(DEPLOY);
    r.tools.search("deploy", 3);
    await r.cloud.flush();

    expect(requests).not.toContain(`POST ${BASE_URL}/events`);
    expect(mock.runtimeEvents).toEqual([]);
    expect(r.cloud.status().overall).toBe("disabled");
    await r.cloud.close();
  });

  it("applies Cloud definitions with definitions: true", async () => {
    const mock = new MockCloud();
    mock.seedRuntimeCatalogOverride({
      kind: "tool",
      entryId: "deploy",
      searchableDescription: "quasar zephyr rollback",
    });
    const r = ratel({
      cloud: { apiKey: mock.apiKey, baseUrl: BASE_URL, fetch: mock.fetch, definitions: true },
    });
    await r.tools.register(DEPLOY, ROLLBACK);

    await r.cloud.ready();
    await r.cloud.refreshDefinitions();

    expect(r.tools.search("quasar zephyr", 3).map((hit) => hit.toolId)).toEqual(["deploy"]);
    await r.cloud.close();
  });

  it("loads the intent graph and enables adaptive ranking with intentGraph: true", async () => {
    const { mock, requests, fetch } = recordingCloud();
    const r = ratel({
      events: { sourceId: "billing-agent" },
      cloud: { apiKey: mock.apiKey, baseUrl: BASE_URL, fetch, events: false, intentGraph: true },
    });

    await r.cloud.ready();

    expect(requests).toContain(`GET ${BASE_URL}/intent-graph?source=billing-agent`);
    expect(r.tools.catalog.experimentalAdaptiveRankingStatus.status).toBe("active");
    await r.cloud.close();
  });

  it("does not load the intent graph with RATEL_CLOUD_INTENT_GRAPH=off", async () => {
    vi.stubEnv("RATEL_CLOUD_INTENT_GRAPH", "off");
    const { mock, requests, fetch } = recordingCloud();
    const r = ratel({
      cloud: { apiKey: mock.apiKey, baseUrl: BASE_URL, fetch, events: false, intentGraph: true },
    });

    await r.cloud.ready();

    expect(requests).toEqual([]);
    await r.cloud.close();
  });

  it("ranks tools with the Tool Picker with toolPicker: 'precise'", async () => {
    const mock = new MockCloud();
    const r = ratel({
      cloud: { apiKey: mock.apiKey, baseUrl: BASE_URL, fetch: mock.fetch, toolPicker: "precise" },
    });
    await r.tools.register(DEPLOY, ROLLBACK);
    await r.cloud.flush();

    const hits = await r.tools.searchAsync("roll back", 2);

    expect(hits[0]?.toolId).toBe("rollback");
    expect(mock.toolPicks).toEqual([{ query: "roll back", mode: "precise", topK: 2 }]);
    expect(r.cloud.toolPicker.mode).toBe("precise");
    await r.cloud.close();
  });

  it.each([
    [{ method: "bm25" as const }],
    [{ retrieveFn: async () => [] }],
  ])("throws a config error when toolPicker meets %o", (ranking) => {
    expect(() =>
      ratel({ ...ranking, cloud: { apiKey: "rtl_k", baseUrl: BASE_URL, toolPicker: true } }),
    ).toThrow(/toolPicker/);
  });

  it("gives every adapted view the same handle", () => {
    const r = ratel({
      cloud: { apiKey: "rtl_k", baseUrl: BASE_URL, fetch: new MockCloud().fetch },
    });

    expect(r.adaptTo(referenceAdapter()).cloud).toBe(r.cloud);
    void r.cloud.close();
  });

  it("ready() never rejects", async () => {
    const failing = (async () => {
      throw new Error("offline");
    }) as typeof fetch;
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const r = ratel({
      cloud: {
        apiKey: "rtl_k",
        baseUrl: BASE_URL,
        fetch: failing,
        definitions: true,
        intentGraph: true,
        events: { retry: { maxAttempts: 1 }, warnOnFailure: false },
      },
    });

    await expect(r.cloud.ready()).resolves.toBeUndefined();
    await r.cloud.close();
  });
});

describe("ratel() without cloud", () => {
  it("makes no network call and runs register, search and invoke", async () => {
    const fetchSpy = vi.fn(async () => {
      throw new Error("ratel() must not call fetch");
    });
    vi.stubGlobal("fetch", fetchSpy);
    vi.stubEnv("RATEL_API_KEY", "rtl_ambient");

    const r = ratel();
    await r.tools.register(DEPLOY);
    const hits = r.tools.search("deploy", 3);
    const result = await r.tools.invoke("deploy", {});
    await r.recall("deploy");
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(hits.map((hit) => hit.toolId)).toEqual(["deploy"]);
    expect(result).toBe("deployed");
    expect(r.cloud).toBeUndefined();
    expect("cloud" in r).toBe(false);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe("ratelCloud()", () => {
  it("gives the management clients and the Tool Picker without a runtime", async () => {
    const mock = new MockCloud();
    const cloud = ratelCloud({ apiKey: mock.apiKey, baseUrl: BASE_URL, fetch: mock.fetch });

    await cloud.skills.create({ name: "s", description: "d", body: "b" });

    expect(mock.skills.size).toBe(1);
    expect(cloud.toolPicker.mode).toBe("precise");
    expect("flush" in cloud).toBe(false);
  });
});
