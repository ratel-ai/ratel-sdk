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
ratel push [-f ratel.yaml] [--image ref] [--platform os/arch] [--no-wait] [--timeout seconds]
           [--ci] [--json]
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
| `--ci` | In a GitHub Actions job: also send the repository, the branch, the pull request, the base commit and the diff (below) |
| `--json` | Print one JSON document: the version, `image`, `git`, `bundle`, `smoke` and `url` |

### `ratel push --ci` in GitHub Actions

Run `ratel push --ci` on each push and pull request, and each commit becomes a version on Ratel.
Ratel reads the diff to run the evals that the change touches.

```yaml
# .github/workflows/ratel.yml
name: Ratel
on:
  push:
    branches: [main]
  pull_request:
jobs:
  push:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0 # the base commit, for the diff
          ref: ${{ github.event.pull_request.head.sha || github.sha }}
      - run: npx @ratel-ai/cli push --ci
        env:
          RATEL_API_KEY: ${{ secrets.RATEL_API_KEY }}
```

`--ci` reads the job's environment (`GITHUB_ACTIONS`, `GITHUB_EVENT_NAME`, `GITHUB_EVENT_PATH`,
`GITHUB_REPOSITORY_ID`, `GITHUB_REPOSITORY`, `GITHUB_HEAD_REF`, `GITHUB_REF_NAME`) and adds these
to `git`:

| Field | A pull request | A push |
|---|---|---|
| `repository` | `{ id, fullName }` | the same |
| `branch` | its head branch | the pushed branch |
| `pullRequest` | its number | `null` |
| `baseCommit` | its base | the branch's previous head (`null` for a new branch) |
| `title`, `author` | its title and author | the commit's first line, the pusher |
| `diff` | `git diff <baseCommit>...<commit>` | the same |

- **Too large:** a diff over 512 KiB is left out, with `diffTruncated: true`.
- **No base:** with no base, or a base git does not have (a shallow clone), there is no diff, and
  Ratel runs every eval.
- **Merge commits:** a pull request's job must check out the pull request's head, not the merge
  commit GitHub checks out by default (`ref:` above). `--ci` stops with a usage error otherwise.
- **Forks:** GitHub gives no secrets to a fork's workflow, so a fork's pull request does not push.
- **Outside Actions:** `--ci` anywhere else is a usage error, and nothing is built.

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
ratel eval run [--eval name|id]... [--version id|latest] [--dataset name|id] [--trials n]
               [-f ratel.yaml] [--param name=value[,value]...]... [--label label] [--name name]
               [--timeout seconds] [--json] [--no-wait] [--compare-baseline]
ratel eval baseline pin <experiment-id> [--json]
```

A run belongs to one eval. `ratel eval run` starts one run for each eval of the project
(`POST /evals/runs` of [`protocol/evals/v1`](../../../protocol/evals/v1/README.md)), waits for the
end of each and prints the counts of each case, the score of each run and a link. `--eval <name>`
runs only that eval (`POST /evals/{eval}/runs`): give it again for more evals (`evals` of
`POST /evals/runs`). An `--eval` is a name or an id.

- `--version` is the image version (default `latest`: the newest ready version of the sandbox that
  `ratel.yaml` names). A version of another sandbox does not apply to an eval of another sandbox:
  that eval runs on the newest ready version of its own sandbox, and the command prints the `note`
  of Ratel on the error output (`ratel: <eval>: <note>`). It is not a refusal.
- `--dataset` runs the cases of a dataset (its name or its id) in place of the cases of each eval,
  for this run only.
- `--trials` is the trials of each case. Without it, each eval has its own trials.
- `--param` takes a parameter that `ratel.yaml` declares (`./ratel.yaml` or `-f`), and the defaults
  of `ratel.yaml` apply. A value with commas is a list: `--param model=a,b --param depth=1,2` sends
  `matrix: { model: ["a", "b"], depth: [1, 2] }`, and each combination is a variant of the run (here
  4). Each item is typed as a single value is (number, boolean, enum option or string). A run has at
  most 12 combinations: the command refuses more with exit 3 before it calls Ratel. A value without
  commas stays in `params`.
- `--label` runs only the cases with the label, and `--name` names the run.

The line of a run has its score (from 0 to 1 as a percent with one decimal: only a rate of 1 shows
100%), its passed trials of all trials, and its delta against the baseline of its eval in percentage
points, or the reason why it has no delta. With a matrix, one line follows for each variant:

```
eval Refunds: 75% score, 9/12 pass, +8.3 points
  variant 0 (model=a, depth=1): 100% score, 3/3 pass
