import { resolve } from "node:path";
import type { LoadedConfig } from "./config.js";
import type { Docker } from "./docker.js";
import { CliError, DockerError, EXIT } from "./errors.js";

/** An image on this machine. */
export interface LocalImage {
  ref: string;
  /** The image id: `sha256:…`. */
  digest: string;
  /** `os/arch`, e.g. `linux/amd64`. */
  platform: string;
}

/**
 * The image of an agent: `--image`, else `image.ref` of `ratel.yaml`, else a `docker build` of the
 * Dockerfile and context of `ratel.yaml`.
 */
export async function resolveImage(
  docker: Docker,
  loaded: LoadedConfig,
  options: { image?: string; platform?: string; progress: (text: string) => void },
): Promise<LocalImage> {
  const { config, dir } = loaded;
  const given =
    options.image ?? (config.image && "ref" in config.image ? config.image.ref : undefined);
  if (given) {
    const found = await inspect(docker, given);
    if (!found) {
      throw new CliError(
        `The image ${given} is not on this machine. Build it, or run docker pull ${given}, and try again.`,
        EXIT.usage,
        "image_not_found",
      );
    }
    return found;
  }
  const build = config.image && !("ref" in config.image) ? config.image : {};
  const tag = `ratel-local/${config.name}:latest`;
  const dockerfile = resolve(dir, build.dockerfile ?? "Dockerfile");
  const context = resolve(dir, build.context ?? ".");
  options.progress(`› docker build ${tag}\n`);
  const result = await docker.exec([
    "build",
    ...(options.platform ? ["--platform", options.platform] : []),
    "-f",
    dockerfile,
    "-t",
    tag,
    context,
  ]);
  if (result.code !== 0) {
    // The whole log of a build is long: the last lines hold the error.
    const tail = `${result.stdout}${result.stderr}`.trimEnd().split("\n").slice(-20).join("\n");
    options.progress(`${tail}\n`);
    throw new CliError(
      `docker build failed: ${errorLine(`${result.stdout}${result.stderr}`)}`,
      EXIT.failed,
      "build_failed",
    );
  }
  const built = await inspect(docker, tag);
  if (!built) throw new DockerError(`docker build made no image ${tag}.`, "docker_failed");
  return built;
}

async function inspect(docker: Docker, ref: string): Promise<LocalImage | null> {
  const result = await docker.exec([
    "image",
    "inspect",
    "--format",
    "{{.Id}} {{.Os}}/{{.Architecture}}",
    ref,
  ]);
  if (result.code !== 0) return null;
  const [digest, platform] = result.stdout.trim().split(" ");
  return { ref, digest, platform };
}

/** The last line of a build log that names an error, else its last line. */
function errorLine(log: string): string {
  const lines = log
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  return lines.findLast((line) => /error/i.test(line)) ?? lines.at(-1) ?? "no message";
}
