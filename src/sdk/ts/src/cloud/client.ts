import { IntentsClient } from "./intents.js";
import { JobsClient } from "./jobs.js";
import { RuntimeCatalogClient } from "./runtime-catalog.js";
import { SkillsClient } from "./skills.js";
import { SuggestionsClient } from "./suggestions.js";
import { type CloudSdkOptions, Transport } from "./transport.js";
/**
 * Management & supervised self-improvement client for Ratel Cloud: catalog
 * writes, intent analysis, and suggestion review over the v1 HTTP API. Pure
 * fetch — no native addon, no BM25; all analysis and drafting stay server-side.
 *
 * This package deliberately does NOT sync skills into an agent at runtime —
 * that's the `@ratel-ai/cloud` loader's job (replica, refresh, ownership).
 * The two share only the API key.
 */
export class RatelCloudSdk {
  readonly skills: SkillsClient;
  readonly suggestions: SuggestionsClient;
  readonly intents: IntentsClient;
  readonly jobs: JobsClient;
  readonly runtimeCatalog: RuntimeCatalogClient;

  /** With no options, the client reads `RATEL_API_KEY` and `RATEL_BASE_URL`. */
  constructor(options: CloudSdkOptions = {}) {
    const transport = new Transport(options);
    this.skills = new SkillsClient(transport);
    this.suggestions = new SuggestionsClient(transport);
    this.intents = new IntentsClient(transport);
    this.jobs = new JobsClient(transport);
    this.runtimeCatalog = new RuntimeCatalogClient(transport);
  }
}
