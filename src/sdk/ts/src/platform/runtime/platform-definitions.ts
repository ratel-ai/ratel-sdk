import type {
  ExperimentalDefinitionOverlaySource,
  ExperimentalDefinitionOverridesAttachment,
  ExperimentalDefinitionOverridesRuntimeCatalog,
} from "../../index.js";
import { RuntimeCatalogClient } from "../runtime-catalog.js";
import { type PlatformClientOptions, Transport } from "../transport.js";

export type PlatformDefinitionsOverlaySource = ExperimentalDefinitionOverlaySource;
export type PlatformDefinitionsAttachment = ExperimentalDefinitionOverridesAttachment;
export type PlatformDefinitionsRuntimeCatalog = Partial<
  Pick<ExperimentalDefinitionOverridesRuntimeCatalog, "experimentalAttachDefinitionOverrides">
>;

/** Bridge the Platform override client into the core SDK's injected overlay seam. */
export function createPlatformDefinitionsSource(
  options: PlatformClientOptions,
): PlatformDefinitionsOverlaySource {
  const client = new RuntimeCatalogClient(new Transport(options));
  return {
    fetch: async (ifNoneMatch) => {
      const result = await client.listOverrides({ ifNoneMatch });
      if (result.notModified) return { status: 304 };
      if (result.etag === null) {
        throw new Error("Ratel Platform definitions response is missing a strong ETag");
      }
      return {
        status: 200,
        etag: result.etag,
        body: { overrides: result.overrides },
      };
    },
  };
}
