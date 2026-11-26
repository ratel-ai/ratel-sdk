/**
 * `@ratel-ai/cloud-sdk/testing` is deprecated. Import `@ratel-ai/sdk/platform/testing` instead.
 *
 * This entry point re-exports the names it exported before the platform code moved into
 * `@ratel-ai/sdk`. Each old Cloud name is an alias of its new Platform name. It holds
 * no logic.
 *
 * @deprecated Import `@ratel-ai/sdk/platform/testing`.
 */

export {
  MockPlatform as MockCloud,
  type MockPlatformOptions as MockCloudOptions,
} from "@ratel-ai/sdk/platform/testing";
