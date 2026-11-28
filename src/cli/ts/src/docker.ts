import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { createWriteStream } from "node:fs";
import { Transform } from "node:stream";
import { pipeline } from "node:stream/promises";
import { createGzip } from "node:zlib";
import { DockerError } from "./errors.js";

/** What one `docker` command gave. */
export interface ExecResult {
  code: number;
  stdout: string;
  stderr: string;
}

export interface ExecOptions {
  /** Written to stdin, then stdin closes. */
  input?: string;
  /** Copy the output to this sink while the command runs (a build shows its progress). */
  progress?: (chunk: string) => void;
  /** Kill the command after this time. The result then has code 124. */
  timeoutMs?: number;
}

/** The port to the local Docker. The tests give a fake. */
export interface Docker {
  exec(args: readonly string[], options?: ExecOptions): Promise<ExecResult>;
  /** `docker save` of the image, gzipped into the file. The hash and size are of the gzip. */
  save(ref: string, file: string): Promise<{ sha256: string; bytes: number }>;
}

/** The local Docker, through the `docker` command. */
export function localDocker(): Docker {
  return {
    exec: (args, options = {}) => exec(args, options),
    async save(ref, file) {
      const child = spawn("docker", ["save", ref], { stdio: ["ignore", "pipe", "pipe"] });
      const failed = new Promise<never>((_, reject) =>
        child.on("error", (error) => reject(notInstalled(error))),
      );
      let stderr = "";
      child.stderr.on("data", (chunk) => {
        stderr += chunk;
      });
      const exited = new Promise<number>((resolve) =>
        child.on("close", (code) => resolve(code ?? 1)),
      );
      const hash = createHash("sha256");
      let bytes = 0;
      const measure = new Transform({
        transform(chunk: Buffer, _encoding, done) {
          hash.update(chunk);
          bytes += chunk.length;
          done(null, chunk);
        },
      });
      await Promise.race([
        failed,
        pipeline(child.stdout, createGzip(), measure, createWriteStream(file)),
      ]);
      const code = await exited;
      if (code !== 0)
        throw new DockerError(`docker save ${ref} failed: ${firstLine(stderr)}`, "docker_failed");
      return { sha256: hash.digest("hex"), bytes };
    },
  };
}

/** Docker is installed and its daemon answers. Else a {@link DockerError} that says which. */
export async function checkDocker(docker: Docker): Promise<void> {
  const { code, stderr } = await docker.exec(["info", "--format", "{{.ServerVersion}}"]);
  if (code !== 0) {
    throw new DockerError(
      `Docker is not running. Start Docker Desktop (or the Docker daemon) and try again. docker info: ${firstLine(stderr)}`,
    );
  }
}

export function firstLine(text: string): string {
  return (
    text
      .split("\n")
      .map((line) => line.trim())
      .find(Boolean) ?? "no message"
  );
}

function exec(args: readonly string[], options: ExecOptions): Promise<ExecResult> {
  return new Promise((resolve, reject) => {
    const child = spawn("docker", args, { stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    let timedOut = false;
    const timer =
      options.timeoutMs === undefined
        ? undefined
        : setTimeout(() => {
            timedOut = true;
            child.kill("SIGKILL");
          }, options.timeoutMs);
    child.stdout.on("data", (chunk: Buffer) => {
      stdout += chunk;
      options.progress?.(chunk.toString());
    });
    child.stderr.on("data", (chunk: Buffer) => {
      stderr += chunk;
      options.progress?.(chunk.toString());
    });
    child.on("error", (error) => {
      clearTimeout(timer);
      reject(notInstalled(error));
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      resolve({ code: timedOut ? 124 : (code ?? 1), stdout, stderr });
    });
    child.stdin.on("error", () => undefined);
    child.stdin.end(options.input ?? "");
  });
}

function notInstalled(error: NodeJS.ErrnoException): Error {
  return error.code === "ENOENT"
    ? new DockerError(
        "Docker is not installed: the docker command is not in PATH. Install Docker and try again.",
      )
    : error;
}
