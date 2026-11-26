// Module resolution hooks for use-shim.mjs. Only imports from examples/cloud are
// redirected: the shim itself imports `@ratel-ai/sdk/cloud`, and must still get it.

const SHIM_ENTRIES = {
  "@ratel-ai/sdk/cloud": new URL("./shim-cloud.mjs", import.meta.url).href,
  "@ratel-ai/sdk/cloud/testing": "@ratel-ai/cloud-sdk/testing",
};

export async function resolve(specifier, context, nextResolve) {
  const target = SHIM_ENTRIES[specifier];
  if (target === undefined || !context.parentURL?.includes("/examples/cloud/")) {
    return nextResolve(specifier, context);
  }
  return nextResolve(target, { ...context, parentURL: import.meta.url });
}
