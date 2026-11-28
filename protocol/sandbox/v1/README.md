# Ratel sandbox contract: sandbox/v1

> **Experimental preview.** sandbox/v1 may change before it is stable.

The wire contract between an agent image and Ratel. The developer brings a Docker image and
one `ratel.yaml`. Ratel starts a new container for each run, sends one request and reads one
response. The platform, `@ratel-ai/sdk/serve` and the `ratel` CLI build against these schemas.

| Schema | Use |
|---|---|
| `run-request` | The body of `POST /ratel/run` (HTTP mode), or stdin (command mode) |
| `run-response` | The body of a 200 answer (HTTP mode), or a JSON object on stdout (command mode) |
| `error` | The body of each non-200 answer: `{"error": {"code", "message"}}` |
| `ratel-yaml` | The parsed `ratel.yaml` of an agent |

## HTTP mode (default)

The container listens on `$PORT` (`run.port`, default 8080) on all interfaces.

- `GET /ratel/health` answers 200 when the agent can take a run.
- `POST /ratel/run` takes a `run-request` and answers 200 with a `run-response`.
  An invalid request answers 400 with an `error`. A failed run answers 500 with an `error`.

## Command mode

`run.mode: command` with `run.command` (exec form). Ratel starts the container with the
command, writes the `run-request` JSON on stdin and closes stdin. When the command exits 0,
stdout is the answer: a JSON object with `output` is a `run-response`, and any other text is the
output as a string. An exit code other than 0 is a failed run, and Ratel keeps stderr as the
logs. Command mode has no tool control.

## Environment

Ratel starts each container with the values of the secrets of `ratel.yaml` as environment
variables, and sets `RATEL_SECRET_NAMES` to their names, separated by commas. A server never
returns the value of a secret: `@ratel-ai/sdk/serve` redacts them from each response.

## Request

`{v: 1, runId, input, params, tools: {<name>: {mode}}, fixtures: [{tool, args, result}], traceparent?}`

- `input` and `output` are any JSON. Conversation history belongs in `input`, never in `params`.
- `params` is a flat object of the parameters that `ratel.yaml` declares.
- `traceparent` is a W3C trace context. The run continues that trace.

## Tool modes

Each tool has a mode for a run: `simulated` (default), `recorded` or `live`.

- `simulated` answers from the fake handler of the tool. With no fake handler the call fails
  with the code `no_fake`, and the message names the tool.
- `recorded` answers from the fixture with the same tool name and the same normalized
  arguments. With no fixture the call fails with the code `no_fixture`.
- `live` runs the real function.

The response lists each call in `toolCalls` with its mode and its `result` or `error`.

## ratel.yaml

One file for each agent holds the configuration. The Dockerfile holds the environment.

```yaml
name: support-agent
image: { dockerfile: Dockerfile, context: . }   # or { ref: ghcr.io/acme/agent:1.2 }
run: { mode: http, port: 8080, timeoutSeconds: 120 }
input:
  example: { message: Refund order 42 }
params:
  model: { type: enum, options: [claude-sonnet-5-5, claude-haiku-5-5], default: claude-sonnet-5-5 }
  tone: { type: string, default: friendly }
secrets: [ANTHROPIC_API_KEY]          # names only; RATEL_, AWS_ and BOAT_ are reserved
tools:
  lookup_order: { mode: live }
  refund_order: { mode: simulated }
network:
  allow: [api.anthropic.com]
```

Unknown fields are refused, so a typo fails before a build. The default of an `enum`
parameter must be one of its `options`: `verify.mjs` checks this rule in `ratelYamlRules`.
The smoke check of each version runs `input.example` with simulated tools.

## Compatibility

A receiver of a request or a response ignores fields that it does not know. New optional
fields are additive in v1. A change to the meaning of a field needs a new major version.

## Conformance

`conformance/vectors.json` lists valid and invalid documents for each schema. An invalid
vector names the JSON pointer of its first error. `node protocol/sandbox/v1/verify.mjs` checks
them with Node built-ins only, and the CI job `protocol` runs it. The SDK and the CLI import
`validate(schema, document)` from `verify.mjs`, so they report the same paths.
