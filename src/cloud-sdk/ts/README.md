# @ratel-ai/cloud-sdk (deprecated)

The Ratel Cloud client is now the Ratel Platform client of
[`@ratel-ai/sdk`](../../sdk/ts/README.md#ratel-platform). This package is a shim: it re-exports
the old names under its old entry points, so current imports continue to work. Each old name is
an alias of its new Platform name. It holds no logic. New code imports `@ratel-ai/sdk` directly.

```sh
npm install @ratel-ai/sdk
```

## Migration

Change each import and each renamed name. The types are the same.

| Old import | New import |
| --- | --- |
| `@ratel-ai/cloud-sdk` | `@ratel-ai/sdk/platform` |
| `@ratel-ai/cloud-sdk/runtime` | `@ratel-ai/sdk/platform` |
| `@ratel-ai/cloud-sdk/otel` | `@ratel-ai/sdk/otel` |
| `@ratel-ai/cloud-sdk/testing` | `@ratel-ai/sdk/platform/testing` |

| Old name | New name |
| --- | --- |
| `RatelCloudSdk` | `RatelPlatformClient` |
| `CloudSdkError`, `CloudSdkErrorCode` | `PlatformError`, `PlatformErrorCode` |
| `CloudSdkOptions` | `PlatformClientOptions` |
| `CloudSdkLogEvent` | `PlatformLogEvent` |
| `CloudSkill`, `CloudSuggestion` | `PlatformSkill`, `PlatformSuggestion` |
| `ratelCloud`, `RatelCloud`, `RatelCloudOptions` | `ratelPlatform`, `RatelPlatform`, `RatelPlatformOptions` |
| `CloudDefinitionsRuntimeAttachment` | `PlatformDefinitionsRuntimeAttachment` |
| `MockCloud`, `MockCloudOptions` | `MockPlatform`, `MockPlatformOptions` |
| `RATEL_CLOUD_EVENTS`, `RATEL_CLOUD_INTENT_GRAPH` | `RATEL_PLATFORM_EVENTS`, `RATEL_PLATFORM_INTENT_GRAPH` (the old names still work) |

The default API root is now `https://app.ratel.sh/api/v1`. Set `RATEL_BASE_URL` to use a
self-hosted platform.

```ts
// Before
import { RatelCloudSdk } from "@ratel-ai/cloud-sdk";
import { attach, ratelCloud } from "@ratel-ai/cloud-sdk/runtime";
import { RatelSpanProcessor } from "@ratel-ai/cloud-sdk/otel";
import { MockCloud } from "@ratel-ai/cloud-sdk/testing";

// After
import { attach, RatelPlatformClient, ratelPlatform } from "@ratel-ai/sdk/platform";
import { RatelSpanProcessor } from "@ratel-ai/sdk/otel";
import { MockPlatform } from "@ratel-ai/sdk/platform/testing";
```

When you create the runtime with `ratel()`, you can replace the separate calls with one option.
`ratel({ platform })` publishes events and snapshots, and `r.platform` holds the management
clients and the Tool Picker:

```ts
// Before
const r = ratel();
const attachment = attach(r, { apiKey: process.env.RATEL_API_KEY });
const sdk = new RatelCloudSdk({ apiKey: process.env.RATEL_API_KEY! });

// After
const r = ratel({ platform: true }); // reads RATEL_API_KEY and RATEL_BASE_URL
await r.platform.skills.list();
await r.platform.close(); // replaces attachment.close()
```

`@ratel-ai/sdk/otel` keeps the OpenTelemetry exporter packages as optional peers, as this package
did. `@ratel-ai/sdk/platform`, `@ratel-ai/sdk/otel` and `@ratel-ai/sdk/platform/testing` do not
load the native addon.

The reference documentation is in the
[Ratel Platform section of the `@ratel-ai/sdk` README](../../sdk/ts/README.md#ratel-platform). The
examples are in [`examples/platform/`](../../../examples/platform/README.md).

## Development

`src/` holds the four re-export entry points and `exports.test.ts`. The test compares the names
that each entry point exports with the list that the package exported before the move. To run
the platform examples through this shim against MockPlatform:

```sh
pnpm --filter @ratel-ai/telemetry build && pnpm --filter "@ratel-ai/cloud-sdk..." build
pnpm --filter @ratel-ai/cloud-sdk example:shim-e2e
```

## License

MIT © Agentified
