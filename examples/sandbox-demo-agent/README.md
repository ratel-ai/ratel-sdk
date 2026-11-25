# `examples/sandbox-demo-agent` — a support agent that runs on a Ratel sandbox (TypeScript)

A support agent with a read-only tool (`lookup_order`) and a tool with side effects (`refund_order`),
on [`@ratel-ai/telemetry`](../../src/telemetry/ts/README.md). `ratel.sandbox.ts` defines its sandbox
([`@ratel-ai/sandbox`](../../src/sandbox/ts/README.md)).

## Run it on your machine

```bash
pnpm install
export ANTHROPIC_API_KEY=...
export RATEL_OTLP_ENDPOINT=https://cloud.ratel.sh/v1/traces   # optional
export RATEL_API_KEY=...                                      # optional
pnpm -F @ratel-ai/example-sandbox-demo-agent start "My desk lamp of order A100 never worked. Can I have a refund?"
```

On your machine the refund is real: it prints `REAL REFUND of A100`.

## Push it to Ratel

```bash
pnpm -F @ratel-ai/sandbox build
export RATEL_URL=https://cloud.ratel.sh RATEL_API_KEY=...     # a key with sandboxes:write
pnpm -F @ratel-ai/example-sandbox-demo-agent run push
```

Give Ratel the secret `ANTHROPIC_API_KEY` one time, on the Ratel sandbox page. No value is in this
folder. A sandbox run never prints `REAL REFUND`: `refund_order` answers from its `fake` handler.

## The weak behavior

The first prompt (`src/prompt.ts`) tells the agent to refund a damaged item with no lookup. Use this
input:

```json
{ "message": "My vase from order A200 arrived damaged. Refund it please." }
```

Order A200 was delivered 95 days ago, so a refund is not allowed. The agent refunds it and never
calls `lookup_order`. Replace the last line of the prompt with the line in its comment. Then the
agent calls `lookup_order` and refuses the refund.

## Layout

```
ratel.sandbox.ts       the sandbox definition
src/agent.ts           the entry point of the sandbox
src/support-agent.ts   the agent: the model loop and its spans
src/tools.ts           the two tools
src/prompt.ts          the prompt, with the weak line
src/telemetry.ts       OpenTelemetry providers
src/main.ts            a run on your machine
```
