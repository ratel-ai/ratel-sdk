import { mkdtempSync, openAsBlob, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { loadDefinition } from "./loader.js";
import { type Manifest, toManifest } from "./manifest.js";
import { type GitState, listProjectFiles, readGitState } from "./project.js";
import { writeTarGz } from "./tar.js";

export type PushOptions = { projectDir: string; url: string; apiKey: string };

export type PushResult = {
  versionId: string;
  name: string;
  status: string;
  reason: string | null;
  git: GitState;
  bundle: { sha256: string; bytes: number; files: number };
};

/** Ratel refused the push. The message is the text of the refusal. */
export class PushRefused extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly body: Record<string, unknown>,
  ) {
    super(message);
  }
}

/** Bundles the project folder and makes one new version from it. */
export async function push({ projectDir, url, apiKey }: PushOptions): Promise<PushResult> {
  const origin = url.replace(/\/+$/, "");
  const manifest: Manifest = toManifest(await loadDefinition(projectDir));
  const git = readGitState(projectDir);
  const files = listProjectFiles(projectDir);
  if (!files.some((file) => file.name === manifest.entry)) {
    throw new Error(
      `The entry point ${manifest.entry} is not in the bundle. Is it ignored in git?`,
    );
  }
  const folder = mkdtempSync(join(tmpdir(), "ratel-push-"));
  try {
    const file = join(folder, "bundle.tar.gz");
    const bundle = await writeTarGz(files, file);
    const auth = { authorization: `Bearer ${apiKey}` };
    const json = { ...auth, "content-type": "application/json" };

    const created = await call(
      "POST",
      `${origin}/api/v1/sandboxes/versions`,
      json,
      JSON.stringify({ manifest, git, bundle }),
    );
    const { versionId, upload } = created as { versionId: string; upload: Upload };
    const headers = {
      ...upload.headers,
      ...(upload.authorize && sameOrigin(upload.url, origin) ? auth : {}),
    };
    await call(upload.method, upload.url, headers, await openAsBlob(file));
    const version = (await call(
      "POST",
      `${origin}/api/v1/sandboxes/versions/${versionId}/finalize`,
      json,
      "{}",
    )) as {
      status: string;
      reason?: string | null;
    };
    return {
      versionId,
      name: manifest.name,
      status: version.status,
      reason: version.reason ?? null,
      git,
      bundle: { ...bundle, files: files.length },
    };
  } finally {
    rmSync(folder, { recursive: true, force: true });
  }
}

/** The key goes to Ratel only. */
function sameOrigin(a: string, b: string): boolean {
  return new URL(a).origin === new URL(b).origin;
}

type Upload = {
  method: "PUT";
  url: string;
  headers?: Record<string, string>;
  authorize?: boolean;
};

async function call(
  method: string,
  url: string,
  headers: Record<string, string>,
  body: string | Blob,
): Promise<unknown> {
  // The bundle goes to a presigned address: its query is a credential, so messages have the path only.
  const path = new URL(url).pathname;
  let response: Response;
  try {
    response = await fetch(url, { method, headers, body, redirect: "error" });
  } catch {
    throw new Error(`${method} ${path} got no answer from ${new URL(url).host}.`);
  }
  const text = await response.text();
  if (!response.ok) {
    const answer = parse(text);
    const reason =
      answer.message ?? answer.error ?? `${method} ${path} answered ${response.status}`;
    throw new PushRefused(String(reason), response.status, answer);
  }
  return parse(text);
}

function parse(text: string): Record<string, unknown> {
  try {
    const value: unknown = JSON.parse(text);
    return value !== null && typeof value === "object" ? (value as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}
