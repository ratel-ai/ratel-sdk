<div align="center">
  <h1>ratel-ai</h1>
  <p>The Ratel Platform SDK for Python agents: context engineering at runtime, plus sync and self-improvement through the platform.</p>

  <p>
    <a href="https://docs.ratel.sh">Docs</a> •
    <a href="https://github.com/ratel-ai/ratel-sdk">GitHub</a> •
    <a href="https://discord.gg/75vAPdjYqT">Discord</a>
  </p>

  <p>
    <a href="https://pypi.org/project/ratel-ai/"><img src="https://img.shields.io/pypi/v/ratel-ai?label=pypi&color=3775a9" alt="PyPI" /></a>
    <a href="https://github.com/ratel-ai/ratel-sdk/stargazers"><img src="https://img.shields.io/github/stars/ratel-ai/ratel-sdk?style=social" alt="GitHub stars" /></a>
    <a href="https://github.com/ratel-ai/ratel-sdk/blob/main/LICENSE.md"><img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT license" /></a>
  </p>
</div>

`ratel-ai` retrieves the tools and skills relevant to each agent turn instead of sending the full catalog to the model. It bundles Ratel's Rust engine in-process: BM25 by default, with configurable semantic and hybrid retrieval available when needed. The default and local-model paths require no API key, vector database, or service. Installing a published package on a supported prebuilt target also requires no Rust toolchain.

