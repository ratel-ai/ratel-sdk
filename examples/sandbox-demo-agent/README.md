# `examples/sandbox-demo-agent`: a support agent in a Docker image for Ratel (TypeScript)

A support agent with a read-only tool (`lookup_order`) and a tool with a side effect
(`refund_order`). `serve(run)` of [`@ratel-ai/sdk/serve`](../../src/sdk/ts/README.md) makes the
image answer the run contract ([`protocol/sandbox/v1`](../../protocol/sandbox/v1/README.md)), and
`ratel.yaml` holds its configuration: the input example, the parameters `model` and `tone`, the
secret `ANTHROPIC_API_KEY`, the tool modes and the network allow-list.

```ts
// src/agent.ts
const tools = {
  lookup_order: tool("lookup_order", { execute: lookupOrder, fake: lookupOrder }),
  refund_order: tool("refund_order", {
    execute: refundOrder, // the real refund: it prints REAL REFUND
    fake: ({ order_id }) => ({ order_id, status: "refunded" }),
  }),
};

serve(async ({ input, params, traceparent }) =>
  answer(input.message, { tools, model: params.model, tone: params.tone, parent: parentOf(traceparent) }),
);
```

`ratel.yaml` sets `refund_order` to `simulated`, so a run answers from `fake` and never prints
`REAL REFUND`. The response lists each tool call with its mode.

## Build, run on this machine, push

From the root of the repository:

```bash
pnpm install && pnpm --filter "@ratel-ai/example-sandbox-demo-agent^..." build
cd examples/sandbox-demo-agent && pnpm build     # bundles src/agent.ts into dist/agent.cjs

export ANTHROPIC_API_KEY=...
pnpm exec ratel run --local --input '{"message":"My vase from order A200 arrived damaged. Refund it please."}'

export RATEL_API_KEY=...                      # a project key of Ratel
pnpm exec ratel push
```

`ratel run --local` builds the image, starts it with local Docker, sends one request and prints the
output and the tool calls. `ratel push` sends the image and `ratel.yaml` to Ratel and waits for the
smoke check. Give Ratel the value of `ANTHROPIC_API_KEY` one time, on the Ratel sandbox page: no
value is in this folder.

`pnpm start "<message>"` runs the agent on this machine with the real tools (`src/main.ts`).

## Traces

Ratel gives each run container `RATEL_BASE_URL` (the API root of the Ratel that started the run)
and `RATEL_API_KEY` (a run token). `src/otlp.ts` sends the traces to `$RATEL_BASE_URL/traces`, so a
run on any Ratel sends its traces back to that Ratel with no change here. `RATEL_OTLP_ENDPOINT`
replaces that address. With neither (`pnpm start` with no env), the agent exports nothing.

Ratel adds the host of its own address to the outbound allow-list of the VM. Do not add it to
`network.allow`.

A Ratel server on your machine: the VM must reach it. Set `SANDBOX_CALLBACK_URL` of the server to
an address that the VM can open:

- `SANDBOX_PROVIDER=local-docker`: `SANDBOX_CALLBACK_URL=http://host.docker.internal:<port>`.
- A remote VM provider: a public tunnel to the server, for example
  `cloudflared tunnel --url http://localhost:<port>`, and `SANDBOX_CALLBACK_URL=https://<name>.trycloudflare.com`.
  Ratel allows that host for the VM. Keep the tunnel out of `ratel.yaml`.

## The weak behavior

The first prompt (`src/prompt.ts`) tells the agent to refund a damaged item with no lookup. Order
A200 was delivered 95 days ago, so a refund is not allowed. With the input above the agent calls
`refund_order` and never calls `lookup_order`. Replace the last line of the prompt with the line in
its comment. Then the agent calls `lookup_order` and refuses the refund.

## Layout

```
ratel.yaml             the configuration of the agent on Ratel
Dockerfile             the image: node:24-slim and dist/agent.cjs
src/agent.ts           the entry point of the image: serve(run) and the tools
src/support-agent.ts   the agent: the model loop and its spans
src/tools.ts           the two tools
src/prompt.ts          the prompt, with the weak line
src/telemetry.ts       OpenTelemetry providers
src/otlp.ts            where the traces go: RATEL_OTLP_ENDPOINT, else RATEL_BASE_URL/traces
src/main.ts            a run on your machine with the real tools
test/agent.test.ts     a fake model: refund_order is simulated and the real refund never runs
```
