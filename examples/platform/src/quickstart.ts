/**
 * Quickstart of `ratel({ platform })`: one option turns on Ratel Platform, and `r.platform`
 * reaches the Platform API from the same object.
 *
 * It runs against MockPlatform (an in-process fake of the Platform API) behind a local
 * HTTP server, so the SDK sends real HTTP requests. It uses no real key and no
 * real user data.
 *
 *   pnpm --filter @ratel-ai/example-platform exec tsx src/quickstart.ts
 */

import { createServer, type IncomingMessage, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { ratel } from "@ratel-ai/sdk";
import { MockPlatform } from "@ratel-ai/sdk/platform/testing";

const mock = new MockPlatform();
const server = await serve(mock);
const baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}/api/v1`;
console.log(`MockPlatform listens on ${baseUrl}\n`);

// One option turns on Platform: runtime events and catalog snapshots go to `baseUrl`.
const r = ratel({ platform: { baseUrl, apiKey: mock.apiKey } });

await r.tools.register(
  {
    id: "deploy",
    name: "deploy",
    description: "Deploy the current build to production.",
    inputSchema: { type: "object", properties: { service: { type: "string" } } },
    outputSchema: { type: "string" },
    execute: ({ service }) => `deployed ${String(service)}`,
  },
  {
    id: "rollback",
    name: "rollback",
    description: "Roll back the last production deploy.",
    inputSchema: { type: "object", properties: { service: { type: "string" } } },
    outputSchema: { type: "string" },
    execute: ({ service }) => `rolled back ${String(service)}`,
  },
);

// One turn: a search, then an invoke of the best hit.
await r.turn(
  async () => {
    const [best] = r.tools.search("roll back the production deploy", 2);
    if (best === undefined) throw new Error("the search found no tool");
    console.log(`search  → ${best.toolId}`);
    console.log(`invoke  → ${String(await r.tools.invoke(best.toolId, { service: "billing" }))}`);
  },
  { userMessage: "Roll back the billing deploy" },
);

// The same object reaches the Platform management API, with the same key.
await r.platform.skills.create({
  name: "rollback-checklist",
  description: "Steps to roll back a production deploy safely.",
  body: "# Rollback\n1. Freeze deploys.\n2. Run `rollback`.\n3. Check the error rate.",
});
const { skills } = await r.platform.skills.list();
console.log(`skills  → ${skills.map((skill) => skill.name).join(", ")}\n`);

await r.platform.flush();
const status = r.platform.status();
console.log("r.platform.status():");
console.log(JSON.stringify(status, null, 2));

console.log("\nMockPlatform received:");
console.log(`  runtime events:    ${mock.runtimeEvents.length} (${eventTypes()})`);
console.log(`  catalog snapshots: ${mock.catalogSnapshots.length}`);
for (const snapshot of mock.catalogSnapshots) {
  console.log(
    `    source ${snapshot.source_id}: ${snapshot.tools.map((tool) => tool.id).join(", ")}`,
  );
}

await r.platform.close();
server.close();

const ok =
  status.events.accepted > 0 && mock.catalogSnapshots.length === 1 && skills.length === 1;
console.log(ok ? "\n✓ quickstart passed" : "\n✗ quickstart failed");
process.exitCode = ok ? 0 : 1;

function eventTypes(): string {
  const counts = new Map<string, number>();
  for (const event of mock.runtimeEvents) counts.set(event.type, (counts.get(event.type) ?? 0) + 1);
  return [...counts].map(([type, count]) => `${type} ×${count}`).join(", ");
}

/** Serve `mock` over HTTP on a free local port. */
async function serve(platform: MockPlatform): Promise<Server> {
  const httpServer = createServer((request, response) => {
    void forward(platform, request).then(
      async (answer) => {
        response.writeHead(answer.status, Object.fromEntries(answer.headers));
        response.end(await answer.text());
      },
      (error: unknown) => {
        response.writeHead(500).end(String(error));
      },
    );
  });
  await new Promise<void>((resolve) => httpServer.listen(0, "127.0.0.1", resolve));
  return httpServer;
}

async function forward(platform: MockPlatform, request: IncomingMessage): Promise<Response> {
  const chunks: Buffer[] = [];
  for await (const chunk of request) chunks.push(chunk as Buffer);
  const headers = new Headers();
  for (const [name, value] of Object.entries(request.headers)) {
    if (typeof value === "string") headers.set(name, value);
  }
  return platform.fetch(`http://${request.headers.host}${request.url}`, {
    method: request.method ?? "GET",
    headers,
    body: chunks.length === 0 ? undefined : Buffer.concat(chunks).toString("utf8"),
  });
}
