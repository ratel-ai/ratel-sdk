import { execFileSync } from "node:child_process";
import { readdirSync, realpathSync, statSync } from "node:fs";
import { dirname, join, sep } from "node:path";
import { fileURLToPath } from "node:url";
import type { TarFile } from "./tar.js";

/** The folder of the runner in a bundle. */
export const RUNNER_DIR = ".ratel-sandbox-runner";

export type GitState = { commit: string; dirty: boolean };

/** The commit of the project folder, and whether its files differ from it. */
export function readGitState(projectDir: string): GitState {
  try {
    const commit = git(projectDir, ["rev-parse", "--verify", "HEAD"]).trim();
    return {
      commit,
      dirty: git(projectDir, ["status", "--porcelain", "--untracked-files=all", "--", "."]) !== "",
    };
  } catch {
    throw new Error(
      "The project folder is not in a git repository with a commit. A push records the commit.",
    );
  }
}

/**
 * The files of a push: the files that git lists (tracked, and untracked that are not ignored),
 * never `.env*` files or `node_modules`, and the runner of this package.
 */
export function listProjectFiles(projectDir: string): TarFile[] {
  const listed = git(projectDir, ["ls-files", "-z", "--cached", "--others", "--exclude-standard"]);
  const names = [...new Set(listed.split("\0").filter(Boolean))].filter(isBundled).sort();
  const root = realpathSync(projectDir);
  const known = new Set(names);
  return [...names.flatMap((name) => projectFile(root, name, known)), ...runnerFiles()];
}

/** Whether a listed file goes in the bundle. */
export function isBundled(name: string): boolean {
  const parts = name.split("/");
  return (
    parts[0] !== RUNNER_DIR &&
    !parts.some((part) => part === "node_modules" || part === ".git" || part.startsWith(".env"))
  );
}

/**
 * A link becomes the file that it names, when that file is in the bundle too. A parent folder can
 * also be a link. A deleted file is not there.
 */
function projectFile(root: string, name: string, known: ReadonlySet<string>): TarFile[] {
  try {
    const target = realpathSync(join(root, name));
    if (!target.startsWith(root + sep)) {
      throw new Error(`${name} is a link to a file outside the project folder. Remove the link.`);
    }
    const real = target
      .slice(root.length + 1)
      .split(sep)
      .join("/");
    if (!isBundled(real)) return [];
    const stat = statSync(target);
    // A folder here is a link to a folder or a git submodule: git lists its files elsewhere.
    if (!stat.isFile()) return [];
    if (!known.has(real)) {
      throw new Error(
        `${name} is a link to a file that git does not list. Remove the link, or add the file to git.`,
      );
    }
    return [{ name, path: target, bytes: stat.size, executable: (stat.mode & 0o111) !== 0 }];
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw error;
  }
}

function runnerFiles(): TarFile[] {
  const dist = dirname(fileURLToPath(import.meta.url));
  const code = readdirSync(dist)
    .filter((file) => file.endsWith(".js"))
    .map((file) => [file, join(dist, file)] as const);
  // `package.json` of this package makes the files ES modules.
  return [["package.json", join(dist, "..", "package.json")] as const, ...code]
    .map(([file, path]) => ({
      name: `${RUNNER_DIR}/${file}`,
      path,
      bytes: statSync(path).size,
      executable: false,
    }))
    .sort((a, b) => (a.name < b.name ? -1 : 1));
}

function git(cwd: string, args: string[]): string {
  return execFileSync("git", args, {
    cwd,
    encoding: "utf8",
    maxBuffer: 256 * 1024 * 1024,
    stdio: ["ignore", "pipe", "ignore"],
  });
}
