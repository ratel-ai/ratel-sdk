# `examples/sandbox-command-agent`: an agent image with no Ratel SDK (command mode)

A Python script in a container. Ratel starts the container with `run.command`, writes the run
request on stdin and reads the response on stdout
([`protocol/sandbox/v1`](../../protocol/sandbox/v1/README.md), command mode). The image has no Ratel
SDK. Command mode has no tool control.

```yaml
# ratel.yaml
name: command-demo
run:
  mode: command
  command: [python, agent.py]
input:
  example: { text: Ratel runs this image with no SDK in it }
params:
  style: { type: enum, options: [plain, shout, reverse], default: plain }
```

`agent.py` reads `{"v": 1, "runId", "input", "params", ...}` and writes `{"output": ...}`. A JSON
object with `output` is the response. Any other text on stdout is the output as a string. Logs go to
stderr.

## Build, run on this machine, push

From the root of the repository:

```bash
pnpm install && pnpm --filter @ratel-ai/cli... build
cd examples/sandbox-command-agent

pnpm exec ratel run --local --param style=shout      # builds the image and runs input.example

export RATEL_API_KEY=...                              # a project key of Ratel
pnpm exec ratel push
```
