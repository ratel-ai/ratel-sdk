# `examples/experiments-python` — a two-arm retrieval experiment (Python)

The Python mirror of the TypeScript `experimentalDefineExperiment` example in the
[`@ratel-ai/sdk` README](../../src/sdk/ts/README.md#retrieval-experiments-experimental). See
[Retrieval experiments (experimental)](../../src/sdk/python/README.md#retrieval-experiments-experimental)
in the Python SDK README for the API.

```bash
uv run python main.py
```

The script registers four tools in a `ratel()` core and defines the experiment `query-expansion`
with two arms. The `control` arm searches with the user's words. The `candidate` arm first
replaces synonyms (`undo` becomes `roll back`). A 50/50 split gives each of six users a stable
arm, and `shadow=True` runs the other arm as detached shadow work.

The script prints the arm and the top hits of each user, then each served-vs-shadow comparison
(top-1 agreement and Jaccard@3) and the count of each experiment event on `r.events`. It reports
one invocation and one outcome per selection. It exits non-zero unless both arms served and every
selection produced a comparison. It sends no request over the network.
