import { createHash, randomBytes, randomUUID } from "node:crypto";
import { createRequire } from "node:module";
import { resolve } from "node:path";
import { pathToFileURL } from "node:url";
import { trace } from "@opentelemetry/api";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-proto";
import {
  BasicTracerProvider,
  SimpleSpanProcessor,
} from "@opentelemetry/sdk-trace-base";
import { attach } from "@ratel-ai/sdk/cloud";
import { MockCloud } from "@ratel-ai/sdk/cloud/testing";

// Hybrid target:
//   • RATEL_CLOUD_ROOT unset → runs in process against MockCloud, plus its own small fake
//     of the runtime ingest routes (`POST /events`, `PUT /catalog/snapshot`). No network, no database, no credentials.
//   • RATEL_CLOUD_ROOT set → the live acceptance against a local ratel-cloud and its
//     Postgres database (see README.md).
if (!process.env.RATEL_CLOUD_ROOT?.trim()) {
  process.exit(await runAgainstMockCloud());
}

const cloudRoot = requiredEnv("RATEL_CLOUD_ROOT");
const ratelSdkDir = requiredEnv("RATEL_SDK_DIR");
const databaseUrl = requiredEnv("RATEL_E2E_DATABASE_URL");
const baseUrl = requiredEnv("RATEL_BASE_URL").replace(/\/+$/, "");
const sourceId = `cloud-sdk-e2e-${Date.now().toString(36)}`;
const organizationId = randomUUID();
const projectId = randomUUID();
const apiKeyId = randomUUID();
const apiKey = `rtl_e2e_${randomBytes(18).toString("base64url")}`;
const oversizedEventId = `oversized-${randomUUID()}`;
const toolId = `weather-${randomUUID()}`;

const cloudRequire = createRequire(resolve(cloudRoot, "apps/web/package.json"));
const { Client } = cloudRequire("pg");
const { resourceFromAttributes } = cloudRequire("@opentelemetry/resources");
const database = new Client({ connectionString: databaseUrl });
let attachment;
let databaseConnected = false;
let eventSubscription;
let failure;
let provider;
let projectSeeded = false;

try {
  await database.connect();
  databaseConnected = true;
  await seedProject(database);
  projectSeeded = true;
  await assertCloudReady();

  provider = new BasicTracerProvider({
    resource: resourceFromAttributes({ "service.name": sourceId }),
    spanProcessors: [
      new SimpleSpanProcessor(
        new OTLPTraceExporter({
          url: `${baseUrl}/traces`,
          headers: { authorization: `Bearer ${apiKey}` },
        }),
      ),
    ],
  });
  trace.setGlobalTracerProvider(provider);

  const sdk = await import(pathToFileURL(resolve(ratelSdkDir, "dist/index.js")).href);
  sdk.setContentCapture(sdk.ContentCapture.SpanOnly);
  const runtime = sdk.ratel({ events: { sourceId } });
  const observedEvents = [];
  eventSubscription = runtime.events.subscribe((batch) => observedEvents.push(...batch));
  attachment = attach(runtime, {
    apiKey,
    baseUrl,
    snapshotDebounceMs: 0,
    flushIntervalMs: 0,
  });

  await runtime.tools.register({
    id: toolId,
    name: "weather_lookup",
    description: "Look up the current weather for a city",
    inputSchema: {
      type: "object",
      properties: { city: { type: "string" } },
      required: ["city"],
    },
    outputSchema: { type: "object" },
    metadata: { e2e: true },
    execute: ({ city }) => ({ city, temperatureC: 21 }),
  });
  runtime.tools.search("weather in Rome", 1);
  await runtime.tools.invoke(toolId, { city: "Rome" });

  await Promise.all([attachment.flush(), eventSubscription.flush(), provider.forceFlush()]);
  await sendOversizedEvent();
  await waitForAssertions(database, observedEvents);

  const searchEvent = observedEvents.find(
    (event) => event.type === "search" && event.query === "weather in Rome",
  );
  const summary = await buildSummary(database, searchEvent?.event_id);
  assert(summary.factsLanded, "runtime facts did not land in trace_events");
  assert(summary.snapshotLanded, "catalog snapshot did not land in the catalog read model");
  assert(summary.oversizedEventDropped, "oversized event did not reach the Dropped ledger");
  assert(summary.otlpDeduplicated, "direct and OTLP search projections were double-counted");
  console.log(JSON.stringify({ ...summary, deliveryStatus: attachment.status() }));
} catch (error) {
  failure = error;
} finally {
  try {
    await attachment?.close();
    eventSubscription?.unsubscribe();
    await provider?.shutdown();
  } catch (error) {
    failure ??= error;
  }
  if (projectSeeded) {
    try {
      await database.query("delete from organizations where id = $1", [organizationId]);
    } catch (error) {
      failure ??= error;
    }
  }
  if (databaseConnected) {
    try {
      await database.end();
    } catch (error) {
      failure ??= error;
    }
  }
}
// The native SDK owns worker handles; this is a standalone acceptance CLI.
if (failure) console.error(failure);
process.exit(failure ? 1 : 0);

