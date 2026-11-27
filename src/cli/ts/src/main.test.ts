import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { tool } from "@ratel-ai/sdk/serve";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { main } from "./main.js";
import { FakeDocker, FakePlatform } from "./test-support/fakes.js";

const HTTP_YAML = `name: support-agent
input:
  example: { message: "Where is order 42?" }
params:
  model: { type: enum, options: [sonnet, haiku], default: sonnet }
  tone: { type: string, default: friendly }
secrets: [ANTHROPIC_API_KEY]
tools:
  lookup_order: { mode: live }
  refund_order: { mode: simulated }
network:
  allow: [api.anthropic.com]
`;

const COMMAND_YAML = `name: command-agent
run: { mode: command, command: [node, agent.mjs] }
input:
  example: { text: hi }
`;

let docker: FakeDocker;
let platform: FakePlatform;
let out: string;
let err: string;

beforeEach(async () => {
  docker = new FakeDocker();
  platform = await new FakePlatform().start();
  out = "";
  err = "";
});

afterEach(() => platform.close());

function project(yaml: string): string {
  const dir = mkdtempSync(join(tmpdir(), "ratel-cli-"));
  writeFileSync(join(dir, "ratel.yaml"), yaml);
  writeFileSync(join(dir, "Dockerfile"), "FROM node:24-slim\n");
  return join(dir, "ratel.yaml");
}

function ratel(argv: string[], env: Record<string, string | undefined> = {}) {
  return main(argv, {
    env: { RATEL_API_KEY: "rtl_test", RATEL_BASE_URL: `${platform.url}/api/v1`, ...env },
    stdout: (text) => {
      out += text;
    },
    stderr: (text) => {
      err += text;
    },
    docker,
    sleep: async () => undefined,
  });
}

