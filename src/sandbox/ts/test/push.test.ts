import { execFileSync, spawn } from "node:child_process";
import { createHash } from "node:crypto";
import {
  chmodSync,
  cpSync,
  mkdirSync,
  mkdtempSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { createServer, type Server } from "node:http";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

const CLI = fileURLToPath(new URL("../dist/cli.js", import.meta.url));
const FIXTURE = fileURLToPath(new URL("./fixtures/support-agent", import.meta.url));
const VERSION_ID = "3f2c1a52-5f0d-4c6e-9a55-0c9d7f1f7e11";

type Call = { method: string; path: string; auth: string | undefined; body: Buffer };
let server: Server;
let url: string;
let calls: Call[];
let refuse: { path: string; status: number; body: unknown } | null;
let authorize: boolean;
let uploadUrl: string | undefined;
let project: string;

beforeEach(async () => {
  calls = [];
  refuse = null;
  authorize = true;
  uploadUrl = undefined;
  server = createServer((req, res) => {
    const parts: Buffer[] = [];
    req.on("data", (chunk) => parts.push(chunk));
    req.on("end", () => {
      const path = req.url ?? "";
      const auth = req.headers.authorization;
      calls.push({ method: req.method ?? "", path, auth, body: Buffer.concat(parts) });
      const answer = (status: number, body: unknown) => {
        res.statusCode = status;
        res.end(JSON.stringify(body));
      };
      if (refuse && path === refuse.path) return answer(refuse.status, refuse.body);
      if (path === "/api/v1/sandboxes/versions") {
        const upload = {
          method: "PUT",
          url: uploadUrl ?? `${url}/upload?sig=1`,
          headers: {},
          authorize,
        };
        return answer(201, { versionId: VERSION_ID, upload });
      }
      return answer(path.startsWith("/upload") ? 200 : 202, { status: "uploaded", reason: null });
    });
  });
  await new Promise<void>((done) => server.listen(0, "127.0.0.1", done));
  url = `http://127.0.0.1:${(server.address() as { port: number }).port}`;

  project = mkdtempSync(join(tmpdir(), "push-"));
  cpSync(FIXTURE, project, { recursive: true });
  git("init", "-q");
  git("add", "-A");
  git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "first");
});
afterEach(() => new Promise((done) => server.close(done)));

const git = (...args: string[]) => execFileSync("git", args, { cwd: project, encoding: "utf8" });

function ratel(args: string[], env: Record<string, string | undefined> = {}) {
  return new Promise<{ code: number | null; out: string; err: string }>((done) => {
    const child = spawn(process.execPath, [CLI, ...args], {
      cwd: project,
      env: { PATH: process.env.PATH, RATEL_URL: url, RATEL_API_KEY: "rtl_key", ...env },
    });
    let out = "";
    let err = "";
    child.stdout.on("data", (chunk) => {
      out += chunk;
    });
    child.stderr.on("data", (chunk) => {
      err += chunk;
    });
    child.on("close", (code) => done({ code, out, err }));
  });
}

function bundleNames(): string[] {
  const folder = mkdtempSync(join(tmpdir(), "bundle-"));
  writeFileSync(join(folder, "b.tgz"), calls[1]?.body ?? "");
  return execFileSync("tar", ["-tzf", join(folder, "b.tgz")], { encoding: "utf8" })
    .trim()
    .split("\n");
}

