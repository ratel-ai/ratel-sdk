import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { type CiDeps, type CiGit, readCiGit, requireActions } from "./ci.js";
import { loadConfig } from "./config.js";
import { checkDocker, type Docker } from "./docker.js";
import { EXIT, type ExitCode, UsageError } from "./errors.js";
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
  /** Read the GitHub Actions job: the repository, branch, pull request, base and diff. */
  ci?: boolean;
}

export interface CommandIo {
  env: Record<string, string | undefined>;
  docker: Docker;
  sleep: (ms: number) => Promise<void>;
  progress: (text: string) => void;
  /** How `--ci` reads the job's event and runs git. The tests give fakes. */
  ci?: CiDeps;
}

/** What `ratel push --json` prints. */
export interface PushResult extends Version {
  image: { ref: string; digest: string; platform: string };
  /** With `--ci`, also the CI fields of `git`, without the diff. */
  git?: { commit: string; dirty: boolean } & Partial<Omit<CiGit, "diff">>;
  bundle: { sha256: string; bytes: number };
}

/**
 * `ratel push`: validate `ratel.yaml`, build (or take) the image, save it as a gzip tarball,
 * upload it with the parsed `ratel.yaml`, and wait for the version to be `ready` or `failed`.
 */
export async function push(options: PushOptions, io: CommandIo): Promise<PushResult> {
  const loaded = await loadConfig(options.file);
  const client = platformClient(io.env);
  // `--ci` fails before the build: a wrong checkout costs nothing.
  const git = await readGit(loaded.dir);
  let ci: CiGit | undefined;
  if (options.ci) {
    requireActions(io.env);
    if (!git) throw new UsageError("--ci needs the folder of ratel.yaml in a git checkout.");
    ci = await readCiGit(io.env, loaded.dir, git.commit, io.ci);
  }
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
    const created = await client.createVersion({
      config: loaded.config,
      image: { digest: image.digest, platform: image.platform },
      ...(git ? { git: { ...git, ...ci } } : {}),
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
    const { diff: _diff, ...printed } = ci ?? {};
    return { ...version, image, ...(git ? { git: { ...git, ...printed } } : {}), bundle };
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
    const { commit, dirty, branch, pullRequest } = result.git;
    const where = branch ? ` on ${oneLine(branch)}${pullRequest ? ` (#${pullRequest})` : ""}` : "";
    lines.push(
      `  commit  ${commit.slice(0, 7)}${where}${dirty ? " (with changes that are not committed)" : ""}`,
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
