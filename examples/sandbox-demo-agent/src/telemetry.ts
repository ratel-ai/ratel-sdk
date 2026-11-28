import {
  type Attributes,
  type Context,
  context,
  propagation,
  type Span,
  SpanStatusCode,
  trace,
} from "@opentelemetry/api";
import { W3CTraceContextPropagator } from "@opentelemetry/core";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-proto";
import { resourceFromAttributes } from "@opentelemetry/resources";
import { BatchSpanProcessor } from "@opentelemetry/sdk-trace-base";
import { NodeTracerProvider } from "@opentelemetry/sdk-trace-node";
import { ATTR_SERVICE_NAME } from "@opentelemetry/semantic-conventions";

const endpoint = process.env.RATEL_OTLP_ENDPOINT;
const apiKey = process.env.RATEL_API_KEY;

const provider = new NodeTracerProvider({
  resource: resourceFromAttributes({ [ATTR_SERVICE_NAME]: "support-demo" }),
  spanProcessors: endpoint
    ? [
        new BatchSpanProcessor(
          new OTLPTraceExporter({
            url: endpoint,
            headers: apiKey ? { Authorization: `Bearer ${apiKey}` } : {},
          }),
        ),
      ]
    : [],
});
provider.register({ propagator: new W3CTraceContextPropagator() });
const tracer = provider.getTracer("support-demo");

export function flush(): Promise<void> {
  return provider.forceFlush();
}

/** The parent of the agent's spans: the sandbox run, or a new trace. */
export function parentOf(traceparent: string | undefined): Context {
  return propagation.extract(context.active(), traceparent ? { traceparent } : {});
}

export function inSpan<T>(
  name: string,
  attributes: Attributes,
  parent: Context,
  work: (span: Span, active: Context) => Promise<T>,
): Promise<T> {
  return tracer.startActiveSpan(name, { attributes }, parent, async (span) => {
    try {
      return await work(span, trace.setSpan(parent, span));
    } catch (error) {
      span.setStatus({ code: SpanStatusCode.ERROR });
      throw error;
    } finally {
      span.end();
    }
  });
}
