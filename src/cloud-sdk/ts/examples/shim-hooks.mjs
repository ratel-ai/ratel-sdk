// Module resolution hooks for use-shim.mjs. Only imports from examples/platform are
// redirected: the shim itself imports `@ratel-ai/sdk/platform`, and must still get it.

const SHIM_ENTRIES = {
  "@ratel-ai/sdk/platform": new URL("./shim-platform.mjs", import.meta.url).href,
  "@ratel-ai/sdk/platform/testing": new URL("./shim-testing.mjs", import.meta.url).href,
};

export async function resolve(specifier, context, nextResolve) {
  const target = SHIM_ENTRIES[specifier];
  if (target === undefined || !context.parentURL?.includes("/examples/platform/")) {
    return nextResolve(specifier, context);
  }
  return nextResolve(target, { ...context, parentURL: import.meta.url });
}