describe("ratel sandbox push", () => {
  it("creates, uploads and finalizes, then prints the version", async () => {
    const { code, out } = await ratel(["sandbox", "push"]);
    expect(code).toBe(0);
    expect(out).toContain(VERSION_ID);
    expect(calls.map(({ method, path, auth }) => [method, path, auth])).toEqual([
      ["POST", "/api/v1/sandboxes/versions", "Bearer rtl_key"],
      ["PUT", "/upload?sig=1", "Bearer rtl_key"],
      ["POST", `/api/v1/sandboxes/versions/${VERSION_ID}/finalize`, "Bearer rtl_key"],
    ]);
    const request = JSON.parse(String(calls[0]?.body));
    expect(request.manifest.name).toBe("support-agent");
    expect(request.git).toEqual({ commit: git("rev-parse", "HEAD").trim(), dirty: false });
    const sent = calls[1]?.body ?? Buffer.alloc(0);
    expect(request.bundle).toEqual({
      sha256: createHash("sha256").update(sent).digest("hex"),
      bytes: sent.length,
    });
  });

  it("does not send the key to an upload address that is not Ratel", async () => {
    authorize = false;
    await ratel(["sandbox", "push"]);
    expect(calls[1]?.auth).toBeUndefined();
    authorize = true;
    uploadUrl = `${url.replace("127.0.0.1", "localhost")}/upload?sig=1`;
    calls = [];
    const { code } = await ratel(["sandbox", "push"]);
    expect(code).toBe(0);
    expect(calls[1]?.path).toBe("/upload?sig=1");
    expect(calls[1]?.auth).toBeUndefined();
  });

  it("bundles what git lists and the runner, never .env files, node_modules or ignored files", async () => {
    const long = `deep/${"folder/".repeat(20)}file.ts`;
    mkdirSync(join(project, long, ".."), { recursive: true });
    mkdirSync(join(project, "node_modules/x"), { recursive: true });
    writeFileSync(join(project, ".gitignore"), "ignored.txt\n");
    for (const file of [
      long,
      ".env",
      ".env.local",
      "src/.env.production",
      ".envrc",
      ".env-prod",
      "node_modules/x/a.js",
      "ignored.txt",
      "new.ts",
      "environment.ts",
    ]) {
      writeFileSync(join(project, file), "x");
    }
    git("add", "-f", ".env", "node_modules/x/a.js");
    writeFileSync(join(project, "run.sh"), "x");
    chmodSync(join(project, "run.sh"), 0o755);
    symlinkSync("new.ts", join(project, "linked.ts"));
    symlinkSync(".env", join(project, "not-env.txt"));
    const { code, out } = await ratel(["sandbox", "push", "--json"]);
    expect(code).toBe(0);
    expect(JSON.parse(out)).toMatchObject({ versionId: VERSION_ID, git: { dirty: true } });
    const names = bundleNames();
    expect(names.filter((name) => !name.startsWith(".ratel-sandbox-runner/")).sort()).toEqual(
      [
        ".gitignore",
        "environment.ts",
        "linked.ts",
        long,
        "new.ts",
        "ratel.sandbox.ts",
        "run.sh",
        "src/agent.ts",
        "src/shout.ts",
      ].sort(),
    );
    expect(names).toContain(".ratel-sandbox-runner/run.js");
    expect(names).toContain(".ratel-sandbox-runner/package.json");
  });

  it("refuses a link that leaves the project", async () => {
    symlinkSync("/etc/hosts", join(project, "hosts"));
    const { code, err } = await ratel(["sandbox", "push"]);
    expect(code).toBe(1);
    expect(err).toMatch(/hosts is a link to a file outside/);
    expect(calls).toEqual([]);
  });

  it("refuses a file that a linked parent folder moves out of the project", async () => {
    const outside = mkdtempSync(join(tmpdir(), "outside-"));
    writeFileSync(join(outside, "a.json"), "x");
    mkdirSync(join(project, "d"));
    writeFileSync(join(project, "d/a.json"), "y");
    git("add", "-A");
    rmSync(join(project, "d"), { recursive: true });
    symlinkSync(outside, join(project, "d"));
    writeFileSync(join(project, ".gitignore"), "d\n");
    const { code, err } = await ratel(["sandbox", "push"]);
    expect(code).toBe(1);
    expect(err).toMatch(/d\/a.json is a link to a file outside/);
    expect(calls).toEqual([]);
  });

  it("refuses a link to a file that git ignores", async () => {
    mkdirSync(join(project, "private"));
    writeFileSync(join(project, "private/key.json"), "x");
    writeFileSync(join(project, ".gitignore"), "private/\n");
    symlinkSync("private/key.json", join(project, "cfg.json"));
    const { code, err } = await ratel(["sandbox", "push"]);
    expect(code).toBe(1);
    expect(err).toMatch(/cfg.json is a link to a file that git does not list/);
    expect(calls).toEqual([]);
  });

  it("takes a definition with an enum, an entry with ./, and any git status setting", async () => {
    writeFileSync(
      join(project, "ratel.sandbox.ts"),
      'enum Name { One = "one" }\nexport default { name: Name.One, entry: "./src/agent.ts" };',
    );
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "second");
    git("config", "status.showUntrackedFiles", "no");
    writeFileSync(join(project, "new.ts"), "x");
    const { code, err } = await ratel(["sandbox", "push"]);
    expect(err).toBe("");
    expect(code).toBe(0);
    const { manifest, git: state } = JSON.parse(String(calls[0]?.body));
    expect(manifest).toMatchObject({ name: "one", entry: "src/agent.ts" });
    expect(state.dirty).toBe(true);
  });

  it("the same folder gives the same bundle", async () => {
    await ratel(["sandbox", "push"]);
    await ratel(["sandbox", "push"]);
    expect(calls[4]?.body.equals(calls[1]?.body ?? Buffer.alloc(0))).toBe(true);
  });

  it("exits with 1 and the text of the refusal", async () => {
    refuse = {
      path: "/api/v1/sandboxes/versions",
      status: 429,
      body: {
        error: "limit_reached",
        message: "The limit sandbox_versions_per_day (2) is reached.",
        limit: "sandbox_versions_per_day",
        max: 2,
        current: 2,
      },
    };
    const { code, out, err } = await ratel(["sandbox", "push", "--json"]);
    expect(code).toBe(1);
    expect(err).toContain("The limit sandbox_versions_per_day (2) is reached.");
    expect(JSON.parse(out)).toMatchObject({
      error: "limit_reached",
      limit: "sandbox_versions_per_day",
    });
  });

  it("exits with 1 for a definition that is not valid, and with no repository", async () => {
    const file = join(project, "ratel.sandbox.ts");
    writeFileSync(file, 'export default { name: "x", entry: "missing.ts" };');
    expect((await ratel(["sandbox", "push"])).err).toMatch(/missing.ts is not in the bundle/);
    writeFileSync(file, 'export default { name: "Bad", entry: "" };');
    expect((await ratel(["sandbox", "push"])).err).toMatch(/not valid/);
    const loose = mkdtempSync(join(tmpdir(), "loose-"));
    cpSync(FIXTURE, loose, { recursive: true });
    const { code, err } = await ratel(["sandbox", "push", loose]);
    expect(code).toBe(1);
    expect(err).toMatch(/not in a git repository/);
  });

  it("exits with 2 for a wrong command or a missing setting, and never prints the key", async () => {
    expect((await ratel(["sandbox", "pull"])).code).toBe(2);
    expect((await ratel(["sandbox", "push"], { RATEL_API_KEY: undefined })).err).toMatch(
      /RATEL_API_KEY is not set/,
    );
    const down = await ratel(["sandbox", "push"], { RATEL_URL: "http://127.0.0.1:9" });
    expect(down.code).toBe(1);
    expect(down.err).toMatch(/no answer/);
    expect(down.err + down.out).not.toContain("rtl_key");
  });
});
