# Ratel sandbox contract: sandbox/v1

> **Experimental preview.** sandbox/v1 may change.

The wire contract between a host repository, Ratel and the runner in an instance. It knows
versions and runs. It does not know why a run starts.

| Schema | Use |
|---|---|
| `manifest` | What a sandbox definition declares. It holds secret names, never values |
| `create-version.request`, `create-version.response` | `POST /api/v1/sandboxes/versions` |
| `version` | `POST /api/v1/sandboxes/versions/{id}/finalize`, `GET /api/v1/sandboxes/versions/{id}` |
| `run-input` | What the runner reads (`RATEL_RUN_INPUT`) |
| `run-result` | `POST /api/v1/sandbox-runs/{id}/result` |
| `error` | The body of each refused call |

## Push

1. `POST /api/v1/sandboxes/versions` creates a version and answers where to upload the bundle.
2. `PUT <upload.url>` with `upload.headers` sends the tar.gz.
3. `POST /api/v1/sandboxes/versions/{id}/finalize` queues the build and answers 202.

The bundle is a gzip tar of files and folders only (no links), with the runner of
`@ratel-ai/sandbox` in `.ratel-sandbox-runner/`.

Each push creates a version. A version keeps its manifest, git commit and bundle hash.

## Run

Each run has a new instance. Ratel sets these variables in it:

| Variable | Value |
|---|---|
| `RATEL_URL` | The origin of Ratel |
| `RATEL_RUN_TOKEN` | A token for this run only: it posts the result and ingests traces |
| `RATEL_RUN_INPUT` | base64 of the gzip of the `run-input` JSON |
| `RATEL_SECRET_NAMES` | The names of the declared secrets, with commas |
| each declared secret | Its value |

The instance runs `node .ratel-sandbox-runner/run.js` in the project folder.

A tool with `sideEffect: false` runs for real. A tool with `sideEffect: true` never runs for real.
It answers from a fixture with the same tool name and normalized arguments, else from the fake
handler of the definition. The result records each call with its source. The runner ends after it
posts the result.

## Compatibility

A receiver ignores fields that it does not know. New optional fields are additive in v1. A change
to the meaning of a field needs a new major version.

## Conformance

`conformance/valid/<schema>.<case>.json` must pass `schema/<schema>.schema.json`, and
`conformance/invalid/<schema>.<case>.json` must fail it. The tests of
[`src/sandbox/ts`](../../../src/sandbox/ts) run them.
