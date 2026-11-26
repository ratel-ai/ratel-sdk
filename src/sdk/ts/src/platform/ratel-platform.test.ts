import { afterEach, describe, expect, it, vi } from "vitest";
import { ratel } from "../index.js";
import { referenceAdapter } from "../testkit/reference-adapter.js";
import { PlatformError } from "./errors.js";
import { ratelPlatform } from "./runtime/tool-picker.js";
import { MockPlatform } from "./testing/mock-platform.js";

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

/** A MockPlatform whose fetch also records each request URL and method. */
function recordingPlatform(): { mock: MockPlatform; requests: string[]; fetch: typeof fetch } {
  const mock = new MockPlatform();
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

describe("ratel({ platform })", () => {
  it("publishes events and a catalog snapshot with platform: true and RATEL_API_KEY", async () => {
    const mock = new MockPlatform();
    vi.stubEnv("RATEL_API_KEY", mock.apiKey);
    vi.stubEnv("RATEL_BASE_URL", BASE_URL);
    vi.stubGlobal("fetch", mock.fetch);

    const r = ratel({ platform: true });
    await r.tools.register(DEPLOY, ROLLBACK);
    r.tools.search("roll back the deploy", 3);
    await r.tools.invoke("rollback", {});
    await r.platform.flush();

    expect(mock.runtimeEvents.length).toBeGreaterThan(0);
    expect(mock.runtimeEvents.map((event) => event.type)).toContain("search");
    expect(
      mock.catalogSnapshots
        .at(-1)
        ?.tools.map((tool) => tool.id)
        .sort(),
    ).toEqual(["deploy", "rollback"]);
    expect(r.platform.status().events.accepted).toBeGreaterThan(0);
    await r.platform.close();
  });

  it("reaches the management API with the key from the same config", async () => {
    const mock = new MockPlatform();
    const r = ratel({ platform: { apiKey: mock.apiKey, baseUrl: BASE_URL, fetch: mock.fetch } });

    const created = await r.platform.skills.create({
      name: "deploy-checklist",
      description: "How to deploy safely.",
      body: "# Deploy",
    });
    const listed = await r.platform.skills.list();

    expect(mock.skills.get(created.id)?.name).toBe("deploy-checklist");
    expect(listed.skills.map((skill) => skill.name)).toEqual(["deploy-checklist"]);
    await r.platform.close();
  });

  it("throws at construction with no API key, and names RATEL_API_KEY", () => {
    vi.stubEnv("RATEL_API_KEY", "");

    expect(() => ratel({ platform: true })).toThrow(PlatformError);
    expect(() => ratel({ platform: true })).toThrow(/RATEL_API_KEY/);
  });

  it("publishes nothing with events: false", async () => {
    const { mock, requests, fetch } = recordingPlatform();
    const r = ratel({ platform: { apiKey: mock.apiKey, baseUrl: BASE_URL, fetch, events: false } });
    await r.tools.register(DEPLOY);
    r.tools.search("deploy", 3);
    await r.platform.flush();

    expect(requests).toEqual([]);
    expect(r.platform.status().events.accepted).toBe(0);
    await r.platform.close();
  });

  it("publishes no runtime events with RATEL_PLATFORM_EVENTS=off", async () => {
    vi.stubEnv("RATEL_PLATFORM_EVENTS", "off");
    const { mock, requests, fetch } = recordingPlatform();
    const r = ratel({ platform: { apiKey: mock.apiKey, baseUrl: BASE_URL, fetch } });
    await r.tools.register(DEPLOY);
    r.tools.search("deploy", 3);
    await r.platform.flush();

    expect(requests).not.toContain(`POST ${BASE_URL}/events`);
    expect(mock.runtimeEvents).toEqual([]);
    expect(r.platform.status().overall).toBe("disabled");
    await r.platform.close();
  });

  it("still publishes no runtime events with the deprecated RATEL_CLOUD_EVENTS=off", async () => {
    vi.stubEnv("RATEL_CLOUD_EVENTS", "off");
    const { mock, fetch } = recordingPlatform();
    const r = ratel({ platform: { apiKey: mock.apiKey, baseUrl: BASE_URL, fetch } });
    await r.tools.register(DEPLOY);
    r.tools.search("deploy", 3);
    await r.platform.flush();

    expect(mock.runtimeEvents).toEqual([]);
    expect(r.platform.status().overall).toBe("disabled");
    await r.platform.close();
  });

  it("lets RATEL_PLATFORM_EVENTS win over RATEL_CLOUD_EVENTS", async () => {
    vi.stubEnv("RATEL_CLOUD_EVENTS", "off");
    vi.stubEnv("RATEL_PLATFORM_EVENTS", "on");
    const { mock, fetch } = recordingPlatform();
    const r = ratel({ platform: { apiKey: mock.apiKey, baseUrl: BASE_URL, fetch } });
    await r.tools.register(DEPLOY);
    r.tools.search("deploy", 3);
    await r.platform.flush();

    expect(mock.runtimeEvents.map((event) => event.type)).toContain("search");
    await r.platform.close();
  });

  it("sends each request to https://app.ratel.sh/api/v1 with no baseUrl", async () => {
    vi.stubEnv("RATEL_BASE_URL", "");
    const { mock, requests, fetch } = recordingPlatform();
    const r = ratel({ platform: { apiKey: mock.apiKey, fetch } });
    await r.tools.register(DEPLOY);
    r.tools.search("deploy", 3);
    await r.platform.skills.list();
    await r.platform.flush();

    expect(requests).toContain("GET https://app.ratel.sh/api/v1/skills");
    expect(requests).toContain("POST https://app.ratel.sh/api/v1/events");
    expect(requests.every((request) => request.includes(" https://app.ratel.sh/api/v1/"))).toBe(
      true,
    );
    await r.platform.close();
  });

  it("sends each request to RATEL_BASE_URL for a self-hosted platform", async () => {
    vi.stubEnv("RATEL_BASE_URL", "https://ratel.internal.test/api/v1");
    const { mock, requests, fetch } = recordingPlatform();
    const r = ratel({ platform: { apiKey: mock.apiKey, fetch } });
    await r.tools.register(DEPLOY);
    r.tools.search("deploy", 3);
    await r.platform.skills.list();
    await r.platform.flush();

    expect(requests).toContain("GET https://ratel.internal.test/api/v1/skills");
    expect(requests).toContain("POST https://ratel.internal.test/api/v1/events");
    expect(
      requests.every((request) => request.includes(" https://ratel.internal.test/api/v1/")),
    ).toBe(true);
    await r.platform.close();
  });

  it("applies Platform definitions with definitions: true", async () => {
    const mock = new MockPlatform();
    mock.seedRuntimeCatalogOverride({
      kind: "tool",
      entryId: "deploy",
      searchableDescription: "quasar zephyr rollback",
    });
    const r = ratel({
      platform: { apiKey: mock.apiKey, baseUrl: BASE_URL, fetch: mock.fetch, definitions: true },
    });
    await r.tools.register(DEPLOY, ROLLBACK);

    await r.platform.ready();
    await r.platform.refreshDefinitions();

    expect(r.tools.search("quasar zephyr", 3).map((hit) => hit.toolId)).toEqual(["deploy"]);
    await r.platform.close();
  });

  it("loads the intent graph and enables adaptive ranking with intentGraph: true", async () => {
    const { mock, requests, fetch } = recordingPlatform();
    const r = ratel({
      events: { sourceId: "billing-agent" },
      platform: { apiKey: mock.apiKey, baseUrl: BASE_URL, fetch, events: false, intentGraph: true },
    });

    await r.platform.ready();

    expect(requests).toContain(`GET ${BASE_URL}/intent-graph?source=billing-agent`);
    expect(r.tools.catalog.experimentalAdaptiveRankingStatus.status).toBe("active");
    await r.platform.close();
  });

  it("does not load the intent graph with RATEL_PLATFORM_INTENT_GRAPH=off", async () => {
    vi.stubEnv("RATEL_PLATFORM_INTENT_GRAPH", "off");
    const { mock, requests, fetch } = recordingPlatform();
    const r = ratel({
      platform: { apiKey: mock.apiKey, baseUrl: BASE_URL, fetch, events: false, intentGraph: true },
    });

    await r.platform.ready();

    expect(requests).toEqual([]);
    await r.platform.close();
  });

  it("still skips the intent graph with the deprecated RATEL_CLOUD_INTENT_GRAPH=off", async () => {
    vi.stubEnv("RATEL_CLOUD_INTENT_GRAPH", "off");
    const { mock, requests, fetch } = recordingPlatform();
    const r = ratel({
      platform: { apiKey: mock.apiKey, baseUrl: BASE_URL, fetch, events: false, intentGraph: true },
    });

    await r.platform.ready();

    expect(requests).toEqual([]);
    await r.platform.close();
  });

  it("lets RATEL_PLATFORM_INTENT_GRAPH win over RATEL_CLOUD_INTENT_GRAPH", async () => {
    vi.stubEnv("RATEL_CLOUD_INTENT_GRAPH", "off");
    vi.stubEnv("RATEL_PLATFORM_INTENT_GRAPH", "on");
    const { mock, requests, fetch } = recordingPlatform();
    const r = ratel({
      events: { sourceId: "billing-agent" },
      platform: { apiKey: mock.apiKey, baseUrl: BASE_URL, fetch, events: false, intentGraph: true },
    });

    await r.platform.ready();

    expect(requests).toContain(`GET ${BASE_URL}/intent-graph?source=billing-agent`);
    await r.platform.close();
  });

  it("ranks tools with the Tool Picker with toolPicker: 'precise'", async () => {
    const mock = new MockPlatform();
    const r = ratel({
      platform: {
        apiKey: mock.apiKey,
        baseUrl: BASE_URL,
        fetch: mock.fetch,
        toolPicker: "precise",
      },
    });
    await r.tools.register(DEPLOY, ROLLBACK);
    await r.platform.flush();

    const hits = await r.tools.searchAsync("roll back", 2);

    expect(hits[0]?.toolId).toBe("rollback");
    expect(mock.toolPicks).toEqual([{ query: "roll back", mode: "precise", topK: 2 }]);
    expect(r.platform.toolPicker.mode).toBe("precise");
    await r.platform.close();
  });

  it.each([
    [{ method: "bm25" as const }],
    [{ retrieveFn: async () => [] }],
  ])("throws a config error when toolPicker meets %o", (ranking) => {
    expect(() =>
      ratel({ ...ranking, platform: { apiKey: "rtl_k", baseUrl: BASE_URL, toolPicker: true } }),
    ).toThrow(/toolPicker/);
  });

  it("gives every adapted view the same handle", () => {
    const r = ratel({
      platform: { apiKey: "rtl_k", baseUrl: BASE_URL, fetch: new MockPlatform().fetch },
    });

    expect(r.adaptTo(referenceAdapter()).platform).toBe(r.platform);
    void r.platform.close();
  });

  it("ready() never rejects", async () => {
    const failing = (async () => {
      throw new Error("offline");
    }) as typeof fetch;
    vi.spyOn(console, "warn").mockImplementation(() => {});
    const r = ratel({
      platform: {
        apiKey: "rtl_k",
        baseUrl: BASE_URL,
        fetch: failing,
        definitions: true,
        intentGraph: true,
        events: { retry: { maxAttempts: 1 }, warnOnFailure: false },
      },
    });

    await expect(r.platform.ready()).resolves.toBeUndefined();
    await r.platform.close();
  });
});

describe("ratel() without platform", () => {
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
    expect(r.platform).toBeUndefined();
    expect("platform" in r).toBe(false);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});

describe("ratelPlatform()", () => {
  it("gives the management clients and the Tool Picker without a runtime", async () => {
    const mock = new MockPlatform();
    const platform = ratelPlatform({ apiKey: mock.apiKey, baseUrl: BASE_URL, fetch: mock.fetch });

    await platform.skills.create({ name: "s", description: "d", body: "b" });

    expect(mock.skills.size).toBe(1);
    expect(platform.toolPicker.mode).toBe("precise");
    expect("flush" in platform).toBe(false);
  });
});
