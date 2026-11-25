import { randomBytes } from "node:crypto";

export type TraceIds = { traceId: string; spanId: string };

export function newTrace(): TraceIds {
  return { traceId: randomBytes(16).toString("hex"), spanId: randomBytes(8).toString("hex") };
}

export function traceparent({ traceId, spanId }: TraceIds): string {
  return `00-${traceId}-${spanId}-01`;
}

/** The OTLP JSON of one root span. The agent's own spans join it through the trace context. */
export function rootSpan(
  ids: TraceIds,
  span: {
    name: string;
    startMs: number;
    endMs: number;
    input: unknown;
    output: unknown;
    runId: string;
    error?: { type: string; message: string };
  },
): unknown {
  const attributes: Record<string, string> = {
    "gen_ai.operation.name": "invoke_agent",
    "gen_ai.agent.name": span.name,
    "ratel.sandbox.run_id": span.runId,
    "gen_ai.input.messages": message("user", span.input),
    ...(span.error
      ? { "error.type": span.error.type }
      : { "gen_ai.output.messages": message("assistant", span.output) }),
  };
  return {
    resourceSpans: [
      {
        resource: { attributes: toAttributes({ "service.name": span.name }) },
        scopeSpans: [
          {
            scope: { name: "@ratel-ai/sandbox" },
            spans: [
              {
                traceId: ids.traceId,
                spanId: ids.spanId,
                name: `invoke_agent ${span.name}`,
                kind: 1,
                startTimeUnixNano: nanos(span.startMs),
                endTimeUnixNano: nanos(span.endMs),
                attributes: toAttributes(attributes),
                status: span.error ? { code: 2, message: span.error.message } : { code: 1 },
              },
            ],
          },
        ],
      },
    ],
  };
}

function message(role: string, value: unknown): string {
  const content = typeof value === "string" ? value : JSON.stringify(value ?? null);
  return JSON.stringify([{ role, parts: [{ type: "text", content }] }]);
}

function toAttributes(values: Record<string, string>) {
  return Object.entries(values).map(([key, value]) => ({ key, value: { stringValue: value } }));
}

function nanos(ms: number): string {
  return `${BigInt(Math.round(ms)) * 1_000_000n}`;
}