describe("ratel push", () => {
  it("builds, saves, uploads, waits for ready and prints the version, the smoke result and a link", async () => {
    const file = project(HTTP_YAML);
    const code = await ratel(["push", "-f", file]);

    expect(err).not.toContain("ratel: ");
    expect(code).toBe(0);
    const build = docker.calls.find((c) => c[0] === "build");
    expect(build).toEqual([
      "build",
      "--platform",
      "linux/amd64",
      "-f",
      join(file, "..", "Dockerfile"),
      "-t",
      "ratel-local/support-agent:latest",
      join(file, ".."),
    ]);
    const created = platform.json("/api/v1/sandboxes/versions") as {
      config: { name: string; tools: Record<string, unknown> };
      image: { digest: string };
      bundle: { bytes: number };
    };
    expect(created.config.name).toBe("support-agent");
    expect(created.config.tools.refund_order).toEqual({ mode: "simulated" });
    expect(created.image.digest).toMatch(/^sha256:[0-9a-f]{64}$/);
    expect(created.bundle.bytes).toBeGreaterThan(0);
    const upload = platform.calls.find((c) => c.method === "PUT");
    expect(upload?.body.toString()).toBe("tarball of ratel-local/support-agent:latest");
    expect(upload?.headers.authorization).toBe("Bearer rtl_test");

    expect(out).toContain("support-agent: version v_1");
    expect(out).toContain("status  ready");
    expect(out).toContain('smoke   ok in 812 ms: {"reply":"hello"}');
    expect(out).toContain(`${platform.url}/sandboxes/support-agent/versions/v_1`);
  });

  it("--json prints one JSON document", async () => {
    const code = await ratel(["push", "-f", project(HTTP_YAML), "--json"]);
    expect(code).toBe(0);
    const doc = JSON.parse(out);
    expect(doc).toMatchObject({
      versionId: "v_1",
      name: "support-agent",
      status: "ready",
      smoke: { ok: true, output: { reply: "hello" } },
      url: `${platform.url}/sandboxes/support-agent/versions/v_1`,
    });
    expect(doc.image.digest).toMatch(/^sha256:/);
    expect(out.trim().split("\n")).toHaveLength(1);
  });

  it("takes an existing image with --image and does not build", async () => {
    const code = await ratel(["push", "-f", project(HTTP_YAML), "--image", "ghcr.io/acme/agent:1"]);
    expect(code).toBe(0);
    expect(docker.calls.some((c) => c[0] === "build")).toBe(false);
    expect(docker.calls).toContainEqual(["save", "ghcr.io/acme/agent:1"]);
  });

  it("a failed smoke check prints the container logs and the exact error, exit 1", async () => {
    platform.versionStatuses = [
      {
        status: "failed",
        reason: "The smoke check failed.",
        smoke: {
          ok: false,
          durationMs: 30000,
          logs: "Error: Cannot find module '/app/dist/main.js'\n",
          error: { code: "no_health", message: "GET /ratel/health did not answer 200 in 30 s." },
        },
      },
    ];
    const code = await ratel(["push", "-f", project(HTTP_YAML)]);
    expect(code).toBe(1);
    expect(out).toContain("status  failed: The smoke check failed.");
    expect(out).toContain(
      "smoke   failed: no_health: GET /ratel/health did not answer 200 in 30 s.",
    );
    expect(out).toContain("container logs:\n  Error: Cannot find module '/app/dist/main.js'");
  });

  it("refuses an invalid ratel.yaml before a build, with the path of the wrong field, exit 2", async () => {
    const file = project("name: Support Agent\ninput:\n  example: 1\n");
    const code = await ratel(["push", "-f", file]);
    expect(code).toBe(2);
    expect(err).toContain(`${file}:1:1: /name must match`);
    expect(docker.calls).toEqual([]);
    expect(platform.calls).toEqual([]);
  });

  it("a failed build names the error and sends nothing, exit 1", async () => {
    docker.buildFails = true;
    const code = await ratel(["push", "-f", project(HTTP_YAML)]);
    expect(code).toBe(1);
    expect(err).toContain("ratel: docker build failed: failed to solve: no Dockerfile");
    expect(platform.calls).toEqual([]);
  });

  it("says that Docker is not installed, exit 4", async () => {
    docker.installed = false;
    const code = await ratel(["push", "-f", project(HTTP_YAML)]);
    expect(code).toBe(4);
    expect(err).toContain("Docker is not installed: the docker command is not in PATH.");
  });

  it("says that Docker is not running, exit 4", async () => {
    docker.running = false;
    const code = await ratel(["push", "-f", project(HTTP_YAML), "--json"]);
    expect(code).toBe(4);
    expect(JSON.parse(out)).toMatchObject({ error: "docker_unavailable" });
    expect(err).toContain("Docker is not running.");
  });

  it("refuses before a build when RATEL_API_KEY is not set, exit 2", async () => {
    const code = await ratel(["push", "-f", project(HTTP_YAML)], { RATEL_API_KEY: undefined });
    expect(code).toBe(2);
    expect(err).toContain("RATEL_API_KEY");
    expect(docker.calls.some((c) => c[0] === "build")).toBe(false);
  });

  it("prints the refusal of Ratel, exit 3", async () => {
    platform.refuseCreate = {
      status: 429,
      body: {
        error: "limit_reached",
        message: "The limit sandbox_versions_per_day (2) is reached.",
        limit: "sandbox_versions_per_day",
        max: 2,
        current: 2,
      },
    };
    const code = await ratel(["push", "-f", project(HTTP_YAML), "--json"]);
    expect(code).toBe(3);
    expect(JSON.parse(out)).toMatchObject({
      error: "limit_reached",
      limit: "sandbox_versions_per_day",
    });
    expect(err).toContain("The limit sandbox_versions_per_day (2) is reached.");
  });

  it("--ci sends the pull request, its base and the diff from a GitHub Actions job", async () => {
    const file = project(HTTP_YAML);
    const dir = join(file, "..");
    const git = (...args: string[]) =>
      execFileSync("git", ["-c", "user.name=t", "-c", "user.email=t@t", ...args], {
        cwd: dir,
        encoding: "utf8",
      }).trim();
    git("init", "-q");
    git("add", ".");
    git("commit", "-q", "-m", "first");
    const base = git("rev-parse", "HEAD");
    writeFileSync(join(dir, "tools.ts"), "export const refundOrder = 1;\n");
    git("add", ".");
    git("commit", "-q", "-m", "second");
    const head = git("rev-parse", "HEAD");
    const event = join(dir, "..", `event-${Date.now()}.json`);
    writeFileSync(
      event,
      JSON.stringify({
        number: 12,
        pull_request: {
          title: "Refund once",
          user: { login: "octo" },
          head: { sha: head, ref: "fix/refund-once" },
          base: { sha: base, ref: "main" },
        },
      }),
    );

    const code = await ratel(["push", "-f", file, "--ci", "--json"], {
      GITHUB_ACTIONS: "true",
      GITHUB_EVENT_NAME: "pull_request",
      GITHUB_EVENT_PATH: event,
      GITHUB_REPOSITORY: "acme/support-agent",
      GITHUB_REPOSITORY_ID: "4242",
      GITHUB_HEAD_REF: "fix/refund-once",
      GITHUB_REF_NAME: "12/merge",
    });

    expect(err).not.toContain("ratel: ");
    expect(code).toBe(0);
    const created = platform.json("/api/v1/sandboxes/versions") as { git: Record<string, unknown> };
    expect(created.git).toMatchObject({
      commit: head,
      dirty: false,
      repository: { id: 4242, fullName: "acme/support-agent" },
      branch: "fix/refund-once",
      pullRequest: 12,
      baseCommit: base,
      title: "Refund once",
      author: "octo",
    });
    expect(created.git.diff).toContain("+export const refundOrder = 1;");
    // The printed result names the pull request, and never carries the diff.
    const doc = JSON.parse(out);
    expect(doc.git).toMatchObject({ branch: "fix/refund-once", pullRequest: 12 });
    expect(doc.git).not.toHaveProperty("diff");
  });

  it("--ci outside GitHub Actions is a usage error, and nothing is built", async () => {
    const code = await ratel(["push", "-f", project(HTTP_YAML), "--ci"]);
    expect(code).toBe(2);
    expect(err).toContain("GitHub Actions");
    expect(docker.calls.some((c) => c[0] === "build")).toBe(false);
  });

  it("never sends the key to an upload on another origin", async () => {
    platform.uploadOrigin = platform.url.replace("127.0.0.1", "localhost");
    await ratel(["push", "-f", project(HTTP_YAML)]);
    const upload = platform.calls.find((c) => c.method === "PUT");
    expect(upload?.headers.authorization).toBeUndefined();
  });
});

