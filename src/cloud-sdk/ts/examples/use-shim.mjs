// Runs an example of examples/platform through this deprecated shim.
//
//   node --import ./examples/use-shim.mjs ../../../examples/platform/e2e.mjs
//
// The examples import `@ratel-ai/sdk/platform` and `@ratel-ai/sdk/platform/testing`. These
// hooks send those imports, from examples/platform only, to the old names of
// `@ratel-ai/cloud-sdk` (root and /runtime) and `@ratel-ai/cloud-sdk/testing`. Build this
// package first.

import { register } from "node:module";

register("./shim-hooks.mjs", import.meta.url);
console.log("[shim] @ratel-ai/sdk/platform → @ratel-ai/cloud-sdk (root + /runtime, /testing)");
