const TRANSFORM = "--experimental-transform-types";

/**
 * The Node flags for the host's TypeScript. Node 22 to 25 can also transform an enum or a parameter
 * property. A later Node has no such flag and only removes the types.
 */
export function typeScriptFlags(
  allowed: ReadonlySet<string> = process.allowedNodeEnvironmentFlags,
): string[] {
  return allowed.has(TRANSFORM) ? [TRANSFORM, "--disable-warning=ExperimentalWarning"] : [];
}
