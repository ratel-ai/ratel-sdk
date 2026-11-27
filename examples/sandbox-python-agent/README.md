# `examples/sandbox-python-agent`: a support agent in a Docker image for Ratel (Python)

A support agent with a read-only tool (`lookup_order`) and a tool with a side effect
(`refund_order`). `serve(run)` of [`ratel_ai.serve`](../../src/sdk/python/README.md) makes the image
answer the run contract ([`protocol/sandbox/v1`](../../protocol/sandbox/v1/README.md)), and
`ratel.yaml` holds its configuration: the input example, the parameter `tone` and the tool modes.
The agent has no model, so a run needs no secret.

```python
# agent.py
lookup_order = tool("lookup_order", execute=tools.lookup_order, fake=tools.lookup_order)
refund_order = tool("refund_order", execute=tools.refund_order, fake=tools.fake_refund_order)

serve(run)  # run(args) reads args.input["message"] and args.params["tone"]
```

`ratel.yaml` sets `refund_order` to `simulated`, so a run answers from the fake handler and never
prints `REAL REFUND`. The response lists each tool call with its mode.

## Build, run on this machine, push

From the root of the repository:

```bash
pnpm install && pnpm --filter @ratel-ai/cli... build
cd examples/sandbox-python-agent && pnpm vendor   # copies serve.py into vendor/ratel_ai

pnpm exec ratel run --local
pnpm exec ratel run --local --param tone=formal \
  --input '{"message":"My vase from order A200 arrived damaged. Refund it please."}'

export RATEL_API_KEY=...                          # a project key of Ratel
pnpm exec ratel push
```

`ratel run --local` builds the image, starts it with local Docker, sends one request and prints the
output and the tool calls:

```
tool calls:
  lookup_order (live) {"order_id":"A100"} -> {"order_id":"A100","found":true,...}
  refund_order (simulated) {"order_id":"A100"} -> {"order_id":"A100","status":"refunded","real":false}
```

The image is `python:3.13-slim` with `serve.py` alone: `ratel_ai.serve` uses the standard library
only. With `ratel-ai` 0.15 or later from PyPI, replace the `COPY vendor/...` line of the Dockerfile
with `RUN pip install --no-cache-dir ratel-ai`.

`pnpm test` runs `test_agent.py`: a simulated refund never runs the real refund.

## Layout

```
ratel.yaml      the configuration of the agent on Ratel
Dockerfile      the image: python:3.13-slim, serve.py, agent.py and tools.py
agent.py        the entry point of the image: serve(run) and the tools
tools.py        the two tools and the fake refund
test_agent.py   a run through serve: the refund is simulated
```
