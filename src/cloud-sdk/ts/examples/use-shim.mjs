// Runs an example of examples/cloud through this deprecated shim.
//
//   node --import ./examples/use-shim.mjs ../../../examples/cloud/e2e.mjs
//
// The examples import `@ratel-ai/sdk/cloud` and `@ratel-ai/sdk/cloud/testing`. These
// hooks send those imports, from examples/cloud only, to `@ratel-ai/cloud-sdk` (root
// and /runtime) and `@ratel-ai/cloud-sdk/testing`. Build this package first.

import { register } from "node:module";

register("./shim-hooks.mjs", import.meta.url);
console.log("[shim] @ratel-ai/sdk/cloud → @ratel-ai/cloud-sdk (root + /runtime, /testing)");