describe("ratel run --local (HTTP mode)", () => {
  it("starts the image, sends one request per the contract and prints the output", async () => {
    const code = await ratel(["run", "--local", "-f", project(HTTP_YAML), "--param", "tone=dry"], {
      ANTHROPIC_API_KEY: "sk-test-value",
    });
    expect(err).not.toContain("ratel: ");
    expect(code).toBe(0);
    expect(out).toContain('"echo": {\n    "message": "Where is order 42?"\n  }');
    const run = docker.calls.find((c) => c[0] === "run");
    expect(run).toEqual(expect.arrayContaining(["-d", "-p", "127.0.0.1::8080", "-e", "PORT=8080"]));
    expect(run).toEqual(
      expect.arrayContaining([
        "-e",
        "ANTHROPIC_API_KEY",
        "-e",
        "RATEL_SECRET_NAMES=ANTHROPIC_API_KEY",
      ]),
    );
    expect(run?.join(" ")).not.toContain("sk-test-value");
    expect(docker.calls.some((c) => c[0] === "rm")).toBe(true);
  });

  it("--json prints the request and the response in one document", async () => {
    const code = await ratel([
      "run",
      "--local",
      "-f",
      project(HTTP_YAML),
      "--input",
      '{"message":"hi"}',
      "--json",
    ]);
    expect(code).toBe(0);
    const doc = JSON.parse(out);
    expect(doc.mode).toBe("http");
    expect(doc.request).toMatchObject({
      v: 1,
      input: { message: "hi" },
      params: { model: "sonnet", tone: "friendly" },
      tools: { lookup_order: { mode: "live" }, refund_order: { mode: "simulated" } },
      fixtures: [],
    });
    expect(doc.request.runId).toMatch(/^local-/);
    expect(doc.response).toEqual({ output: { echo: { message: "hi" } }, toolCalls: [] });
  });

  it("prints the tool calls with their modes", async () => {
    const refund = tool<{ id: string }, Record<string, boolean>>("refund_order", {
      execute: async () => ({ real: true }),
      fake: async () => ({ fake: true }),
    });
    docker.agent = async () => refund({ id: "42" });
    const code = await ratel(["run", "--local", "-f", project(HTTP_YAML)]);
    expect(code).toBe(0);
    expect(out).toContain('refund_order (simulated) {"id":"42"} -> {"fake":true}');
  });

  it("an agent error answers exit 1 with the error and the container logs", async () => {
    docker.agent = async () => {
      throw new Error("model is down");
    };
    const code = await ratel(["run", "--local", "-f", project(HTTP_YAML)]);
    expect(code).toBe(1);
    expect(err).toContain("run_failed: model is down");
    expect(err).toContain("container logs:\n  agent started");
  });

  it("a contract error names what was wrong and prints the container logs", async () => {
    docker.raw = (_req, res) => {
      if (_req.url === "/ratel/health") {
        res.writeHead(200);
        return res.end("{}");
      }
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ reply: "no output field" }));
    };
    const code = await ratel(["run", "--local", "-f", project(HTTP_YAML), "--json"]);
    expect(code).toBe(1);
    const doc = JSON.parse(out);
    expect(doc.error).toEqual({
      code: "invalid_response",
      message: "POST /ratel/run answered 200 and /output is required",
    });
    expect(doc.logs).toBe("agent started\n");
  });

  it("a container that never answers health fails with no_health", async () => {
    docker.agent = null;
    const code = await ratel(["run", "--local", "-f", project(HTTP_YAML)], {
      RATEL_HEALTH_TIMEOUT_MS: "50",
    });
    expect(code).toBe(1);
    expect(err).toContain("contract error: no_health: GET /ratel/health did not answer 200");
  });

  it("refuses an undeclared parameter before Docker starts, exit 2", async () => {
    const code = await ratel([
      "run",
      "--local",
      "-f",
      project(HTTP_YAML),
      "--param",
      "temperature=1",
    ]);
    expect(code).toBe(2);
    expect(docker.calls).toEqual([]);
  });
});

