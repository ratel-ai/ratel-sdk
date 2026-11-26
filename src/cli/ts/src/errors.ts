import { PlatformError } from "@ratel-ai/sdk/platform";

/** The exit codes of `ratel push` and `ratel run`. They agree with the codes of `ratel eval`. */
export const EXIT = {
  /** The command did what it should. */
  ok: 0,
  /** The agent failed: a failed version, a failed run or a contract error. */
  failed: 1,
  /** A wrong flag, or a wrong `ratel.yaml`. Nothing was sent. */
  usage: 2,
  /** Ratel refused the call. */
  refused: 3,
  /** Docker or Ratel is not available. */
  unavailable: 4,
} as const;

export type ExitCode = (typeof EXIT)[keyof typeof EXIT];

/** An error that the CLI prints as it is, with its exit code and a stable code for `--json`. */
export class CliError extends Error {
  constructor(
    message: string,
    readonly exit: ExitCode,
    readonly code: string,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "CliError";
  }
}

/** A wrong flag. */
export class UsageError extends CliError {
  constructor(message: string) {
    super(message, EXIT.usage, "usage");
  }
}

/** Docker is not installed, not running, or could not do a step. */
export class DockerError extends CliError {
  constructor(message: string, code = "docker_unavailable") {
    super(message, EXIT.unavailable, code);
  }
}

/** The error of a call to Ratel, with the refusal of Ratel when there is one. */
export function fromPlatformError(error: PlatformError): CliError {
  if (error.code === "config") return new CliError(error.message, EXIT.usage, "config");
  const body = (error.body ?? {}) as Record<string, unknown>;
  const message = typeof body.message === "string" ? body.message : error.message;
  if (error.status === null || error.code === "server_error" || error.code === "unavailable") {
    return new CliError(
      message,
      EXIT.unavailable,
      typeof body.error === "string" ? body.error : error.code,
      body,
    );
  }
  return new CliError(
    message,
    EXIT.refused,
    typeof body.error === "string" ? body.error : error.code,
    body,
  );
}

/** Any error as a {@link CliError}. */
export function toCliError(error: unknown): CliError {
  if (error instanceof CliError) return error;
  if (error instanceof PlatformError) return fromPlatformError(error);
  return new CliError(
    error instanceof Error ? error.message : String(error),
    EXIT.failed,
    "failed",
  );
}
