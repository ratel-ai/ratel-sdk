import { execFile } from "node:child_process";
import { promisify } from "node:util";

const run = promisify(execFile);

/** The commit of the folder, and whether it has changes that are not committed. Not a git folder: undefined. */
export async function readGit(
  dir: string,
): Promise<{ commit: string; dirty: boolean } | undefined> {
  try {
    const { stdout: commit } = await run("git", ["rev-parse", "HEAD"], { cwd: dir });
    const { stdout: changes } = await run("git", ["status", "--porcelain", "--", "."], {
      cwd: dir,
    });
    return { commit: commit.trim(), dirty: changes.trim().length > 0 };
  } catch {
    return undefined;
  }
}
