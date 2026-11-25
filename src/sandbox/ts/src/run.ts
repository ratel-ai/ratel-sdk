/** The main process of an instance: `node <this package>/dist/run.js`, in the project folder. */
import { runSandbox } from "./runner.js";

runSandbox({
  env: process.env,
  projectDir: process.cwd(),
  secretNames: (process.env.RATEL_SECRET_NAMES ?? "").split(",").filter(Boolean),
})
  .then(() => process.exit(0))
  .catch((error: unknown) => {
    process.stderr.write(`ratel: ${error instanceof Error ? error.message : String(error)}\n`);
    process.exit(1);
  });
