# @ratel-ai/cli

The `ratel` CLI. Push the Docker image of an agent to Ratel, and run one input on this machine or
on Ratel. The image answers the run contract
[`protocol/sandbox/v1`](../../../protocol/sandbox/v1/README.md), and `ratel.yaml` holds the
configuration of the agent.

```sh
npm install -g @ratel-ai/cli
ratel run --local --input '{"message":"Where is order 42?"}'   # check the contract on this machine
ratel push                                                       # send the image to Ratel
ratel run --input '{"message":"Where is order 42?"}'            # run it on Ratel
```

You need Docker for `ratel push` and `ratel run --local`. You need a project key of Ratel for
`ratel push` and `ratel run`.

| Variable | Use |
|---|---|
| `RATEL_API_KEY` | A project key of Ratel (`ratel push`, `ratel run`) |
| `RATEL_BASE_URL` | The API root of Ratel, with `/api/v1`. Default `https://app.ratel.sh/api/v1` |
| `RATEL_HEALTH_TIMEOUT_MS` | How long `ratel run --local` waits for `GET /ratel/health`. Default 30000 |

## `ratel.yaml`

```yaml
name: support-agent
image: { dockerfile: Dockerfile, context: . }   # or { ref: ghcr.io/acme/agent:1.2 }
run: { mode: http, port: 8080, timeoutSeconds: 120 }   # or { mode: command, command: [python, agent.py] }
input:
  example: { message: Where is order 42? }
params:
  model: { type: enum, options: [claude-sonnet-5-5, claude-haiku-5-5], default: claude-sonnet-5-5 }
  tone: { type: string, default: friendly }
secrets: [ANTHROPIC_API_KEY]
tools:
  lookup_order: { mode: live }
  refund_order: { mode: simulated }
network:
  allow: [api.anthropic.com]
```

The CLI checks the file against the schema before it starts Docker. A wrong field stops the
command with its place and its JSON path, for example
`ratel.yaml:5:3: /params/model/options is required`.

## `ratel push`

```
ratel push [-f ratel.yaml] [--image ref] [--platform os/arch] [--no-wait] [--timeout seconds] [--json]
```

1. Reads and checks `ratel.yaml`, then checks Docker and `RATEL_API_KEY`.
2. Builds the image with `docker build` (`--platform linux/amd64` by default), or takes `--image` or
   `image.ref`.
3. Saves the image with `docker save` and gzip, and reads the git commit of the folder.
4. Creates a version on Ratel with the parsed `ratel.yaml`, uploads the tarball and finalizes it.
5. Waits until the version is `ready` or `failed` (default 900 s; `--no-wait` does not wait).

It prints the version, the image, the commit, the status, the smoke result and a link. When the
smoke check fails, it prints the contract error and the container logs.

| Flag | Use |
|---|---|
| `-f`, `--file` | The `ratel.yaml` (default `./ratel.yaml`) |
| `--image` | Push this image of the local Docker: no build |
| `--platform` | The platform of the build (default `linux/amd64`) |
| `--no-wait` | Do not wait for the smoke check |
| `--timeout` | The longest wait for the smoke check, in seconds (default 900) |
| `--json` | Print one JSON document: the version, `image`, `git`, `bundle`, `smoke` and `url` |

## `ratel run --local`

```
ratel run --local [-f ratel.yaml] [--image ref] [--input json|@file] [--param name=value]...
                  [--tool name=mode]... [--fixtures @file] [--json]
```

Builds the image for this machine and sends one request per the contract.

- HTTP mode: starts the container with `$PORT`, waits for `GET /ratel/health`, sends
  `POST /ratel/run`, then removes the container.
- Command mode: starts the container with `run.command`, writes the request on stdin and reads stdout.
  The image needs no Ratel SDK.

The declared secrets go from this shell into the container (`-e NAME`: the value never shows in the
arguments). It prints the output and each tool call with its mode. A failed run, or a contract error
(no health, a body that is not JSON, no `output`, a timeout), prints the exact error and the
container logs.

| Flag | Use |
|---|---|
| `--input` | The input: JSON, else text, or `@file`. Default `input.example` |
| `--param name=value` | A declared parameter. The type of `ratel.yaml` applies. Repeat for more |
| `--tool name=mode` | The mode of a tool: `simulated`, `recorded` or `live`. Repeat for more |
| `--fixtures @file` | A JSON array of `{tool, args, result}` for the tools in mode `recorded` |
| `--json` | Print one JSON document: `mode`, `image`, `request`, `response`, `error`, `logs`, `durationMs` |

