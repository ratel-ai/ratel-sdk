# @ratel-ai/cloud-sdk (deprecated)

The Ratel Cloud client moved into [`@ratel-ai/sdk`](../../sdk/ts/README.md#ratel-cloud). This
package is a shim: it re-exports the same names under its old entry points, so current imports
continue to work. It holds no Cloud logic. New code imports `@ratel-ai/sdk` directly.

```sh
npm install @ratel-ai/sdk
```

## Migration

Change each import. The names and their types are the same.

| Old import | New import |
| --- | --- |
| `@ratel-ai/cloud-sdk` | `@ratel-ai/sdk/cloud` |
| `@ratel-ai/cloud-sdk/runtime` | `@ratel-ai/sdk/cloud` |
| `@ratel-ai/cloud-sdk/otel` | `@ratel-ai/sdk/otel` |
| `@ratel-ai/cloud-sdk/testing` | `@ratel-ai/sdk/cloud/testing` |

```ts
// Before
import { RatelCloudSdk } from "@ratel-ai/cloud-sdk";
import { attach, ratelCloud } from "@ratel-ai/cloud-sdk/runtime";
import { RatelSpanProcessor } from "@ratel-ai/cloud-sdk/otel";
import { MockCloud } from "@ratel-ai/cloud-sdk/testing";

// After
import { attach, RatelCloudSdk, ratelCloud } from "@ratel-ai/sdk/cloud";
import { RatelSpanProcessor } from "@ratel-ai/sdk/otel";
import { MockCloud } from "@ratel-ai/sdk/cloud/testing";
```

`@ratel-ai/sdk/otel` keeps the OpenTelemetry exporter packages as optional peers, as this package
did. `@ratel-ai/sdk/cloud`, `@ratel-ai/sdk/otel` and `@ratel-ai/sdk/cloud/testing` do not load the
native addon.

The reference documentation is in the
[Ratel Cloud section of the `@ratel-ai/sdk` README](../../sdk/ts/README.md#ratel-cloud). The
examples are in [`examples/cloud/`](../../../examples/cloud/README.md).

## Development

`src/` holds the four re-export entry points and `exports.test.ts`. The test compares the names
that each entry point exports with the list that the package exported before the move. To run
the Cloud examples through this shim against MockCloud:

```sh
pnpm --filter @ratel-ai/telemetry build && pnpm --filter "@ratel-ai/cloud-sdk..." build
pnpm --filter @ratel-ai/cloud-sdk example:shim-e2e
```

## License

MIT © Agentified
