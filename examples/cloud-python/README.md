# `examples/cloud-python` — `ratel(cloud=...)` quickstart (Python)

The Python mirror of [`examples/cloud/src/quickstart.ts`](../cloud/src/quickstart.ts). One option
turns on Ratel Cloud for a [`ratel-ai`](../../src/sdk/python/README.md#ratel-cloud) core, and
`r.cloud` reaches the Cloud API from the same object.

The script passes `MockCloud` (an in-process fake of the Cloud API, from
`ratel_ai.cloud.testing`) as the transport. It sends no request over the network. When
`RATEL_API_KEY` is unset, it sets the fake key `rk_test_demo` in its own process.

```bash
uv run python main.py
```

`uv run` resolves `ratel-ai` from this monorepo (see `[tool.uv.sources]` in `pyproject.toml`) and
builds the native extension on the first run.

The script calls `ratel(cloud={"base_url": ..., "transport": mock})`, registers two tools and runs
one turn with a search and an invoke. Then it creates and lists one skill through `r.cloud`, calls
`await r.cloud.flush()`, and prints `r.cloud.status()` and what `MockCloud` received. It exits
non-zero unless Cloud accepted more than 0 events, one catalog snapshot and one skill.

To send to a real project, remove `transport` and `base_url` and set `RATEL_API_KEY`.