Use `ToolCatalog` for ranked tools with sync or async handlers and `SkillCatalog` for ranked Markdown playbooks loaded on demand. Expose `search_capabilities_tool`, `invoke_tool_tool`, and `get_skill_content_tool` so an agent can discover tools and skills, invoke tools, and load full skill instructions. Tools from existing MCP servers can be ingested into the tool catalog with the `mcp` extra. **Experimental — facts:** the opt-in `ratel_ai.experimental` namespace adds `FactCatalog` for constant grounding content (a shop's address, a brand's voice). See [Facts](#facts-experimental) below. This API may change or be removed without a major version bump.

Every tool, skill, and fact accepts an optional experimental `experimental_searchable_description`. It replaces only the description component used by BM25 and embeddings; the model-facing `description` is unchanged, names plus skill/fact tags remain indexed, and opted-in tool schemas are not indexed. When omitted, stable behavior is unchanged: tools rank their description and schema tokens, while skills and facts rank their description. This API may change without a major-version bump.

Semantic and hybrid retrieval use a configurable embedding model ([ADR 0012](../../../docs/adr/0012-configurable-embedding-models.md)), set per catalog via the `embedding` argument: the built-in default, a HuggingFace repo or local directory (in-process), or an OpenAI-compatible endpoint (OpenAI, Ollama, TEI, vLLM).

Hybrid fuses the two arms on normalised scores ([ADR 0024](../../../docs/adr/0024-hybrid-fuses-on-scores.md)). `experimental_dense_weight` (default `0.7`) sets how much of that score the semantic arm carries, with BM25 taking the remainder — `0` is pure lexical, `1` pure dense, and anything outside `[0, 1]` raises rather than being clamped. The default was measured on catalogs of natural-language descriptions; a catalog keyed on exact identifiers, error codes, or internal jargon gives BM25 purchase those corpora do not have and will want a lower value. It is read by `"hybrid"` only and does not scale the adaptive-ranking arm.

Adaptive ranking's `IntentGraph` ([ADR 0014](../../../docs/adr/0014-adaptive-usage-ranking.md)) is host-persisted: core only offers `to_json()`/`from_json()`/`rev`. `LocalFileIntentGraphStorage` and `S3IntentGraphStorage` ([ADR 0025](../../../docs/adr/0025-intent-graph-storage-plugins.md)) are the two ready-made backends — both implement `async load() -> IntentGraph | None` / `async save(graph) -> None`, skip the write when `rev` is unchanged, and raise `StaleIntentGraphError` instead of clobbering a concurrent writer. The S3 backend needs no `boto3` dependency; it signs requests with a built-in SigV4 client:

```python
catalog = ToolCatalog()
storage = S3IntentGraphStorage(bucket="my-bucket", key="intent-graph.json")
graph = await storage.load() or IntentGraph()
catalog.experimental_enable_adaptive_ranking(graph)
# ...later, e.g. on an interval...
await storage.save(graph)
```

For MinIO or another self-hosted S3-compatible service, pass `endpoint`; `force_path_style` defaults to `True` once `endpoint` is set (what MinIO and most self-hosted services require):

```python
S3IntentGraphStorage(
    bucket="my-bucket", key="intent-graph.json", endpoint="http://localhost:9000"
)
```

Both S3 calls are bounded by an idle timeout (`idle_timeout_s`, default 60s): it measures time with **no data moving** rather than total elapsed time, so a slow transfer still completes but a wedged endpoint fails instead of hanging.

**A stored graph carries the raw text of past user queries** (the cluster `members`), so treat it like a query or telemetry log: the file backend writes `0600` and the file belongs outside version control and images, while an S3 bucket holding one wants private access and encryption at rest. Neither backend encrypts the payload; the graph is stored as plain JSON.

For semantic or hybrid retrieval, `register()` folds embedding in: it accepts one tool or a whole batch and embeds on a worker thread, so model loading, HTTP, and inference never block the asyncio loop or hold the GIL — and embedding errors surface right at `register()`:

```python
async def retrieve(tools):
    catalog = ToolCatalog(method="semantic", embedding={"ollama": "nomic-embed-text"})
    await catalog.register(tools)                              # embeds the batch here
    return await catalog.search_async("deploy the service", 5)
```

`register()` is async for every method (BM25 too); `search()` stays synchronous for BM25 only, and `search_async()` covers all three. To change the endpoint's model or vector dimension, construct a new catalog and re-register.

A `SkillCatalog` also takes a whole reloaded catalog at once with `replace_all()`, for a source that fetches the full set rather than individual changes ([ADR 0015](../../../docs/adr/0015-whole-catalog-skill-reload.md)). The batch *is* the catalog: ids missing from it are removed, including ones registered in-process, so a host that mixes local and remote skills composes the batch itself. It mutates in place, so every holder of the catalog sees the reload without being rebuilt.

```python
outcome = await catalog.replace_all([*local_skills, *await fetch_remote_skills()])
print(f"reload: +{outcome.added} -{outcome.removed} ~{outcome.updated}")
```

The corpus swap is the synchronous half of that call, so the counts are already final when it returns — read them without awaiting and a reload whose embedding pass fails still reports what it changed:

```python
reload = catalog.replace_all(batch)  # corpus is live; counts are final
try:
    await reload  # drives the embedding pass
except EmbedderError:
    log.warning("applied +%d -%d, embeddings pending", reload.added, reload.removed)
```

Only new and re-worded skills are embedded — reloading an unchanged catalog costs no embedding calls — and a reload that races an in-flight operation — dense work, but also an ordinary BM25 `search_async` holding the read lock — raises rather than applying half of itself.

Build-time embedding artifacts ([ADR 0018](../../../docs/adr/0018-build-time-embedding-artifacts.md), experimental) avoid corpus/document embedding inference for covered entries on cold start: `experimental_build_embedding_artifact` writes a mixed Tool+Skill RAT1 (halves merged internally; no public merge API), and catalogs accept `experimental_embedding_artifact` (`path` or `bytes`; default `on_miss="error"`) to warm the dense cache on `register` / `replace_all` — each call re-resolves the artifact source and re-warms the whole current corpus. `ToolRegistry` / `SkillRegistry` also expose `experimental_build_embedding_artifact` and `experimental_warm_embeddings_from_artifact`. With default `on_miss="error"`, every id in each non-empty registering corpus must be covered; a tool-only artifact is valid while Skill stays empty (and vice versa); when both sides register, use a mixed artifact or `on_miss="embed"`. Semantic/hybrid search still requires query embedding through the configured backend; Local/HF paths may still initialize/load the model, and endpoint performs its normal remote query embedding. `ArtifactWarmError` covers warm failures (`.code`, `.missing`); `ArtifactError` covers non-embedder artifact construction failures (`EmbedderError` remains the embedding/backend failure); `IncompatibleMergeError` may surface from the high-level mixed builder's internal Tool+Skill composition; writing the output file may raise `OSError`.

## Install

```bash
pip install ratel-ai
# MCP ingestion: pip install 'ratel-ai[mcp]'
```

## Quickstart

Save as `quickstart.py`, then run `python quickstart.py`:

```python
import asyncio
from ratel_ai import ExecutableTool, ratel

async def main():
    r = ratel()
    await r.tools.register(
        ExecutableTool(
            id="get_weather",
            name="get_weather",
            description="Get the current weather for a city.",
            input_schema={"properties": {"city": {"type": "string"}}},
            output_schema={"type": "object"},
            execute=lambda args: {"forecast": f"Sunny in {args['city']}"},
        )
    )

    recall = await r.recall("What is the weather in Rome?")
    tool_id = recall["tools"]["groups"][0]["hits"][0]["toolId"]
    print(await r.tools.invoke(tool_id, {"city": "Rome"}))


asyncio.run(main())
```

`ratel()` is the one construction point. It builds a `ToolCatalog` (`r.tools.catalog`), a
`SkillCatalog` (`r.skills`), one runtime-event stream (`r.events`) and a snapshot view
(`r.catalog`). Give the model `r.model_tools()`: `search_capabilities`, `invoke_tool` and
`get_skill_content`, the same three tools however many tools and skills you register, and in
any order. `r.recall(query)` ranks tools and skills for one turn, or returns `None` when nothing
matched. A tool id equal to a capability tool id raises `ValueError`. The keyword arguments
(`method`, `embedding`, `reranker`, `recall_top_k`, `trace`, `events`, ...) go to every catalog.
The experimental fact catalog is `r.facts`, with `r.ground(...)` and `r.ground_snapshot(...)`.

### Mark each request as one turn

Wrap the work for one user request in `catalog.turn(...)` (or `r.turn(...)` on a `ratel()` core). Every search, skill load, and tool
call inside it, across `await` and the tasks it starts, carries the same `turn_id`, and one
`turn_start` event opens the turn. Concurrent requests keep their own turns.

```python
async def handle(request):
    async with catalog.turn(
        id=request.id,  # optional; a fresh id is minted when omitted
        end_user_id=request.user_id,  # optional; stamped on every event in the turn
        user_message=request.text,  # optional; sent only because you pass it
    ):
        return await run_agent(request.text)
```

A plain `with catalog.turn():` works in sync code. If your framework runs a tool itself instead
of through `catalog.invoke`, record it so the turn still shows the call (`origin: "external"`):

```python
catalog.record_tool_call("web_search", took_ms=120)
catalog.record_tool_call("web_search", error=exc)  # a failed call
```

`ratel_ai.current_turn_id()` returns the active id. See
[ADR 0026](../../../docs/adr/0026-turn-scope.md).

### `turn_id`

An explicit `turn_id` argument still works, and wins over the turn scope.
`search` / `search_async` and `invoke` all take a trailing `turn_id`. Mint **one per user
message** and reuse it for every search and invoke that message produces — including a turn that
searches several times for several subtasks. The `search_capabilities` capability tool's executor
takes `turn_id` as a keyword argument, so a framework adapter passes one per model turn.

It scopes, it does not attribute: within a turn, an invoke is paired with the search that actually
returned that capability, so several searches before any invoke each keep their own evidence
(ADR-0014), whether the graph is learned in-process or by Ratel Platform replaying your runtime
events. What the id buys is separation — two conversations sharing one catalog must not pair
each other's searches and invokes. Omit it and every caller shares a single scope, which is fine
for one conversation at a time and wrong for concurrent ones.

```python
turn_id = str(uuid.uuid4())
await catalog.search_async("read issues on github", 5, "agent", turn_id=turn_id)
await catalog.search_async("create linear task", 5, "agent", turn_id=turn_id)
await catalog.invoke("read_github_issues", {}, turn_id)  # pairs with the first
await catalog.invoke("create_linear_task", {}, turn_id)  # pairs with the second
```

Continue with the [Python guide](https://docs.ratel.sh/docs/sdks/python), [capability tools](https://docs.ratel.sh/docs/capability-tools), [API reference](https://docs.ratel.sh/docs/api/sdk-python), or the [Pydantic AI example](https://github.com/ratel-ai/ratel-sdk/tree/main/examples/pydantic-ai).

## Your own retriever or reranker (experimental)

A catalog can rank with a function you supply, such as a decision model you call ([ADR 0027](../../../docs/adr/0027-custom-retriever-and-reranker-functions.md)). The SDK hands the function the candidates and their searchable text, and knows nothing about the model behind it:

```python
from ratel_ai import RankCandidate, RankedId, ToolCatalog, define_plugin


async def mine(query: str, candidates: list[RankCandidate], top_k: int) -> list[RankedId]:
    return [{"id": c.id, "score": await my_model(query, c.text)} for c in candidates]

ToolCatalog(method="custom", retrieve_fn=mine)                    # your model ranks every tool
ToolCatalog(method="bm25", reranker_fn=mine, reranker_depth=20)   # BM25, then your model over its top 20

my_plugin = define_plugin(retrieve=mine, rerank=mine)             # package it as a plugin
```

Ready-made plugins for decision models (Jev, OpenAI's Decisions API) live in [`ratel-ai-rankers-plugin`](../../rankers/python/README.md) ([ADR 0028](../../../docs/adr/0028-vendor-rankers-as-plugin-packages.md)), so this SDK carries no vendor code:

```python
from ratel_ai_rankers_plugin import ratel_jev_plugin
ToolCatalog(method="bm25", reranker_fn=ratel_jev_plugin().rerank)  # key in TYPESAFE_API_KEY
```

- **The contract:** `(query, candidates, top_k)` to a list of `{"id", "score"}`, sync or async. Each `RankCandidate` has `.id`, `.kind` (`"tool"` or `"skill"`) and `.text`. Unknown ids are dropped, each id counts once, and scores are clamped to `[0, 1]`.
  - As `retrieve_fn` (with `method="custom"`) the search returns only the ids the function returned, best first, at most `top_k`.
  - As `reranker_fn` it sees the first stage's top `reranker_depth` (default 50, raised to `top_k`). It can't add a tool the first stage missed; candidates it leaves out follow at 0, in first-stage order.
- **Failures:** whatever `retrieve_fn` raises fails the search. A `reranker_fn` that raises a `RetrieverError` with `transient=True` keeps the first stage's order and records `rerank_fallback:<code>` on the trace; anything else it raises fails the search.
  - A search that fails records no `search` event on the local trace stream, the same as a failed built-in search. The error reaches your `search_async` call and, with telemetry on, marks the `ratel.search` span as an error.
- **Rules:** both need `search_async` (synchronous `search` raises). `reranker_fn` can't be combined with `reranker`, nor with `method="custom"`. `search_async(q, k, reranker=False)` turns either reranker off for one call. `SkillCatalog` takes the same arguments.
- **Plugins:** `define_plugin(retrieve=, rerank=)` checks that both functions are callable and returns a `RetrieverPlugin` with `.retrieve` and `.rerank`. That is the shape every plugin returns. A ranker for one stage only is a plain `RankFn`; pass it to the catalog directly.
- **Privacy:** a function sends whatever it sends. The built-in methods never leave the process.

## Reranking (experimental)

A catalog can rank in two stages with built-in methods ([ADR 0027](../../../docs/adr/0027-custom-retriever-and-reranker-functions.md)). `method` picks candidates, and `reranker["method"]` re-scores the top `depth` of them (default 50). A reranker never adds a tool the first stage missed. Either stage can be `"bm25"`, `"semantic"` or `"hybrid"`, but the two stages must use different methods:

```python
catalog = ToolCatalog(method="bm25", reranker={"method": "semantic", "depth": 30})
await catalog.register(tools)    # a semantic reranker makes register() build embeddings
hits = await catalog.search_async("deploy the service", 5)
plain = await catalog.search_async("deploy the service", 5, reranker=False)   # off for one call
```

A reranker needs `search_async`; synchronous `search` raises on a catalog that has one. `SkillCatalog` takes the same `reranker` argument.

## Runtime events and catalog snapshots

`RuntimeEvents` merges tool and skill facts into one bounded push stream. Give the paired
`RuntimeCatalog` the stream's `source_id` so envelopes and full snapshots identify the same
deployment source:

```python
from ratel_ai import RuntimeCatalog, RuntimeEvents, SkillCatalog, ToolCatalog

tools = ToolCatalog()
skills = SkillCatalog()
events = RuntimeEvents(
    [tools, skills],
    session_id="agent-session",
    source_id="checkout-agent",
    experimental_catalog_definitions=True,
)
catalog = RuntimeCatalog(tools, skills, source_id=events.source_id)

async def publish(batch):
    await send_runtime_facts(batch)

subscription = events.subscribe(publish)  # call from the target asyncio event loop
# Register, search, and invoke through tools / skills as usual.
await subscription.flush()
snapshot = catalog.snapshot()
subscription.unsubscribe()
```

Async handlers are marshaled onto the subscribing event loop; synchronous handlers run on the
native callback thread. Both are observational and fail open. `flush()` waits for work already
accepted by the bounded native queues and for async handlers to settle. Subscribing a remote
publisher must set `experimental_catalog_definitions=True` to consent to public
`catalog_definition` fields regardless of the OTel message-content capture setting. Definition events are lossy and change-sensitive; snapshots are
the authoritative full replacement for removals and recovery. They contain sorted public
definitions only—never tool executors or skill bodies. To publish them to Ratel Platform, use
`ratel(platform=...)` (see [Ratel Platform](#ratel-platform)), or publish them through your own adapter.

## Definition overrides from an external source (experimental)

Registration owns the complete local definition. An external source can override only the
retrieval description, and only after the runtime opts in at attach time. A source is any object
with an async `fetch(if_none_match)` method:

```python
from ratel_ai import ratel

r = ratel()
await r.tools.register(local_tools)


class OverlaySource:
    async def fetch(self, if_none_match=None):
        # Return {"status": 304} when the overlay is unchanged, else:
        return {
            "status": 200,
            "etag": '"overlay-1"',
            "body": {
                "overrides": [
                    {"kind": "tool", "entry_id": "deploy", "searchable_description": "ship a release"}
                ]
            },
        }


attachment = await r.catalog.experimental_attach_definition_overrides(OverlaySource())
await attachment.refresh()  # later pulls; True when a new overlay was applied
```

The attach call does the initial pull and applies the complete overlay to live tools, skills and
facts, and to the ones registered later. `refresh()` sends the last accepted ETag; a `304` changes
nothing. Concurrent `refresh()` calls share one request. The SDK validates each response before it
changes a catalog: the status is `200` or `304`, a `200` has a non-empty ETag and a complete
`overrides` list, entry ids are at most 512 UTF-8 bytes and descriptions at most 16 KiB. An invalid
response raises `ratel_ai.experimental.DefinitionOverlayError` (`code`: `invalid_status`,
`invalid_payload`, `invalid_etag`) and keeps the last accepted ETag and definitions. If a catalog
update fails, all three catalogs are restored and the error has `code="apply_failed"`.

Removing an override restores the latest local value. When both sides set a retrieval
description, the override wins and the SDK warns once for that period. `r.catalog.snapshot()`
always shows the local definitions. The model-facing description, schemas, bodies, tags and
executors stay local. Definition events after an attach carry
`ratel.catalog.use_definition_overrides=true`. Opting in is one-way for the life of the process.
These APIs may change without a major-version bump.

## Run an agent image on Ratel: `ratel_ai.serve`

`ratel_ai.serve` makes the Docker image of an agent answer the Ratel run contract
([`protocol/sandbox/v1`](../../../protocol/sandbox/v1/README.md)). It uses the standard library only
and never loads the native module, so a slim image can use `serve.py` alone.

```python
from ratel_ai.serve import RunArgs, serve, tool

refund_order = tool(
    "refund_order",
    execute=refund,                                  # the real function: mode live
    fake=lambda order_id: {"order_id": order_id, "status": "refunded"},  # mode simulated
)

def run(args: RunArgs) -> dict:                      # sync or async
    return agent.reply(args.input, tone=args.params["tone"], tools=[refund_order])

serve(run)                                           # $PORT, default 8080, on 0.0.0.0
```

`serve(run)` answers `GET /ratel/health` and `POST /ratel/run`. An invalid request answers 400 and
a raised error answers 500, both with `{"error": {"code", "message"}}`. Each run sets the mode of
each tool (default `simulated`): `simulated` answers from `fake` (none: `ToolCallError` with the code
`no_fake`), `recorded` from the fixture with the same normalized arguments (none: `no_fixture`),
`live` from `execute`. The response lists each call with its mode. Outside a run, a tool runs
`execute`. `current_run()` gives the run id and the traceparent, and `record_usage()` adds model
usage. The values of the secrets in `$RATEL_SECRET_NAMES` are redacted from each response. See
[`examples/sandbox-python-agent`](../../../examples/sandbox-python-agent/README.md).

## Ratel Platform

`ratel-ai` is the SDK of the Ratel Platform. It manages the context of an agent at runtime, in
process. It also connects the agent to the platform: the platform receives the runtime events and
the catalog snapshots, syncs the definitions and the intent graph back, and drafts improvements
for review. The platform is `https://app.ratel.sh` by default, or your own self-hosted platform
(see [Self-hosted platform](#self-hosted-platform)).

One option turns on the Ratel Platform for a core: `ratel(platform=...)`. With `RATEL_API_KEY` set,
`platform=True` is enough. The core then publishes its runtime events and its catalog snapshots,
and `r.platform` reaches the Platform API with the same key:

```python
from ratel_ai import ratel


async def main() -> None:
    r = ratel(platform=True)  # reads RATEL_API_KEY (and RATEL_BASE_URL)

    await r.tools.register([deploy_tool, rollback_tool])
    async with r.turn():
        hits = r.tools.search("roll back the production deploy", 2)
        if hits:
            await r.tools.invoke(hits[0].tool_id, {"service": "billing"})

    # The Platform API, from the same object and the same key.
    skills = (await r.platform.skills.list())["skills"]

    await r.platform.flush()  # deliver what is queued (also called by close())
    print(r.platform.status())  # delivery health of events and snapshots
    await r.platform.close()  # at shutdown
```

Without `platform`, `ratel()` runs no Platform code, does not import `ratel_ai.platform` and sends no
request, and `r.platform` is `None`. With `platform` and no API key, `ratel()` raises a
`ratel_ai.platform.PlatformError` (code `config`) that names `RATEL_API_KEY`. Delivery is fail-open: a
Platform failure never fails a search or an invoke. A runnable version is
[`examples/platform-python/main.py`](../../../examples/platform-python/main.py).

Pass a `PlatformOptions` dict to set the key and the URL in code, or to turn on more features:

```python
r = ratel(
    platform={
        "api_key": "rtl_…",
        "base_url": "https://app.ratel.sh/api/v1",
        "events": {"warn_on_failure": False},  # on by default; False turns it off
        "definitions": True,  # Platform owns the retrieval descriptions
        "intent_graph": True,  # sync the intent graph and rank with it
        "tool_picker": "precise",  # rank tools with the Platform Tool Picker (method="custom")
    }
)
await r.platform.ready()  # the first definitions pull and intent graph load ended
```

`r.platform` has `skills`, `suggestions`, `intents`, `jobs` and `runtime_catalog` (the management
clients), `tool_picker`, and the lifecycle methods `status()`, `verify()`, `ready()`, `flush()`,
`close()` and `refresh_definitions()`. Do not set `method` or `retrieve_fn` with `tool_picker`:
`ratel()` raises. `ratel(platform=...)` returns a `Ratel[RatelPlatformRuntime]`, so a type checker knows
that `r.platform` is set. `ratel()` returns a `Ratel[None]`.

**The event loop.** `ratel()` is synchronous, and the Platform features run on an asyncio event
loop. When you call `ratel(platform=...)` inside a running loop (for example in `async def main()` or
in the startup hook of your web framework), the features start at once on that loop. When you call
it before the loop starts (for example at import time), `r.platform` holds the runtime events (at
most `queue_capacity`, default 1,024, oldest dropped first). The features then start on the first
awaited `r.platform` call: `ready()`, `flush()`, `verify()`, `refresh_definitions()` or `close()`. That
call replays the held events. In that case, call `await r.platform.ready()` one time inside the loop,
at startup. The features stay on the loop that they started on.

### Platform settings

Each Platform feature resolves its key and URL in one place (`resolve_platform_config` of
`ratel_ai.platform`): the option, then the env variable, then the default.

| Option | Env variable | Default |
| --- | --- | --- |
| `platform["api_key"]` | `RATEL_API_KEY` | none: required |
| `platform["base_url"]` | `RATEL_BASE_URL` | `https://app.ratel.sh/api/v1` |
| `platform["events"]` | `RATEL_PLATFORM_EVENTS=off` turns runtime events off (deprecated: `RATEL_CLOUD_EVENTS`) | on |
| `platform["definitions"]` | — | off |
| `platform["intent_graph"]` | `RATEL_PLATFORM_INTENT_GRAPH=off` turns it off (deprecated: `RATEL_CLOUD_INTENT_GRAPH`) | off |
| `platform["tool_picker"]` | — | off (`True` means `"precise"`) |
| `platform["source_id"]` | — | the runtime's event source id |
| `platform["timeout"]` | — | the default of each feature (30 s for most requests) |
| `endpoint` of `configure_telemetry` | `RATEL_OTLP_ENDPOINT` | `${base_url}/traces` |
| `logs_endpoint` of `configure_telemetry` | `RATEL_OTLP_LOGS_ENDPOINT` | `${base_url}/logs` |

Python has two OTLP differences from TypeScript. With a custom traces URL and no logs setting,
Python sends logs to the `/v1/logs` sibling of that URL, but TypeScript sends them to
`${baseUrl}/logs`. With no API key, no base URL and no endpoint, Python raises `ValueError`, but
TypeScript uses `https://app.ratel.sh/api/v1`.

The SDK still reads the deprecated `RATEL_CLOUD_EVENTS` and `RATEL_CLOUD_INTENT_GRAPH` when the
`RATEL_PLATFORM_*` name is not set. When both are set, the `RATEL_PLATFORM_*` name wins.

`base_url` of `configure_telemetry` reads `RATEL_BASE_URL` too. `platform["timeout"]` is in
seconds. `platform["transport"]` replaces the HTTP transport of every feature (default
`ratel_ai.platform.UrllibTransport`). Tests pass a `MockPlatform`.

### Self-hosted platform

To use a self-hosted Ratel Platform, set its API root, including the `/api/v1` prefix, in one
place: the `RATEL_BASE_URL` env variable or `platform["base_url"]`. Each platform request and both
OTLP defaults (`${base_url}/traces` and `${base_url}/logs`) then go to that platform:

```sh
RATEL_API_KEY=rtl_… RATEL_BASE_URL=https://ratel.example.internal/api/v1 python agent.py
```

```python
r = ratel(platform={"base_url": "https://ratel.example.internal/api/v1"})
```

### Advanced: the separate functions

`ratel(platform=...)` wires the functions below. Use them directly to wire one feature yourself, or
for a runtime that `ratel()` does not create. They read the same settings. The Platform code uses the
standard library only:

| Import | What it holds |
| --- | --- |
| `ratel_ai.platform` | `RatelPlatformClient` (catalog writes, intent analysis, suggestion review over the v1 HTTP API), `resolve_platform_config`, `PlatformError` and the transports |
| `ratel_ai.platform.runtime` | Runtime attachment: `attach`, `attach_intent_graph_sync`, `ratel_platform` |
| `ratel_ai.platform.testing` | `MockPlatform`, an in-process fake of the Platform API, and `ManualClock` |

### Runtime events and catalog snapshots

`attach(runtime)` subscribes one runtime to fail-open delivery. Call it from a running event loop:

```python
from ratel_ai import ratel
from ratel_ai.platform.runtime import attach

runtime = ratel()
platform_runtime = attach(runtime, source_id="checkout-worker")
...
await platform_runtime.flush()  # in serverless handlers, before each invocation ends
await platform_runtime.close()  # at shutdown
```

Only the remotely publishable event types (`RUNTIME_EVENT_TYPES` of `ratel_ai.platform.runtime`)
leave the process. `attach()` publishes an initial catalog snapshot and refreshes it after
registration churn, behind a debounce (`snapshot_debounce_ms`, default 500 ms) with a maximum wait
of four debounce windows. Durable snapshots reconcile every five minutes
(`snapshot_reconcile_interval_ms`). Transient failures retry with an exponential backoff
(`retry=RetryOptions(...)`). Payload rejects (HTTP 400/413/415) go one time to `on_rejected` and
are dropped. Set `RATEL_PLATFORM_EVENTS=off` before `attach()` to turn event delivery off. Catalog
snapshots stay on.

`platform_runtime.status()` returns JSON-serializable delivery health (`overall`, `events`,
`snapshots`), and `await platform_runtime.verify()` probes event ingestion without a change to the
queues. `status()["overall"]` is `pending`, `ok`, `degraded`, `blocked` or `disabled`. The first
transition into each failing kind warns one time. Pass `warn_on_failure=False` to stop the
warnings, or `on_status_change` to observe the transitions.

#### Platform definitions

`attach(runtime, use_platform_definitions=True)` gives Ratel Platform ownership of the retrieval
descriptions of the live tools, skills and facts (`platform["definitions"]` of `ratel()`). It uses
`RuntimeCatalog.experimental_attach_definition_overrides` with a Platform source. The initial pull
runs in the background, and `flush()` and `close()` wait for it. Later pulls send the last ETag:

```python
await platform_runtime.refresh_platform_definitions()  # True when a changed overlay was applied
```

A failed initial pull keeps the local descriptions. A later failed refresh keeps the last applied
Platform descriptions.

#### Intent graph sync (adaptive usage ranking)

`attach_intent_graph_sync` moves the in-memory `IntentGraph` of a runtime to and from the
per-project, per-source-id graph storage of Ratel Platform (`platform["intent_graph"]` of `ratel()`). It
does not turn ranking on: you do that.

```python
from ratel_ai.platform.runtime import attach_intent_graph_sync

sync = await attach_intent_graph_sync(
    runtime,
    source_id="billing-agent",
    on_replaced=lambda graph: runtime.tools.catalog.experimental_enable_adaptive_ranking(graph),
)
runtime.tools.catalog.experimental_enable_adaptive_ranking(sync.graph)
```

In `"push"` mode (the default), it saves local usage behind a debounce (`debounce_ms`, default
15 s) and a maximum wait (`max_wait_ms`, default 60 s). A 409 loads the newer graph and calls
`on_replaced`. In `"consume"` mode, it reads a graph that Platform owns (`graph_key`) and polls for
newer revisions (`poll_interval_ms`, default 5 minutes, minimum 15 s). It never writes. Rank a
consumed graph with `learn=False`. A second call for the same runtime raises.
`RATEL_PLATFORM_INTENT_GRAPH=off` turns the sync off. Every failure is fail-open.

#### Tool Picker ranking (`retrieve_fn`)

`ratel_platform()` returns the Platform API of one configuration. Its `tool_picker` is a `RankFn` that
ranks with the Ratel Platform Tool Picker (`platform["tool_picker"]` of `ratel()`):

```python
from ratel_ai import ratel
from ratel_ai.platform.runtime import attach, ratel_platform

rc = ratel_platform()  # api_key defaults to RATEL_API_KEY
runtime = ratel(method="custom", retrieve_fn=rc.tool_picker)
platform_runtime = attach(runtime)

await runtime.tools.register(tools)
await platform_runtime.flush()  # publish the catalog snapshot before the first search

hits = await runtime.tools.search_async("roll back the last deploy", 5)
```

| Mode | Ranking | Client timeout |
|---|---|---|
| `instant` | BM25 only, no credits | 15 s |
| `precise` (default) | BM25 shortlist, then Jev | 15 s |
| `exhaustive` | Jev over the whole catalog. Platform stops at 45 s | 60 s |

`rc.tool_picker.with_mode("exhaustive")` returns a picker for another mode. The picker ranks the
tool catalog that the project synced to Platform, and sends only `{query, mode, top_k}`. Before the
first search, register your tools and `await flush()`. Skill candidates return `[]` without a
request. Every failure raises `ratel_ai.RetrieverError` with the same `code` and `transient`
value as the TypeScript SDK (`Config`, `InvalidRequest`, `Unauthorized`, `InsufficientCredits`,
`NoSyncedTools`, `RateLimited`, `Unavailable`, `Timeout`, `Unreachable`, `Malformed`, `Http`).

### Testing with `MockPlatform`

`MockPlatform` serves the Platform wire contract in process. Pass it as the transport:

```python
from ratel_ai import ratel
from ratel_ai.platform.testing import MockPlatform

mock = MockPlatform()
r = ratel(platform={"api_key": mock.api_key, "transport": mock})
await r.tools.register(tools)
await r.platform.flush()

assert mock.catalog_snapshots[-1]["tools"]
assert mock.runtime_events
```

It records `requests`, `received_event_batches`, `runtime_events`, `received_snapshots`,
`catalog_snapshots`, `intent_graphs` and `tool_picks`. Seed it with
`seed_runtime_catalog_override`, `seed_intent_graph` and `seed_tool_ranking`, script failures with
`script_responses`, and add routes with `add_route`.

## Retrieval experiments (experimental)

`experimental_define_experiment` wraps host-supplied async selectors with a deterministic A/B
assignment and bounded shadow evaluation, like `experimentalDefineExperiment` in `@ratel-ai/sdk`.
It is opt-in. It does not change the ranking of `ToolCatalog` or `SkillCatalog`. This API may
change without a major version bump.

```python
from ratel_ai import experimental_define_experiment, ratel

r = ratel()


async def control(query, context):
    return [{"id": "inspect-ci", "score": 1.0}, {"id": "search-logs", "score": 0.8}]


async def candidate(query, context):
    return [{"id": "search-logs", "score": 0.9}, {"id": "inspect-ci", "score": 0.7}]


experiment = experimental_define_experiment(
    {
        "id": "retrieval-v2",
        "arms": {"control": {"select": control}, "candidate": {"select": candidate}},
        "split": [{"arm": "control", "weight": 90}, {"arm": "candidate", "weight": 10}],
        "ranking": lambda hits: [{"id": h["id"], "score": h["score"]} for h in hits],
        "evaluation": {
            "k": 2,
            "references": [
                "peer-selection",
                {"kind": "invocation", "window": {"turns": 5, "max_age_ms": 300_000}},
            ],
            "outcome": True,
        },
        "fallback_arm": "control",
        "shadow_policy": {"concurrency": 1},
    },
    r.events,
)

selection = await experiment.select("build failure", unit_id="account-42", shadow=True, k=2)
if selection.result:
    experiment.report_invocation(unit_id="account-42", tool_id=selection.result[0]["id"])
experiment.report_outcome(selection_id=selection.selection_id, label="accepted", score=1)
print(selection.assigned_arm, selection.effective_arm, selection.result)
await experiment.drain()
```

An `Experiment` has five operations:

| operation | contract |
| --- | --- |
| `await select(params, *, unit_id, arm=None, shadow=False, k=None, transform=None, attributes=None)` | Returns an `ExperimentSelection`: the transformed `result`, `selection_id`, `assigned_arm`, `effective_arm` and the arm-callback `duration_ms`. An explicit `arm` overrides `split`. |
| `await warm()` | Starts the unresolved arm warmups at once. It never raises. A failed warmup stays cold and retries on the next call. A selection never waits for a warmup. |
| `await drain()` | Waits for the shadow and comparison work that started before the call. It never raises, can run again and does not close the experiment. |
| `report_invocation(*, unit_id, tool_id, turn=None)` | Attributes a tool to the configured in-process invocation window. It does nothing without an invocation reference. |
| `report_outcome(*, selection_id, label=None, score=None)` | Appends a delayed outcome when `evaluation.outcome` is true. It needs a non-empty label or a finite score. Repeated reports stay distinct. |

The assignment hashes the compact JSON array `[experiment_id, unit_id]` with SHA-256 into the
ordered integer weights, as TypeScript does: the same ids get the same arm in both SDKs
([`protocol/v1/conformance/experiment-split-vectors.json`](../../../protocol/v1/conformance/experiment-split-vectors.json)
pins it). Pass an opaque stable unit id. Telemetry records only the first 16 hex characters of
`SHA-256(unit_id)`. `shadow=True` runs every other arm in declaration order. Capacity is per
experiment instance, and an arm over it is skipped (`experiment_skip`, reason `capacity`), never
queued. The selection never waits for a shadow, and a shadow failure never changes the served
result: it records `experiment_drop` with the reason `arm-failed`, `selection-failed`,
`fallback-consumed`, `served-ranking-failed` or `comparison-failed`. When the assigned selector
raises, `fallback_arm` serves and can reuse a running shadow of that arm. The fallback does not run
for an empty ranking, a ranking failure or a `transform` error.

`transform` runs synchronously after the arm callback and before the ranking, the telemetry and the
return. An arm selector receives the caller params and an `ExperimentArmContext` with its `role`
(`serving` or `shadow`). It owns its deadlines: a `TimeoutError` is classified as a timeout. The
comparison uses ids only (top-1, exact order, overlap and Jaccard@K), never scores. The request `k`
overrides the configured value (default 10) and does not cut the result.

Each arm run opens a `ratel.experiment.arm` span with the five `ratel.experiment.*` baggage
entries, and its facts are OpenTelemetry log events (`ratel.experiment.results`, `.comparison`,
`.skip`, `.fallback`, `.drop`, `.invocation`, `.outcome`) with the attribute names of
`ratel_ai_telemetry`. With `r.events`, each fact also arrives as an `experiment_*` runtime event,
with the same `event_id` as its span or log event. Call descendant model and tool work inside the
arm selector so it inherits the experiment baggage. The runnable
[`examples/experiments-python`](../../../examples/experiments-python) runs a two-arm experiment.

## Facts (experimental)

Tools and skills are **pulled** — a query ranks them and only the winners reach the model. Facts are the opposite: constant content the agent should always work from (a shop's address, hours, a brand's voice), **pushed** into the context and deduplicated so it is injected once rather than every turn.

Facts live in the opt-in `ratel_ai.experimental` namespace and may change without a major version bump. Registering one is like a skill, plus a `pin` tier:

```python
from ratel_ai.experimental import Fact, FactCatalog, Pin

facts = FactCatalog()
await facts.register([
    Fact(
        id="shop-address",
        name="shop address & hours",
        description="where the shop is and when it's open",
        body="Fade & Blade — 12 Baker Street, London. Open Mon–Sat 9am–7pm.",
        pin=Pin.ALWAYS,       # every turn, regardless of the query
    ),
    Fact(
        id="cancellation",
        name="cancellation policy",
        description="cancelling or rescheduling a booking, and refunds",
        body="Cancel at least 24h ahead for a full refund; same-day is a 50% fee.",
        pin=Pin.RETRIEVED,    # only when the turn's query ranks it in (default)
    ),
])
```

Then pick **one** of two injection modes per turn.

**`ground()` — persist into your stored history.** Returns only the facts not already present; render each `body` verbatim and keep it in the messages you save. It takes a **list of per-message strings** — flatten multi-part content yourself, and note that a bare `str` is rejected (it is itself a `Sequence[str]`, so it would be iterated character by character):

```python
def text_of(message: dict) -> str:
    content = message["content"]
    if isinstance(content, str):
        return content
    return "\n".join(part.get("text", "") for part in content)  # multi-part content

result = await facts.ground(user_text, [text_of(m) for m in messages])
for item in result.inject:
    messages.append({"role": "system", "content": item.body})  # verbatim — presence is the dedupe
```

Turn 1 injects the address; turn 2 sees it in the transcript and injects nothing. It re-injects only when the body is gone (compaction) or was edited — `item.reason` is `"never"` / `"evicted"` / `"mutated"`.

**`ground_snapshot()` — per call, nothing stored.** Returns the full applicable set every time; put it in the request you're about to send and discard it:

```python
snapshot = await facts.ground_snapshot(user_text)
payload = [{"role": "system", "content": f.body} for f in snapshot] + messages
```

Use `ground()` for a long-lived agent whose messages you persist; `ground_snapshot()` for one-shot or stateless calls, or to keep injected content out of your stored history.

Facts are **host-driven**: the model-facing `search_capabilities` tool is unchanged and never returns facts — you decide what is true and inject it, rather than letting the model discover it. Every decision is traced (`fact_inject` with its reason, `fact_inject_skip`, `fact_snapshot`), so the skip rate — the tokens you saved — is measurable. See [ADR-0017](../../../docs/adr/0017-facts-and-injection-freshness.md).

Telemetry export is optional. With the `otlp` extra installed, `configure_telemetry()` reads `RATEL_OTLP_ENDPOINT` (falling back to the superseded `RATEL_URL`, which warns), then `RATEL_BASE_URL` (default `https://app.ratel.sh/api/v1`, with the `/traces` and `/logs` routes), `RATEL_OTLP_LOGS_ENDPOINT` and `RATEL_API_KEY`, wires trace and Logs exporters, and returns a shutdown handle. It exports only `gen_ai.*`/`ratel.*` signal spans and EventRecords by default — `export_all_spans=True` widens spans only. Message and tool content stays off by default; opt in with `capture_content`/`include_span_and_events` (see the [telemetry guide](https://docs.ratel.sh/docs/telemetry) for the capture modes and their privacy implications). Experimental catalog-definition export additionally requires `RATEL_EXPERIMENTAL_CATALOG_DEFINITIONS=true`. Changed definitions then emit one `ratel.catalog.definition` EventRecord per registry-local content hash. Hosts that already own OpenTelemetry providers add both `ratel_span_processor` and `ratel_log_record_processor` instead.

Package layout: `ratel_ai/` is the Python surface (including `embedding_artifact.py` for build/warm helpers), `native/` contains the PyO3 binding, and `tests/` exercises both. For local development, create `.venv` with `uv`, install `maturin`, `pytest`, `pytest-asyncio`, `ruff`, and `mypy`, then run `.venv/bin/maturin develop` and `.venv/bin/pytest`.
