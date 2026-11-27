/**
 * Where the agent sends its traces: `RATEL_OTLP_ENDPOINT`, else the traces route of the
 * `RATEL_BASE_URL` that Ratel gives to the container of each run (the Ratel that started the run).
 * With neither, the agent exports nothing.
 */
export function otlpEndpoint(env: Record<string, string | undefined>): string | undefined {
  if (env.RATEL_OTLP_ENDPOINT) return env.RATEL_OTLP_ENDPOINT;
  const base = env.RATEL_BASE_URL?.replace(/\/+$/, "");
  return base ? `${base}/traces` : undefined;
}
