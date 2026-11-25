# `@ratel-ai/sandbox`

> **Experimental.** Not published. The API may change.

A sandbox is the definition of an agent that Ratel can build and run. The definition is one file in
the host repository, `ratel.sandbox.ts`. TypeScript agents only.

## The definition

```ts
// ratel.sandbox.ts, at the root of the project folder
import { defineSandbox } from "@ratel-ai/sandbox";
import { lookupOrder, refundOrder } from "./src/tools.js";

export default defineSandbox({
  name: "support-agent",
  entry: "src/agent.ts", // its default export is the run function
  base: "node:24-slim", // the default. Node 22.18 or later (from Node 26 with no enum)
  install: ["npm ci --omit=dev"], // runs in the project folder when Ratel builds the image
  secrets: ["OPENAI_API_KEY"], // names only, never values
  tools: {
    lookup_order: { sideEffect: false, execute: lookupOrder },
    refund_order: { sideEffect: true, execute: refundOrder, fake: ({ id }) => ({ id, refunded: true }) },
  },
});
```

```ts
// src/agent.ts
import type { RunContext } from "@ratel-ai/sandbox";

export default async function run({ input, params, tools }: RunContext) {
  return answer(input, params, tools); // call tools only through `tools`
}
```

- `entry`, `name` and `secrets` are checked by the types and again when the file loads.
- The prefixes `RATEL_`, `MODAL_` and `AWS_` are reserved for secret names.
- A tool with `sideEffect: true` must have a `fake` handler.
- Node removes the types of the files. Imports of `./x.js` and `./x` find `./x.ts`. `paths` of
  `tsconfig.json` are not supported.

## Tool policy

| Tool | In a run |
|---|---|
| `sideEffect: false` | Runs for real |
| `sideEffect: true` | Answers from a fixture of the run with the same arguments, else from `fake`. Never runs for real |

The policy applies to the calls through `tools`. A tool that the agent imports and calls itself is
not under the policy.

## `ratel sandbox push`

```bash
RATEL_URL=https://cloud.ratel.sh RATEL_API_KEY=... ratel sandbox push [folder] [--json]
```

Makes one new version from the project folder. The bundle has the files that git lists (tracked, and
untracked that are not ignored) and the runner of this package in `.ratel-sandbox-runner/`. It never
has `.env*` files or `node_modules`. A link inside the project becomes its file. A push records the
commit, and whether the folder has changes that are not committed. The key needs the permission
`sandboxes:write`.

| Exit code | Meaning |
|---|---|
| 0 | Ratel has the version. The output has its id |
| 1 | The push failed or Ratel refused it. The error output has the reason |
| 2 | The command or a setting is wrong |

## `ratel eval run`

```bash
RATEL_URL=https://cloud.ratel.sh RATEL_API_KEY=... ratel eval run [options]
```

Runs each approved case of the project on a sandbox version, waits for the end and prints the counts
of each case and the address of the experiment page. The key needs the permissions `evals:read` and
`evals:write`.

| Option | Meaning |
|---|---|
| `--sandbox-version <id\|latest>` | The version to run. The default is `latest`: the newest version that is ready |
| `--trials <n>` | The trials for each case, 1 to 20. Ratel's default is 3 |
| `--params <json>` | A JSON object that each run gets as its params |
| `--json` | Prints one JSON document on the standard output, and nothing else there |
| `--no-wait` | Prints the experiment id, does not wait, and exits with 0 |
| `--compare-baseline` | Compares the run with the pinned baseline of the project. The verdict gives the exit code |

The exit codes are a contract:

| Exit code | Meaning |
|---|---|
| 0 | All cases pass: each trial passes. Also `--no-wait` and `--help` (with `--json` the help goes to the error output) |
| 1 | A case fails: one of its trials fails |
| 2 | A case is invalid (one of its trials is) and no case fails |
| 3 | Ratel refused the call, or the command or a setting is wrong |
| 4 | The network or the server failed |

With `--json`, the document is the [experiment](../../../protocol/evals/v1) (exit 0, 1 and 2), the
started experiment (`--no-wait`), or `{ "error", "message" }` (exit 3 and 4).

### Compare with a baseline

```bash
ratel eval baseline pin <experiment-id> [--json]   # a completed experiment. Nothing else pins one
ratel eval run --compare-baseline --json
```

With no pinned baseline, `--compare-baseline` starts no run and exits with 3. The exit codes with it:

| Exit code | Meaning |
|---|---|
| 0 | Improved or unchanged |
| 1 | Regressed: a case went from pass to fail |
| 2 | Inconclusive: a compared case is invalid, or no case is in both experiments |
| 3 | Refused (also when the scorers of the two experiments differ), or the command or a setting is wrong |
| 4 | The network or the server failed |

With `--json`, the document is the experiment with a `comparison`: the `verdict`, the text of its
`rule`, the `changedCases` and the `otherCases`. After a run, a refusal also has its `experimentId`
and `url`, and `scorers` names each scorer that differs. `baseline pin` exits with 0, 3 or 4.

## The runner

`node .ratel-sandbox-runner/run.js`, in the project folder, is the main process of an instance. It:

1. reads `RATEL_URL`, `RATEL_RUN_TOKEN`, `RATEL_RUN_INPUT` and `RATEL_SECRET_NAMES`,
2. starts the agent in its own process and records each tool call,
3. sends a root span to `/v1/traces` and posts the run result, both with the run token,
4. exits with 0, or with 1 when it could not post the result.

The agent's process gets the secrets, `TRACEPARENT`, and the trace endpoint with the run token as
`RATEL_OTLP_ENDPOINT` and `RATEL_API_KEY` (and the OpenTelemetry variables). Start the agent's spans
below `TRACEPARENT` (or `context.traceparent`) to have one trace. An agent that throws, exits or is
killed gives a result with the status `failed`.

Secret values of 4 characters or more are replaced with `[redacted]` in the result, the root span
and the output of the process. The agent's own spans are not changed.

The wire contract is [`protocol/sandbox/v1`](../../../protocol/sandbox/v1). The contract of eval runs is
[`protocol/evals/v1`](../../../protocol/evals/v1).

## Build and test

```bash
pnpm --filter @ratel-ai/sandbox build
pnpm --filter @ratel-ai/sandbox typecheck
pnpm --filter @ratel-ai/sandbox lint
pnpm --filter @ratel-ai/sandbox test
```
