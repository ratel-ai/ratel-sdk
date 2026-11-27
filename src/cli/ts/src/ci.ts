import { execFile } from "node:child_process";
import { readFile } from "node:fs/promises";
import { promisify } from "node:util";
import { UsageError } from "./errors.js";

const run = promisify(execFile);

/**
 * A diff over 512 KiB is left out of the push (`diffTruncated`), and Ratel then runs every eval.
 * The push API takes a JSON body of at most 1 MiB, so the diff leaves room for `ratel.yaml`.
 */
export const MAX_DIFF_BYTES = 524_288;

/** What `ratel push --ci` adds to `git` (protocol/sandbox/v1, create-version.request). */
export interface CiGit {
  repository: { id: number; fullName: string };
  branch: string;
  pullRequest: number | null;
  baseCommit: string | null;
  title: string;
  author: string | null;
  diff?: string;
  diffTruncated?: true;
}

export interface CiDeps {
  readFile: (path: string) => Promise<string>;
  /** Runs git in the folder and answers its stdout. */
  git: (args: string[], cwd: string) => Promise<string>;
}

export const ciDeps: CiDeps = {
  readFile: (path) => readFile(path, "utf8"),
  git: async (args, cwd) => {
    // Past the cap is too large anyway: the buffer only has to hold the cap and a little more.
    const { stdout } = await run("git", args, { cwd, maxBuffer: MAX_DIFF_BYTES + 64 * 1024 });
    return stdout;
  },
};

/** What to give actions/checkout so a pull request's job builds its head, not a merge commit. */
// biome-ignore lint/suspicious/noTemplateCurlyInString: a GitHub Actions expression, not a template.
export const HEAD_REF = "ref: ${{ github.event.pull_request.head.sha }}";

const ZERO = /^0+$/;
const SHA = /^(?:[0-9a-f]{40}|[0-9a-f]{64})$/;

interface Event {
  number?: number;
  pull_request?: {
    title?: string;
    user?: { login?: string } | null;
    head?: { sha?: string; ref?: string };
    base?: { sha?: string };
  };
  before?: string;
  head_commit?: { message?: string } | null;
  sender?: { login?: string } | null;
  repository?: { id?: number; full_name?: string };
}

/** The event file of the GitHub Actions job; `--ci` anywhere else is a usage error. */
export function requireActions(env: Record<string, string | undefined>): string {
  if (env.GITHUB_ACTIONS !== "true" || !env.GITHUB_EVENT_PATH) {
    throw new UsageError(
      "--ci reads the GitHub Actions environment: run it in a GitHub Actions job, or push without --ci.",
    );
  }
  return env.GITHUB_EVENT_PATH;
}

/**
 * The commit's place on GitHub, read in a GitHub Actions job: the repository, the branch, the pull
 * request, what the change compares against, and its diff. `commit` is the checked-out commit
 * (`git rev-parse HEAD`); for a pull request it must be the pull request's head.
 */
export async function readCiGit(
  env: Record<string, string | undefined>,
  dir: string,
  commit: string,
  deps: CiDeps = ciDeps,
): Promise<CiGit> {
  const eventPath = requireActions(env);
  const name = env.GITHUB_EVENT_NAME ?? "";
  let event: Event;
  try {
    event = JSON.parse(await deps.readFile(eventPath)) as Event;
  } catch (error) {
    throw new UsageError(`--ci could not read the event of the job: ${(error as Error).message}`);
  }

  let ci: Omit<CiGit, "repository" | "diff" | "diffTruncated">;
  if (name === "pull_request" || name === "pull_request_target") {
    const pr = event.pull_request;
    if (!pr?.head?.sha || !event.number)
      throw new UsageError(`--ci: the ${name} event has no pull request.`);
    if (pr.head.sha !== commit) {
      throw new UsageError(
        `--ci: the checked-out commit ${commit.slice(0, 7)} is not the head of pull request #${event.number} ` +
          `(${pr.head.sha.slice(0, 7)}): GitHub checks out a merge commit by default. In actions/checkout, ` +
          `set \`${HEAD_REF}\`.`,
      );
    }
    ci = {
      branch: env.GITHUB_HEAD_REF || pr.head.ref || "",
      pullRequest: event.number,
      baseCommit: pr.base?.sha && SHA.test(pr.base.sha) ? pr.base.sha : null,
      title: firstLine(pr.title ?? ""),
      author: pr.user?.login ?? null,
    };
  } else if (name === "push") {
    ci = {
      branch: env.GITHUB_REF_NAME ?? "",
      pullRequest: null,
      baseCommit:
        event.before && SHA.test(event.before) && !ZERO.test(event.before) ? event.before : null,
      title: firstLine(event.head_commit?.message ?? ""),
      author: event.sender?.login ?? null,
    };
  } else {
    throw new UsageError(
      `--ci pushes on push and pull_request events, not on ${name || "this event"}.`,
    );
  }
  if (!ci.branch) throw new UsageError("--ci: the job names no branch.");

  const id = Number(env.GITHUB_REPOSITORY_ID || event.repository?.id);
  const fullName = env.GITHUB_REPOSITORY || event.repository?.full_name || "";
  if (!Number.isInteger(id) || id < 1 || !fullName) {
    throw new UsageError(
      "--ci: the job names no repository (GITHUB_REPOSITORY_ID, GITHUB_REPOSITORY).",
    );
  }
  return {
    repository: { id, fullName },
    ...ci,
    ...(await diffOf(dir, ci.baseCommit, commit, deps)),
  };
}

/** The diff from the base to the commit; left out when too large (marked) or when git cannot make it. */
async function diffOf(
  dir: string,
  base: string | null,
  commit: string,
  deps: CiDeps,
): Promise<Pick<CiGit, "diff" | "diffTruncated">> {
  if (!base) return {};
  try {
    const diff = await deps.git(
      ["diff", "--no-color", "--no-ext-diff", `${base}...${commit}`],
      dir,
    );
    return Buffer.byteLength(diff) > MAX_DIFF_BYTES ? { diffTruncated: true } : { diff };
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ERR_CHILD_PROCESS_STDIO_MAXBUFFER") {
      return { diffTruncated: true };
    }
    // A shallow clone has no base: the change is not known, and Ratel runs every eval.
    return {};
  }
}

const firstLine = (text: string) => text.split("\n", 1)[0].trim();
