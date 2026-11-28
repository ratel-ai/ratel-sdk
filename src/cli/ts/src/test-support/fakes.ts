import { createHash } from "node:crypto";
import { writeFileSync } from "node:fs";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import type { AddressInfo } from "node:net";
import { createRunHandler, type RunFn } from "@ratel-ai/sdk/serve";
import type { Docker, ExecOptions, ExecResult } from "../docker.js";

/** A fake Docker. An HTTP-mode "container" is a real server of `createRunHandler(run)`. */
export class FakeDocker implements Docker {
  calls: string[][] = [];
  envs: Record<string, string | undefined>[] = [];
  running = true;
  installed = true;
  images = new Map<string, string>([["ghcr.io/acme/agent:1", `sha256:${"e".repeat(64)}`]]);
  buildFails = false;
  /** The agent of an HTTP-mode container. `null`: the container never answers. */
  agent: RunFn | null = async ({ input }) => ({ echo: input });
  /** A raw handler instead of the agent: for contract errors. */
  raw?: (req: IncomingMessage, res: ServerResponse) => void;
  /** The command of a command-mode container: stdin to the result. */
  command: (stdin: string) => ExecResult = (stdin) => ({
    code: 0,
    stdout: `${JSON.stringify({ output: { got: JSON.parse(stdin).input } })}\n`,
    stderr: "command log\n",
  });
  logs = "agent started\n";
  private servers = new Map<string, Server>();

  async exec(args: readonly string[], options: ExecOptions = {}): Promise<ExecResult> {
    if (!this.installed) {
      const { DockerError } = await import("../errors.js");
      throw new DockerError(
        "Docker is not installed: the docker command is not in PATH. Install Docker and try again.",
      );
    }
    this.calls.push([...args]);
    const ok = (stdout = ""): ExecResult => ({ code: 0, stdout, stderr: "" });
    switch (args[0]) {
      case "info":
        return this.running
          ? ok("29.0.0\n")
          : {
              code: 1,
              stdout: "",
              stderr: "Cannot connect to the Docker daemon at unix:///var/run/docker.sock.\n",
            };
      case "build": {
        if (this.buildFails)
          return { code: 1, stdout: "", stderr: "failed to solve: no Dockerfile\n" };
        const tag = args[args.indexOf("-t") + 1];
        this.images.set(tag, `sha256:${createHash("sha256").update(tag).digest("hex")}`);
        options.progress?.("#1 building\n");
        return ok();
      }
      case "image": {
        const id = this.images.get(args.at(-1) ?? "");
        return id
          ? ok(`${id} linux/amd64\n`)
          : { code: 1, stdout: "", stderr: "Error: No such image\n" };
      }
      case "run":
        return args.includes("-d") ? this.start(args) : this.command(options.input ?? "");
      case "port": {
        const server = this.servers.get(args[1]);
        return ok(`127.0.0.1:${(server?.address() as AddressInfo | null)?.port ?? 1}\n`);
      }
      case "inspect":
        return ok("true\n");
      case "logs":
        return { code: 0, stdout: this.logs, stderr: "" };
      case "rm":
        await new Promise<void>((resolve) => {
          const server = this.servers.get(args.at(-1) ?? "");
          if (!server) return resolve();
          server.closeAllConnections();
          server.close(() => resolve());
        });
        this.servers.delete(args.at(-1) ?? "");
        return ok();
      default:
        return { code: 1, stdout: "", stderr: `fake docker: no ${args[0]}\n` };
    }
  }

  async save(ref: string, file: string) {
    this.calls.push(["save", ref]);
    const bytes = Buffer.from(`tarball of ${ref}`);
    writeFileSync(file, bytes);
    return { sha256: createHash("sha256").update(bytes).digest("hex"), bytes: bytes.length };
  }

  private async start(args: readonly string[]): Promise<ExecResult> {
    const env: Record<string, string | undefined> = {};
    args.forEach((arg, i) => {
      if (arg === "-e") {
        const [name, value] = args[i + 1].split("=");
        env[name] = value ?? process.env[name];
      }
    });
    this.envs.push(env);
    const agent = this.agent;
    const handler =
      this.raw ??
      (agent
        ? createRunHandler(agent)
        : (_req: IncomingMessage, res: ServerResponse) => {
            res.writeHead(503);
            res.end("starting");
          });
    const server = createServer(handler);
    await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
    const id = `c${this.servers.size + 1}`;
    this.servers.set(id, server);
    return { code: 0, stdout: `${id}\n`, stderr: "" };
  }
}