## `ratel run`

```
ratel run [-f ratel.yaml] [--version id] [--input json|@file] [--param name=value]... [--tool name=mode]... [--json]
```

Runs the newest ready version of the agent of `ratel.yaml` on Ratel (or `--version`), waits for the
end and prints the output and a link. `--json` prints the run as one JSON document.

## `ratel eval`

```
ratel eval run [--version id|latest] [--eval name]... [--trials n] [-f ratel.yaml]
               [--param name=value]... [--label label] [--name name] [--timeout seconds] [--json]
               [--no-wait] [--compare-baseline]
ratel eval baseline pin <experiment-id> [--json]
```

`ratel eval run` runs the suite (each eval of the project, with each of its cases) on an image
version (default `latest`, the newest ready version) through
[`protocol/evals/v1`](../../../protocol/evals/v1/README.md), waits for the end and prints the counts
of each case, one line for each eval and a link. `--eval <name>` runs only that eval: give it again
for more evals. `--trials` is the trials of each case. Without it, each eval has its own trials.
`--param` takes a parameter that `ratel.yaml` declares (`./ratel.yaml` or `-f`), and the defaults of
`ratel.yaml` apply. `--label` runs only the cases with the label, and `--name` names the experiment.

The line of an eval has its score (the mean pass rate of its scorers over the valid trials, with one decimal: only a rate of 1 shows 100%), its
passed trials of all trials, and its delta against the pinned baseline in percentage points, or the
reason why it has no delta (`not_in_baseline`, `scorers_differ`):

```
eval Refunds (v2): 66.7% score, 2/3 pass, +33.3 points
```

`--json` prints one JSON document: the experiment, its `params`, `evals` and, with
`--compare-baseline`, the `comparison` (verdict, rule and changed cases). Each item of `evals` is
`{ name, score, counts, scorers, delta }`: `score` is from 0 to 1 or null, `delta` is the
`{ score, scorers }` of Ratel or null. An older Ratel sends no evals, and then the document has no
`evals`. The evals do not change the exit code.
`--compare-baseline` needs a pinned baseline: `ratel eval baseline pin <experiment-id>`.

`--timeout` is the longest wait for the end of the run, in whole seconds. The default is
`trials × run.timeoutSeconds + 600`: `trials` is `--trials` (20, the most trials of a case, when not given) and
`run.timeoutSeconds` comes from `ratel.yaml` (300 when not given or with no `ratel.yaml`). Give a
larger `--timeout` for many cases. At the deadline the command exits with 4, and `--json` prints
`{"error": "wait_timeout", "message", "experimentId"}`. The run can still end on Ratel.

The exit codes of `ratel eval` are a contract of CI gates, and they did not change:

| Code | `ratel eval run` | With `--compare-baseline` |
|---|---|---|
| 0 | Each case passes | `improved` or `unchanged` |
| 1 | A case fails | `regressed` |
| 2 | A case is invalid and none fails | `inconclusive` |
| 3 | Ratel refused the call, or a wrong flag or `ratel.yaml` | the same |
| 4 | Ratel did not answer, its answer is not readable, or the run did not end in `--timeout` | the same |

## Try it with the test server

`scripts/test-server.mjs` is a local server of the push, runs and evals API (not Ratel, not
published). It checks each body against the protocol schemas and does real work on this machine:
`docker load` of the uploaded tarball, the smoke check, the runs and the eval trials, each with
`ratel run --local`.

```sh
node src/cli/ts/scripts/test-server.mjs 4580 examples/sandbox-command-agent/cases.json
export RATEL_BASE_URL=http://127.0.0.1:4580/api/v1 RATEL_API_KEY=rtl_test
cd examples/sandbox-command-agent
ratel push && ratel run --input '{"text":"hi"}'
ratel eval run --name baseline && ratel eval baseline pin e_1
ratel eval run --param style=shout --compare-baseline     # regressed: exit 1
```

## Exit codes of `push` and `run`

| Code | Meaning |
|---|---|
| 0 | The command did what it should |
| 1 | The agent failed: a failed build, a failed smoke check, a failed run or a contract error |
| 2 | A wrong flag or a wrong `ratel.yaml`. Nothing was sent |
| 3 | Ratel refused the call (for example a limit). `--json` prints its refusal |
| 4 | Docker or Ratel is not available: Docker is not installed or not running, or Ratel does not answer |

With `--json`, an error prints one JSON document `{"error": code, "message", ...}` on stdout.