async function seedProject(client) {
  const suffix = organizationId.slice(0, 8);
  await client.query("begin");
  try {
    await client.query(
      "insert into organizations (id, name, slug) values ($1, $2, $3)",
      [organizationId, "Cloud SDK E2E", `cloud-sdk-e2e-${suffix}`],
    );
    await client.query(
      "insert into projects (id, organization_id, name, slug) values ($1, $2, $3, $4)",
      [projectId, organizationId, "Runtime attach E2E", "runtime-attach-e2e"],
    );
    await client.query(
      `insert into api_keys (id, project_id, name, key_hash, key_prefix, key_plaintext)
       values ($1, $2, $3, $4, $5, $6)`,
      [apiKeyId, projectId, "Runtime attach E2E", sha256(apiKey), apiKey.slice(0, 12), apiKey],
    );
    await client.query("commit");
  } catch (error) {
    await client.query("rollback");
    throw error;
  }
}

async function assertCloudReady() {
  const response = await fetch(`${baseUrl}/events`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${apiKey}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({ events: [] }),
  });
  assert(response.status === 202, `local Ratel Cloud returned ${response.status} for /events`);
}

async function sendOversizedEvent() {
  const response = await fetch(`${baseUrl}/events`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${apiKey}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({
      events: [
        {
          v: 2,
          event_id: oversizedEventId,
          ts: Date.now(),
          session_id: "oversized-e2e",
          source_id: sourceId,
          type: "future_oversized_event",
          padding: "x".repeat(70_000),
        },
      ],
    }),
  });
  const body = await response.json();
  assert(response.status === 202, `oversized event request returned ${response.status}`);
  assert(
    body.rejected?.some((rejected) => rejected.event_id === oversizedEventId),
    "Cloud did not reject the oversized event",
  );
}

async function waitForAssertions(client, observedEvents) {
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    const searchEvent = observedEvents.find(
      (event) => event.type === "search" && event.query === "weather in Rome",
    );
    const summary = await buildSummary(client, searchEvent?.event_id);
    if (Object.values(summary).every(Boolean)) return;
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 100));
  }
}

async function buildSummary(client, searchEventId) {
  const [facts, snapshot, dropped, dedup, otlp] = await Promise.all([
    client.query(
      `select type from trace_events
       where project_id = $1 and source_id = $2 and ingest_source = 'runtime'`,
      [projectId, sourceId],
    ),
    client.query(
      `select d.tool_id, d.name, coalesce(u.calls, 0)::int as calls
       from catalog_tool_definitions d
       join runtime_event_sources s
         on s.project_id = d.project_id and s.source_id = d.source_id
       left join tool_usage_counters u
         on u.project_id = d.project_id and u.source_id = d.source_id and u.tool_id = d.tool_id
       where d.project_id = $1 and d.source_id = $2 and d.tool_id = $3`,
      [projectId, sourceId, toolId],
    ),
    client.query(
      "select 1 from trace_event_rejects where project_id = $1 and event_id = $2",
      [projectId, oversizedEventId],
    ),
    searchEventId
      ? client.query(
          "select count(*)::int as count from trace_events where project_id = $1 and event_id = $2",
          [projectId, searchEventId],
        )
      : Promise.resolve({ rows: [{ count: 0 }] }),
    searchEventId
      ? client.query(
          "select count(*)::int as count from otel_spans where project_id = $1 and event_id = $2",
          [projectId, searchEventId],
        )
      : Promise.resolve({ rows: [{ count: 0 }] }),
  ]);
  const factTypes = new Set(facts.rows.map(({ type }) => type));
  const tool = snapshot.rows[0];
  return {
    factsLanded:
      factTypes.has("search") && factTypes.has("invoke_start") && factTypes.has("invoke_end"),
    snapshotLanded: tool?.name === "weather_lookup" && tool?.calls === 1,
    oversizedEventDropped: dropped.rowCount === 1,
    otlpDeduplicated: dedup.rows[0].count === 1 && otlp.rows[0].count === 1,
  };
}

