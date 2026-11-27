import { describe, expect, it } from "vitest";
import { type CiDeps, HEAD_REF, MAX_DIFF_BYTES, readCiGit } from "./ci.js";
import { UsageError } from "./errors.js";

const HEAD = "a".repeat(40);
const BASE = "b".repeat(40);
const DIFF = "diff --git a/src/tools/refund_order.ts b/src/tools/refund_order.ts\n";

const PR_EVENT = {
  number: 12,
  pull_request: {
    title: "Never refund an order twice",
    user: { login: "octo" },
    head: { sha: HEAD, ref: "fix/refund-once" },
    base: { sha: BASE, ref: "main" },
  },
  repository: { id: 4242, full_name: "acme/support-agent" },
};

const PUSH_EVENT = {
  ref: "refs/heads/main",
  before: BASE,
  after: HEAD,
  head_commit: { message: "Refund once\n\nThe tool checks refundedAt." },
  pusher: { name: "octo" },
  sender: { login: "octo" },
  repository: { id: 4242, full_name: "acme/support-agent" },
};

function actions(event: string, extra: Record<string, string> = {}) {
  return {
    GITHUB_ACTIONS: "true",
    GITHUB_EVENT_NAME: event,
    GITHUB_EVENT_PATH: "/github/workflow/event.json",
    GITHUB_REPOSITORY: "acme/support-agent",
    GITHUB_REPOSITORY_ID: "4242",
    GITHUB_REF_NAME: event === "push" ? "main" : "12/merge",
    GITHUB_HEAD_REF: event === "push" ? "" : "fix/refund-once",
    ...extra,
  };
}

function fakes(payload: unknown, diff: string | Error = DIFF) {
  const git: string[][] = [];
  const deps: CiDeps = {
    readFile: async (path) => {
      expect(path).toBe("/github/workflow/event.json");
      return JSON.stringify(payload);
    },
    git: async (args) => {
      git.push(args);
      if (args[0] === "diff") {
        if (diff instanceof Error) throw diff;
        return diff;
      }
      throw new Error(`unexpected git ${args.join(" ")}`);
    },
  };
  return { deps, git };
}

describe("readCiGit", () => {
  it("reads a pull request: its number, title, author, head branch, base and diff", async () => {
    const { deps, git } = fakes(PR_EVENT);
    const ci = await readCiGit(actions("pull_request"), "/repo", HEAD, deps);
    expect(ci).toEqual({
      repository: { id: 4242, fullName: "acme/support-agent" },
      branch: "fix/refund-once",
      pullRequest: 12,
      baseCommit: BASE,
      title: "Never refund an order twice",
      author: "octo",
      diff: DIFF,
    });
    expect(git).toEqual([["diff", "--no-color", "--no-ext-diff", `${BASE}...${HEAD}`]]);
  });

  it("reads a push: its branch, its previous head as the base, the first line of the message", async () => {
    const { deps } = fakes(PUSH_EVENT);
    const ci = await readCiGit(actions("push"), "/repo", HEAD, deps);
    expect(ci).toMatchObject({
      branch: "main",
      pullRequest: null,
      baseCommit: BASE,
      title: "Refund once",
      author: "octo",
      diff: DIFF,
    });
  });

  it("a new branch has no base and no diff", async () => {
    const { deps, git } = fakes({ ...PUSH_EVENT, before: "0".repeat(40) });
    const ci = await readCiGit(actions("push"), "/repo", HEAD, deps);
    expect(ci.baseCommit).toBeNull();
    expect(ci).not.toHaveProperty("diff");
    expect(ci).not.toHaveProperty("diffTruncated");
    expect(git).toEqual([]);
  });

  it("a diff over 512 KiB is left out and marked", async () => {
    const { deps } = fakes(PR_EVENT, "x".repeat(MAX_DIFF_BYTES + 1));
    const ci = await readCiGit(actions("pull_request"), "/repo", HEAD, deps);
    expect(ci).not.toHaveProperty("diff");
    expect(ci.diffTruncated).toBe(true);
  });

  it("a diff git cannot hold in its buffer counts as too large", async () => {
    const overflow = Object.assign(new Error("stdout maxBuffer length exceeded"), {
      code: "ERR_CHILD_PROCESS_STDIO_MAXBUFFER",
    });
    const { deps } = fakes(PR_EVENT, overflow);
    const ci = await readCiGit(actions("pull_request"), "/repo", HEAD, deps);
    expect(ci.diffTruncated).toBe(true);
  });

  it("a diff git cannot make (a shallow clone) is left out, unmarked: the change is not known", async () => {
    const { deps } = fakes(PR_EVENT, new Error("fatal: bad revision"));
    const ci = await readCiGit(actions("pull_request"), "/repo", HEAD, deps);
    expect(ci).not.toHaveProperty("diff");
    expect(ci).not.toHaveProperty("diffTruncated");
  });

  it("refuses the merge commit a pull request checks out by default", async () => {
    const { deps } = fakes(PR_EVENT);
    const merge = "c".repeat(40);
    const error = await readCiGit(actions("pull_request"), "/repo", merge, deps).catch((e) => e);
    expect(error).toBeInstanceOf(UsageError);
    expect(error.message).toContain(HEAD_REF);
  });

  it("refuses --ci outside GitHub Actions", async () => {
    const { deps } = fakes(PUSH_EVENT);
    const error = await readCiGit({}, "/repo", HEAD, deps).catch((e) => e);
    expect(error).toBeInstanceOf(UsageError);
    expect(error.message).toContain("GitHub Actions");
  });

  it("refuses an event that names no commit to push", async () => {
    const { deps } = fakes({});
    const error = await readCiGit(actions("schedule"), "/repo", HEAD, deps).catch((e) => e);
    expect(error).toBeInstanceOf(UsageError);
    expect(error.message).toContain("schedule");
  });

  it("takes the repository from the event when the environment has none", async () => {
    const { deps } = fakes(PUSH_EVENT);
    const env = actions("push", { GITHUB_REPOSITORY: "", GITHUB_REPOSITORY_ID: "" });
    const ci = await readCiGit(env, "/repo", HEAD, deps);
    expect(ci.repository).toEqual({ id: 4242, fullName: "acme/support-agent" });
  });
});