/** A recorded call to the fake platform. */
export interface PlatformCall {
  method: string;
  path: string;
  headers: IncomingMessage["headers"];
  body: Buffer;
}

/** A fake Ratel: the push API and the runs API of protocol/sandbox/v1. */
export class FakePlatform {
  calls: PlatformCall[] = [];
  /** The statuses that each GET of the version answers, in order. The last repeats. */
  versionStatuses: Record<string, unknown>[] = [
    { status: "preparing" },
    { status: "ready", smoke: { ok: true, output: { reply: "hello" }, durationMs: 812 } },
  ];
  runStatuses: Record<string, unknown>[] = [
    { status: "running" },
    { status: "completed", output: { reply: "hi" } },
  ];
  refuseCreate?: { status: number; body: unknown };
  uploadOrigin?: string;
  private server = createServer((req, res) => this.handle(req, res));
  private gets = 0;
  private runGets = 0;
  url = "";

  async start(): Promise<this> {
    await new Promise<void>((resolve) => this.server.listen(0, "127.0.0.1", resolve));
    this.url = `http://127.0.0.1:${(this.server.address() as AddressInfo).port}`;
    return this;
  }

  close(): Promise<void> {
    this.server.closeAllConnections();
    return new Promise((resolve) => this.server.close(() => resolve()));
  }

  json(path: string): unknown {
    const call = this.calls.find((c) => c.path === path);
    return call ? JSON.parse(call.body.toString() || "null") : undefined;
  }

  private version(extra: Record<string, unknown>) {
    return {
      versionId: "v_1",
      name: "support-agent",
      url: `${this.url}/sandboxes/support-agent/versions/v_1`,
      ...extra,
    };
  }

  private async handle(req: IncomingMessage, res: ServerResponse) {
    const chunks: Buffer[] = [];
    for await (const chunk of req) chunks.push(chunk as Buffer);
    const path = (req.url ?? "").split("?")[0];
    this.calls.push({
      method: req.method ?? "",
      path,
      headers: req.headers,
      body: Buffer.concat(chunks),
    });
    const send = (status: number, body?: unknown) => {
      res.writeHead(status, { "content-type": "application/json" });
      res.end(body === undefined ? "" : JSON.stringify(body));
    };
    const route = `${req.method} ${path}`;
    if (route === "POST /api/v1/sandboxes/versions") {
      if (this.refuseCreate) return send(this.refuseCreate.status, this.refuseCreate.body);
      return send(201, {
        versionId: "v_1",
        upload: {
          method: "PUT",
          url: `${this.uploadOrigin ?? this.url}/api/v1/sandboxes/versions/v_1/bundle`,
          headers: { "content-type": "application/gzip" },
          authorize: true,
        },
      });
    }
    if (route === "PUT /api/v1/sandboxes/versions/v_1/bundle") return send(204);
    if (route === "POST /api/v1/sandboxes/versions/v_1/finalize")
      return send(202, this.version({ status: "uploaded" }));
    if (route === "GET /api/v1/sandboxes/versions/v_1") {
      const answer = this.versionStatuses[Math.min(this.gets++, this.versionStatuses.length - 1)];
      return send(200, this.version(answer));
    }
    if (route === "POST /api/v1/sandbox-runs")
      return send(202, { runId: "r_1", versionId: "v_1", status: "queued" });
    if (route === "GET /api/v1/sandbox-runs/r_1") {
      const answer = this.runStatuses[Math.min(this.runGets++, this.runStatuses.length - 1)];
      return send(200, {
        runId: "r_1",
        versionId: "v_1",
        url: `${this.url}/sandboxes/runs/r_1`,
        ...answer,
      });
    }
    if (route === "GET /redirect") {
      res.writeHead(302, { location: "https://elsewhere.test/" });
      return res.end();
    }
    send(404, { error: "not_found", message: `no ${route}` });
  }
}