describe("ratel run --local (command mode)", () => {
  it("writes the request on stdin and prints the output, with no SDK in the image", async () => {
    const code = await ratel(["run", "--local", "-f", project(COMMAND_YAML), "--json"]);
    expect(code).toBe(0);
    const run = docker.calls.find((c) => c[0] === "run");
    expect(run).toEqual(expect.arrayContaining(["-i", "--rm", "--entrypoint", "node"]));
    expect(run?.slice(-2)).toEqual(["ratel-local/command-agent:latest", "agent.mjs"]);
    const doc = JSON.parse(out);
    expect(doc.mode).toBe("command");
    expect(doc.response).toEqual({ output: { got: { text: "hi" } } });
    expect(doc.logs).toBe("command log\n");
  });

  it("takes text on stdout as the output", async () => {
    docker.command = () => ({ code: 0, stdout: "plain answer\n", stderr: "" });
    const code = await ratel(["run", "--local", "-f", project(COMMAND_YAML), "--json"]);
    expect(code).toBe(0);
    expect(JSON.parse(out).response).toEqual({ output: "plain answer" });
  });

  it("a command that exits with an error fails with its stderr as logs", async () => {
    docker.command = () => ({ code: 3, stdout: "", stderr: "Traceback: boom\n" });
    const code = await ratel(["run", "--local", "-f", project(COMMAND_YAML)]);
    expect(code).toBe(1);
    expect(err).toContain("run_failed: The command exited with code 3.");
    expect(err).toContain("container logs:\n  Traceback: boom");
  });
});

describe("ratel run (on Ratel)", () => {
  it("starts a run of the newest ready version and prints the output", async () => {
    const code = await ratel([
      "run",
      "-f",
      project(HTTP_YAML),
      "--input",
      "hi",
      "--param",
      "model=haiku",
    ]);
    expect(code).toBe(0);
    expect(platform.json("/api/v1/sandbox-runs")).toEqual({
      name: "support-agent",
      input: "hi",
      params: { model: "haiku", tone: "friendly" },
      tools: { lookup_order: { mode: "live" }, refund_order: { mode: "simulated" } },
    });
    expect(out).toContain('"reply": "hi"');
    expect(out).toContain(`${platform.url}/sandboxes/runs/r_1`);
    expect(docker.calls).toEqual([]);
  });

  it("a failed run prints the error and the logs, exit 1", async () => {
    platform.runStatuses = [
      {
        status: "failed",
        error: { code: "timeout", message: "The run took more than 120 s." },
        logs: "slow\n",
      },
    ];
    const code = await ratel(["run", "-f", project(HTTP_YAML), "--json"]);
    expect(code).toBe(1);
    expect(JSON.parse(out)).toMatchObject({ status: "failed", error: { code: "timeout" } });
  });
});

describe("usage", () => {
  it("prints the usage with --help, exit 0", async () => {
    expect(await ratel(["--help"])).toBe(0);
    expect(out).toContain("ratel push");
  });

  it("refuses an unknown command, exit 2", async () => {
    expect(await ratel(["deploy"])).toBe(2);
    expect(err).toContain("Unknown command: deploy");
  });
});
