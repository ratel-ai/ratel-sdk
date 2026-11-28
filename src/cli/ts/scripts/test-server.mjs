#!/usr/bin/env node
// A local test server for the push API and the runs API of protocol/sandbox/v1.
//
//   node src/cli/ts/scripts/test-server.mjs [port]      (default 4580)
//   RATEL_BASE_URL=http://127.0.0.1:4580/api/v1 RATEL_API_KEY=rtl_test ratel push
//
// It is not Ratel. It checks each body against the schemas of the protocol, keeps the versions
// in memory, and does a real smoke check on this machine: `docker load` of the uploaded tarball,
// then `ratel run --local` of input.example with all tools simulated. It never sends data out.

import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { createReadStream, createWriteStream, mkdtempSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { pipeline } from "node:stream/promises";
import { fileURLToPath } from "node:url";
import { createGunzip } from "node:zlib";
import { validate } from "../protocol/verify.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const CLI = join(HERE, "../dist/cli.js");
const KEY = process.env.TEST_SERVER_KEY ?? "rtl_test";
const PORT = Number(process.argv[2] ?? 4580);
const STORE = mkdtempSync(join(tmpdir(), "ratel-test-server-"));
const versions = new Map();
const runs = new Map();

const server = createServer(async (req, res) => {
  const path = (req.url ?? "").split("?")[0];
  const origin = `http://${req.headers.host}`;
  log(`${req.method} ${path}`);
  const send = (status, body) => {
    res.writeHead(status, { "content-type": "application/json" });
    res.end(body === undefined ? "" : JSON.stringify(body));
  };
  if (req.headers.authorization !== `Bearer ${KEY}`) {
    return send(401, { error: "unauthorized", message: "The API key is not valid." });
  }
  try {
    let m;
    if (req.method === "POST" && path === "/api/v1/sandboxes/versions") {
      const body = await json(req);
      const problem = validate("create-version.request", body)[0];
      if (problem) return send(400, { error: "invalid", message: `${problem.path} ${problem.message}` });
      const versionId = `v_${versions.size + 1}`;
      versions.set(versionId, {
        versionId,
        name: body.config.name,
        status: "uploading",
        reason: null,
        image: body.image,
        git: body.git ?? null,
        bundle: body.bundle,
        config: body.config,
        createdAt: new Date().toISOString(),
        url: `${origin}/sandboxes/${body.config.name}/versions/${versionId}`,
      });
      log(`  config ${body.config.name}: tools ${JSON.stringify(body.config.tools ?? {})}`);
      return send(201, {
        versionId,
        upload: {
          method: "PUT",
          url: `${origin}/api/v1/sandboxes/versions/${versionId}/bundle`,
          headers: { "content-type": "application/gzip" },
          authorize: true,
        },
      });
    }
    if (req.method === "PUT" && (m = /^\/api\/v1\/sandboxes\/versions\/([^/]+)\/bundle$/.exec(path))) {
      const version = versions.get(m[1]);
      if (!version) return send(404, { error: "not_found", message: "No such version." });
      const file = join(STORE, `${version.versionId}.tar.gz`);
      const hash = createHash("sha256");
      let bytes = 0;
      req.on("data", (chunk) => {
        hash.update(chunk);
        bytes += chunk.length;
      });
      await pipeline(req, createWriteStream(file));
      if (hash.digest("hex") !== version.bundle.sha256 || bytes !== version.bundle.bytes) {
        return send(409, { error: "bundle_mismatch", message: "The tarball does not match its SHA-256 and size." });
      }
      log(`  stored ${(bytes / 1e6).toFixed(1)} MB`);
      version.file = file;
      version.status = "uploaded";
      return send(204);
    }
    if (req.method === "POST" && (m = /^\/api\/v1\/sandboxes\/versions\/([^/]+)\/finalize$/.exec(path))) {
      const version = versions.get(m[1]);
      if (!version) return send(404, { error: "not_found", message: "No such version." });
      if (version.status === "uploaded") {
        version.status = "preparing";
        prepare(version).catch((error) => {
          version.status = "failed";
          version.reason = error.message;
        });
      }
      return send(202, view(version));
    }
    if (req.method === "GET" && (m = /^\/api\/v1\/sandboxes\/versions\/([^/]+)$/.exec(path))) {
      const version = versions.get(m[1]);
      return version ? send(200, view(version)) : send(404, { error: "not_found", message: "No such version." });
    }
    if (req.method === "POST" && path === "/api/v1/sandbox-runs") {
      const body = await json(req);
      const problem = validate("start-run.request", body)[0];
      if (problem) return send(400, { error: "invalid", message: `${problem.path} ${problem.message}` });
      const version = body.versionId
        ? versions.get(body.versionId)
        : [...versions.values()].reverse().find((v) => v.name === body.name && v.status === "ready");
      if (!version || version.status !== "ready") {
        return send(404, { error: "not_found", message: "No ready version of this agent." });
      }
      const runId = `r_${runs.size + 1}`;
      const run = { runId, versionId: version.versionId, status: "running", input: body.input, params: body.params ?? {} };
      run.url = `${origin}/sandboxes/runs/${runId}`;
      runs.set(runId, run);
      execute(version, body).then((result) => Object.assign(run, result));
      return send(202, run);
    }
    if (req.method === "GET" && (m = /^\/api\/v1\/sandbox-runs\/([^/]+)$/.exec(path))) {
      const run = runs.get(m[1]);
      return run ? send(200, run) : send(404, { error: "not_found", message: "No such run." });
    }
    send(404, { error: "not_found", message: `No route ${req.method} ${path}.` });
  } catch (error) {
    send(500, { error: "server_error", message: error.message });
  }
});

server.listen(PORT, "127.0.0.1", () => log(`test server on http://127.0.0.1:${PORT}/api/v1 (key ${KEY})`));

/** docker load, then the smoke check: input.example with all tools simulated. */
async function prepare(version) {
  const loaded = await sh("docker", ["load"], createReadStream(version.file).pipe(createGunzip()));
  const ref = /Loaded image(?: ID)?: (\S+)/.exec(loaded.stdout)?.[1];
  if (!ref) throw new Error(`docker load gave no image: ${loaded.stderr.trim()}`);
  version.ref = `ratel-test-server/${version.name}:${version.versionId}`;
  await sh("docker", ["tag", ref, version.ref]);
  log(`  loaded ${version.ref}, smoke check`);
  const tools = Object.keys(version.config.tools ?? {}).map((name) => `${name}=simulated`);
  const result = await runImage(version, { tools });
  version.smoke = {
    ok: !result.error,
    ...(result.response ? { output: result.response.output } : {}),
    durationMs: result.durationMs,
    logs: result.logs,
    ...(result.error ? { error: result.error } : {}),
  };
  version.status = result.error ? "failed" : "ready";
  version.reason = result.error ? "The smoke check failed." : null;
  log(`  ${version.versionId} ${version.status}`);
}

async function execute(version, body) {
  const params = Object.entries(body.params ?? {}).map(([k, v]) => `${k}=${v}`);
  const tools = Object.entries(body.tools ?? {}).map(([k, v]) => `${k}=${v.mode}`);
  const result = await runImage(version, { input: JSON.stringify(body.input), params, tools });
  log(`  run on ${version.versionId}: ${result.error ? "failed" : "completed"}`);
  return result.error
    ? { status: "failed", error: result.error, logs: result.logs }
    : { status: "completed", ...result.response, logs: result.logs };
}

/** One run of the loaded image with `ratel run --local`, so the contract checks are the CLI's. */
async function runImage(version, { input, params = [], tools = [] }) {
  const folder = mkdtempSync(join(STORE, "run-"));
  writeFileSync(join(folder, "ratel.yaml"), JSON.stringify(version.config));
  const args = [CLI, "run", "--local", "-f", join(folder, "ratel.yaml"), "--image", version.ref, "--json"];
  if (input !== undefined) args.push("--input", input);
  for (const p of params) args.push("--param", p);
  for (const t of tools) args.push("--tool", t);
  const { stdout } = await sh(process.execPath, args);
  const doc = JSON.parse(stdout);
  if (doc.request === undefined) return { error: { code: doc.error, message: doc.message }, logs: "", durationMs: 0 };
  return doc;
}

function sh(command, args, input) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (c) => (stdout += c));
    child.stderr.on("data", (c) => (stderr += c));
    child.on("error", reject);
    child.on("close", (code) => resolve({ code, stdout, stderr }));
    if (input) input.pipe(child.stdin);
    else child.stdin.end();
  });
}

function view({ file, ref, ...version }) {
  return version;
}

async function json(req) {
  let text = "";
  for await (const chunk of req) text += chunk;
  return JSON.parse(text);
}

function log(line) {
  process.stdout.write(`[test-server] ${line}\n`);
}