async function runAgainstMockCloud() {
  const ingest = mockRuntimeIngest(new MockCloud());
  const mockBaseUrl = "https://mock.ratel.local/api/v1";
  const mockSourceId = `cloud-sdk-e2e-${Date.now().toString(36)}`;
  const mockToolId = `weather-${randomUUID()}`;
  const mockOversizedEventId = `oversized-${randomUUID()}`;
  let mockAttachment;
  try {
    const { ratel } = await import("@ratel-ai/sdk");
    const runtime = ratel({ events: { sourceId: mockSourceId } });
    mockAttachment = attach(runtime, {
      apiKey: "mock-key",
      baseUrl: mockBaseUrl,
      fetch: ingest.fetch,
      snapshotDebounceMs: 0,
      flushIntervalMs: 0,
    });
    console.log(`attached runtime ${mockSourceId} to MockCloud`);

    await runtime.tools.register({
      id: mockToolId,
      name: "weather_lookup",
      description: "Look up the current weather for a city",
      inputSchema: {
        type: "object",
        properties: { city: { type: "string" } },
        required: ["city"],
      },
      outputSchema: { type: "object" },
      execute: ({ city }) => ({ city, temperatureC: 21 }),
    });
    runtime.tools.search("weather in Rome", 1);
    await runtime.tools.invoke(mockToolId, { city: "Rome" });
    await mockAttachment.flush();

    const oversized = await ingest.fetch(`${mockBaseUrl}/events`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        events: [
          {
            v: 2,
            event_id: mockOversizedEventId,
            type: "future_oversized_event",
            padding: "x".repeat(70_000),
          },
        ],
      }),
    });
    const oversizedReport = await oversized.json();

    const factTypes = new Set(ingest.events.map(({ type }) => type));
    const snapshot = ingest.snapshots.at(-1);
    const summary = {
      factsLanded:
        factTypes.has("search") && factTypes.has("invoke_start") && factTypes.has("invoke_end"),
      snapshotLanded: snapshot?.tools?.some((tool) => tool.name === "weather_lookup") === true,
      oversizedEventDropped: oversizedReport.rejected?.[0]?.event_id === mockOversizedEventId,
    };
    console.log(`facts delivered: ${[...factTypes].join(", ")}`);
    console.log(`catalog snapshots delivered: ${ingest.snapshots.length}`);
    assert(summary.factsLanded, "runtime facts did not reach POST /events");
    assert(summary.snapshotLanded, "the catalog snapshot did not reach PUT /catalog/snapshot");
    assert(summary.oversizedEventDropped, "the oversized event was not rejected");
    const deliveryStatus = mockAttachment.status();
    console.log(`delivery status: ${deliveryStatus.overall}`);
    assert(deliveryStatus.overall === "ok", "the delivery status is not ok");
    console.log(JSON.stringify({ ...summary, deliveryStatus }));
    return 0;
  } catch (error) {
    console.error(error);
    return 1;
  } finally {
    await mockAttachment?.close();
  }
}

/** MockCloud for every route, plus its own fake of the two runtime ingest routes. */
function mockRuntimeIngest(cloud) {
  const events = [];
  const snapshots = [];
  const ingestFetch = async (input, init = {}) => {
    const url = new URL(input instanceof Request ? input.url : String(input));
    const method = init.method ?? "GET";
    if (method === "POST" && url.pathname.endsWith("/events")) {
      const batch = JSON.parse(init.body).events;
      const rejected = [];
      for (const event of batch) {
        if (JSON.stringify(event).length > 65_536) {
          rejected.push({ event_id: event.event_id, reason: "event exceeds 64 KiB" });
        } else {
          events.push(event);
        }
      }
      return Response.json(
        { accepted: batch.length - rejected.length, rejected },
        { status: 202 },
      );
    }
    if (method === "PUT" && url.pathname.endsWith("/catalog/snapshot")) {
      snapshots.push(JSON.parse(init.body));
      return Response.json({ synced: true }, { status: 200 });
    }
    return cloud.fetch(input, init);
  };
  return { events, snapshots, fetch: ingestFetch };
}

function requiredEnv(name) {
  const value = process.env[name]?.trim();
  if (!value) throw new Error(`${name} is required`);
  return value;
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function assert(condition, message) {
  if (!condition) throw new Error(message);
}
