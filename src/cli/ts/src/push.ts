import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { loadConfig } from "./config.js";
import { checkDocker, type Docker } from "./docker.js";
import { EXIT, type ExitCode } from "./errors.js";
import { readGit } from "./git.js";
import { resolveImage } from "./image.js";
import { logsBlock, megabytes, oneLine, short } from "./output.js";
import { platformClient, poll, type Version } from "./platform.js";

export interface PushOptions {
  file: string;
  image?: string;
  platform: string;
  wait: boolean;
  timeoutMs: number;
}

export interface CommandIo {
  env: Record<string, string | undefined>;
  docker: Docker;
  sleep: (ms: number) => Promise<void>;
  progress: (text: string) => void;
}

/** What `ratel push --json` prints. */
export interface PushResult extends Version {
  image: { ref: string; digest: string; platform: string };
  git?: { commit: string; dirty: boolean };
  bundle: { sha256: string; bytes: number };
}

/**
 * `ratel push`: validate `ratel.yaml`, build (or take) the image, save it as a gzip tarball,
 * upload it with the parsed `ratel.yaml`, and wait for the version to be `ready` or `failed`.
 */
export async function push(options: PushOptions, io: CommandIo): Promise<PushResult> {
  const loaded = await loadConfig(options.file);
  const client = platformClient(io.env);
  await checkDocker(io.docker);
  const image = await resolveImage(io.docker, loaded, {
    image: options.image,
    platform: options.platform,
    progress: io.progress,
  });
  const folder = await mkdtemp(join(tmpdir(), "ratel-push-"));
  try {
    const tarball = join(folder, "image.tar.gz");
    io.progress(`› docker save ${image.ref}\n`);
    const bundle = await io.docker.save(image.ref, tarball);
    const git = await readGit(loaded.dir);
    const created = await client.createVersion({
      config: loaded.config,
      image: { digest: image.digest, platform: image.platform },
      ...(git ? { git } : {}),
      bundle,
    });
    io.progress(`› upload ${megabytes(bundle.bytes)} as version ${oneLine(created.versionId)}\n`);
    await client.upload(created.upload, tarball);
    let version = await client.finalize(created.versionId);
    if (options.wait) {
      io.progress("› wait for the smoke check\n");
      version = await poll(
        () => client.getVersion(created.versionId),
        (v) => v.status === "ready" || v.status === "failed",
        {
          intervalMs: 2000,
          timeoutMs: options.timeoutMs,
          sleep: io.sleep,
          what: `The version ${created.versionId}`,
        },
      );
    }
    return { ...version, image, ...(git ? { git } : {}), bundle };
  } finally {
    await rm(folder, { recursive: true, force: true });
  }
}

/** The exit code of a push: a failed version is 1. */
export function pushExit(result: PushResult): ExitCode {
  return result.status === "failed" ? EXIT.failed : EXIT.ok;
}

/** The report of a push for a person. */
export function pushReport(result: PushResult): string {
  const lines = [`${oneLine(result.name)}: version ${oneLine(result.versionId)}`];
  lines.push(
    `  image   ${result.image.digest.slice(0, 19)} (${oneLine(result.image.platform)}), ${megabytes(result.bundle.bytes)}`,
  );
  if (result.git) {
    lines.push(
      `  commit  ${result.git.commit.slice(0, 7)}${result.git.dirty ? " (with changes that are not committed)" : ""}`,
    );
  }
  lines.push(
    `  status  ${oneLine(result.status)}${result.reason ? `: ${oneLine(result.reason)}` : ""}`,
  );
  const { smoke } = result;
  let logs = "";
  if (smoke?.ok) {
    lines.push(`  smoke   ok in ${Math.round(smoke.durationMs ?? 0)} ms: ${short(smoke.output)}`);
  } else if (smoke) {
    lines.push(
      `  smoke   failed: ${oneLine(smoke.error?.code ?? "failed")}: ${oneLine(smoke.error?.message ?? "")}`,
    );
    logs = logsBlock(smoke.logs);
  }
  return `${lines.join("\n")}\n${logs}${result.url ? `  ${oneLine(result.url)}\n` : ""}`;
}