```

An older Ratel sends no `run`: then the line comes from `evals` of the experiment:
`eval Refunds (v2): 66.7% score, 2/3 pass, +33.3 points`.

`--json` prints one JSON document on the standard output. Its shape depends on the number of runs:

- **One run** (one `--eval`, or a project with one eval): the document of one run, as before. It is
  the experiment, its `params` (and `matrix`), `evals` (each `{ name, score, counts, scorers, delta }`)
  and, with `--compare-baseline`, the `comparison` (verdict, rule and changed cases). The new fields
  of the experiment (`evalId`, `datasetId`, `variants`, `run`, `runDelta`, `runDeltaRefusal`) and the
  `note` of the run are in it when Ratel sends them.
- **More runs**: `{ "v": 1, "runs": [...], "exitCode": n }`, with `params` and `matrix`. Each item of
  `runs` is one eval: `{ eval, evalId, experimentId, url, note?, exitCode, score, counts, delta,
  variants, comparison? }`; a refused eval is `{ eval, evalId, error, message, exitCode: 3 }`; a run
  whose wait or comparison failed has `error` and `message` with its `exitCode`.

With `--no-wait` the command prints the experiment id of each run (one a line) and exits with 0, or
with 3 when Ratel refused an eval.

`--compare-baseline` compares each run with the pinned baseline of its eval, and the verdict gives
the exit code of that run. Before it starts a run, it reads the evals (`GET /evals`) and the baseline
of each eval to run (`GET /evals/baseline?eval=<evalId>`): an eval with no baseline refuses the
command (exit 3) and starts no run. Pin a baseline with `ratel eval baseline pin <experiment-id>`.

`--timeout` is the longest wait for the end of the runs, in whole seconds. The default is
`trials × run.timeoutSeconds + 600`: `trials` is `--trials` (20, the most trials of a case, when not given) and
`run.timeoutSeconds` comes from `ratel.yaml` (300 when not given or with no `ratel.yaml`). Give a
larger `--timeout` for many cases. At the deadline the command exits with 4, and `--json` prints
`{"error": "wait_timeout", "message", "experimentId"}` (in the item of the run, with more runs). The
run can still end on Ratel.

The exit codes of `ratel eval` are a contract of CI gates, and they did not change:

| Code | `ratel eval run` | With `--compare-baseline` |
|---|---|---|
| 0 | Each case passes | `improved` or `unchanged` |
| 1 | A case fails | `regressed` |
| 2 | A case is invalid and none fails | `inconclusive` |
| 3 | Ratel refused the call, or a wrong flag or `ratel.yaml` | the same |
| 4 | Ratel did not answer, its answer is not readable, or the run did not end in `--timeout` | the same |

With more runs, the exit code is the highest code of the runs. A refused eval is 3. One run has the
exit code of one run.

## Try it with the test server

`scripts/test-server.mjs` is a local server of the push, runs and evals API (not Ratel, not
published). It checks each body against the protocol schemas and does real work on this machine:
`docker load` of the uploaded tarball, the smoke check, the runs and the eval trials, each with
`ratel run --local`. It has one eval, `cases`: the cases of the `cases.json` that it gets.

```sh
node src/cli/ts/scripts/test-server.mjs 4580 examples/sandbox-command-agent/cases.json
export RATEL_BASE_URL=http://127.0.0.1:4580/api/v1 RATEL_API_KEY=rtl_test
cd examples/sandbox-command-agent
ratel push && ratel run --input '{"text":"hi"}'
ratel eval run --name baseline && ratel eval baseline pin e_1
ratel eval run --param style=shout --compare-baseline     # regressed: exit 1
ratel eval run --param style=plain,shout --trials 1        # 2 variants of one run
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
