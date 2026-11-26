# `examples/platform-python` — `ratel(platform=...)` quickstart (Python)

The Python mirror of [`examples/platform/src/quickstart.ts`](../platform/src/quickstart.ts). One option
turns on Ratel Platform for a [`ratel-ai`](../../src/sdk/python/README.md#ratel platform) core, and
`r.platform` reaches the Platform API from the same object.

The script passes `MockPlatform` (an in-process fake of the Platform API, from
`ratel_ai.platform.testing`) as the transport. It sends no request over the network. When
`RATEL_API_KEY` is unset, it sets the fake key `rk_test_demo` in its own process.

```bash
uv run python main.py
```

`uv run` resolves `ratel-ai` from this monorepo (see `[tool.uv.sources]` in `pyproject.toml`) and
builds the native extension on the first run.

The script calls `ratel(platform={"base_url": ..., "transport": mock})`, registers two tools and runs
one turn with a search and an invoke. Then it creates and lists one skill through `r.platform`, calls
`await r.platform.flush()`, and prints `r.platform.status()` and what `MockPlatform` received. It exits
non-zero unless Platform accepted more than 0 events, one catalog snapshot and one skill.

To send to a real project, remove `transport` and `base_url` and set `RATEL_API_KEY`.
