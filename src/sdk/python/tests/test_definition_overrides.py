"""Experimental definition overrides: the Python port of the overlay cases in ratel.test.ts."""

from __future__ import annotations

import asyncio
import json
import threading
import warnings
from collections.abc import Iterator, Mapping, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from ratel_ai import ExecutableTool, Ratel, Skill, ratel
from ratel_ai.experimental import (
    DefinitionOverlayError,
    ExperimentalDefinitionOverride,
    Fact,
)

# Shadowing a local retrieval description warns; one test asserts that warning.
pytestmark = pytest.mark.filterwarnings("ignore:ratel. definition override:RuntimeWarning")


def _tool(tool_id: str, description: str, searchable: str | None = None) -> ExecutableTool:
    return ExecutableTool(
        id=tool_id,
        name=tool_id,
        description=description,
        experimental_searchable_description=searchable,
        execute=lambda _args: {"ok": True},
    )


def _override(kind: str, entry_id: str, text: str) -> dict[str, Any]:
    return {"kind": kind, "entry_id": entry_id, "searchable_description": text}


class _Source:
    """An overlay source that answers from a fixed list and records each ETag it got."""

    def __init__(self, responses: Sequence[Mapping[str, Any]]) -> None:
        self.responses = list(responses)
        self.requests: list[str | None] = []

    async def fetch(self, if_none_match: str | None = None) -> Mapping[str, Any]:
        self.requests.append(if_none_match)
        if not self.responses:
            raise AssertionError("unexpected overlay fetch")
        return self.responses.pop(0)


def _tool_ids(r: Ratel, query: str) -> list[str]:
    return [hit.tool_id for hit in r.tools.search(query, 5)]


def _facts(r: Ratel) -> Any:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return r.facts


async def test_an_overlay_changes_the_first_bm25_hit_and_its_removal_restores_it() -> None:
    r = ratel()
    await r.tools.register(
        [
            _tool("deploy_app", "Ship the application to production servers."),
            _tool("rollback_app", "Revert the last release."),
        ]
    )
    assert _tool_ids(r, "ship to production")[0] == "deploy_app"
    source = _Source(
        [
            {
                "status": 200,
                "etag": '"overlay-1"',
                "body": {
                    "overrides": [
                        _override("tool", "rollback_app", "ship to production undo revert")
                    ]
                },
            },
            {"status": 200, "etag": '"overlay-2"', "body": {"overrides": []}},
        ]
    )

    attachment = await r.catalog.experimental_attach_definition_overrides(source)
    assert _tool_ids(r, "ship to production undo")[0] == "rollback_app"

    assert await attachment.refresh() is True
    assert _tool_ids(r, "ship to production undo")[0] == "deploy_app"


async def test_attach_applies_tool_and_skill_overrides_and_keeps_local_snapshot() -> None:
    r = ratel(events={"source_id": "src-1"})
    await r.tools.register(_tool("deploy", "Deploy an application.", "localcanaryterm"))
    await r.skills.register(
        Skill(id="deploy-skill", name="deploy-skill", description="Deploy an application.")
    )
    snapshot_before = r.catalog.snapshot()
    source = _Source(
        [
            {
                "status": 200,
                "etag": '"overlay-1"',
                "body": {
                    "overrides": [
                        _override("tool", "deploy", "overriderollbackterm"),
                        _override("skill", "deploy-skill", "overrideskillterm"),
                    ]
                },
            },
            {"status": 304},
            {"status": 200, "etag": '"overlay-2"', "body": {"overrides": []}},
        ]
    )

    attachment = await r.catalog.experimental_attach_definition_overrides(source)

    assert _tool_ids(r, "overriderollbackterm") == ["deploy"]
    assert _tool_ids(r, "localcanaryterm") == []
    assert [hit.skill_id for hit in r.skills.search("overrideskillterm", 5)] == ["deploy-skill"]
    assert r.catalog.snapshot() == snapshot_before
    assert r.tools.get("deploy") is not None
    assert r.tools.get("deploy").description == "Deploy an application."  # type: ignore[union-attr]

    assert await attachment.refresh() is False
    assert _tool_ids(r, "overriderollbackterm") == ["deploy"]

    assert await attachment.refresh() is True
    assert _tool_ids(r, "localcanaryterm") == ["deploy"]
    assert _tool_ids(r, "overriderollbackterm") == []
    assert source.requests == [None, '"overlay-1"', '"overlay-1"']


async def test_concurrent_refreshes_share_one_request() -> None:
    r = ratel()
    gate = asyncio.Event()
    requests: list[str | None] = []

    class GatedSource:
        async def fetch(self, if_none_match: str | None = None) -> Mapping[str, Any]:
            requests.append(if_none_match)
            if len(requests) == 1:
                return {"status": 200, "etag": '"overlay-1"', "body": {"overrides": []}}
            if len(requests) == 2:
                await gate.wait()
                return {"status": 200, "etag": '"overlay-2"', "body": {"overrides": []}}
            return {"status": 304}

    attachment = await r.catalog.experimental_attach_definition_overrides(GatedSource())
    first = asyncio.ensure_future(attachment.refresh())
    second = asyncio.ensure_future(attachment.refresh())
    for _ in range(10):
        await asyncio.sleep(0)
    assert requests == [None, '"overlay-1"']
    gate.set()

    assert await asyncio.gather(first, second) == [True, True]
    assert await attachment.refresh() is False
    assert requests == [None, '"overlay-1"', '"overlay-2"']


async def test_a_rejected_refresh_keeps_the_last_accepted_etag() -> None:
    r = ratel()
    source = _Source(
        [
            {"status": 200, "etag": '"overlay-1"', "body": {"overrides": []}},
            {"status": 200, "etag": "", "body": {"overrides": []}},
            {"status": 304},
        ]
    )
    attachment = await r.catalog.experimental_attach_definition_overrides(source)

    with pytest.raises(DefinitionOverlayError) as raised:
        await attachment.refresh()

    assert raised.value.code == "invalid_etag"
    assert await attachment.refresh() is False
    assert source.requests == [None, '"overlay-1"', '"overlay-1"']


async def test_a_malformed_override_set_changes_no_catalog_now_or_later() -> None:
    r = ratel()
    await r.tools.register(_tool("deploy", "Deploy an application.", "localtoolterm"))
    source = _Source(
        [
            {
                "status": 200,
                "etag": '"overlay-1"',
                "body": {
                    "overrides": [
                        _override("tool", "deploy", "overridetoolterm"),
                        {"kind": "skill", "entry_id": "deploy-skill", "searchable_description": 1},
                        _override("fact", "address", "overridefactterm"),
                    ]
                },
            }
        ]
    )

    with pytest.raises(
        DefinitionOverlayError,
        match="definition overlay override 1 searchable_description must be a string",
    ):
        await r.catalog.experimental_attach_definition_overrides(source)

    assert _tool_ids(r, "localtoolterm") == ["deploy"]
    assert _tool_ids(r, "overridetoolterm") == []
    facts = _facts(r)
    await facts.register(
        Fact(
            id="address",
            name="address",
            description="Where the shop is.",
            experimental_searchable_description="localfactterm",
        )
    )
    assert [hit.fact_id for hit in facts.search("localfactterm", 5)] == ["address"]
    assert facts.search("overridefactterm", 5) == []


@pytest.mark.parametrize(
    ("response", "code", "message"),
    [
        (
            {"status": 500},
            "invalid_status",
            "definition overlay response status must be 200 or 304",
        ),
        (
            {"status": 200, "etag": '"overlay-1"', "body": None},
            "invalid_payload",
            "definition overlay response body must be an object",
        ),
        (
            {"status": 200, "etag": '"overlay-1"', "body": {"overrides": "invalid"}},
            "invalid_payload",
            "definition overlay overrides must be an array",
        ),
        (
            {
                "status": 200,
                "etag": '"overlay-1"',
                "body": {"overrides": [_override("prompt", "deploy", "Deploy safely")]},
            },
            "invalid_payload",
            "definition overlay override 0 kind must be tool, skill, or fact",
        ),
        (
            {"status": 200, "etag": " ", "body": {"overrides": []}},
            "invalid_etag",
            "definition overlay response etag must be a non-empty string",
        ),
        (
            {
                "status": 200,
                "etag": '"overlay-1"',
                "body": {"overrides": [_override("tool", "x" * 513, "Deploy safely")]},
            },
            "invalid_payload",
            "definition overlay override 0 entry_id exceeds 512 UTF-8 bytes",
        ),
        (
            {
                "status": 200,
                "etag": '"overlay-1"',
                "body": {"overrides": [_override("tool", "deploy", "x" * 16_385)]},
            },
            "invalid_payload",
            "definition overlay override 0 searchable_description exceeds 16384 UTF-8 bytes",
        ),
        (
            {"status": 200, "etag": '"overlay-1"', "body": {"overrides": [None]}},
            "invalid_payload",
            "definition overlay override 0 must be an object",
        ),
    ],
)
async def test_rejects_an_invalid_source_response(
    response: Mapping[str, Any], code: str, message: str
) -> None:
    r = ratel()

    with pytest.raises(DefinitionOverlayError) as raised:
        await r.catalog.experimental_attach_definition_overrides(_Source([response]))

    assert raised.value.code == code
    assert str(raised.value) == message


async def test_overrides_reach_skills_and_facts_registered_after_attach() -> None:
    r = ratel()
    await r.catalog._apply_definition_overrides(
        [
            ExperimentalDefinitionOverride("skill", "deploy", "overrideskillterm"),
            ExperimentalDefinitionOverride("fact", "address", "overridelocationterm"),
        ]
    )

    await r.skills.register(
        Skill(
            id="deploy",
            name="deploy",
            description="Deploy an application.",
            experimental_searchable_description="localcanaryterm",
        )
    )
    facts = _facts(r)
    await facts.register(
        Fact(
            id="address",
            name="address",
            description="Where the shop is.",
            experimental_searchable_description="localaddressterm",
        )
    )

    assert [hit.skill_id for hit in r.skills.search("overrideskillterm", 5)] == ["deploy"]
    assert r.skills.search("localcanaryterm", 5) == []
    assert [hit.fact_id for hit in facts.search("overridelocationterm", 5)] == ["address"]
    assert facts.search("localaddressterm", 5) == []


async def test_skill_replace_all_applies_the_overlay() -> None:
    r = ratel()
    await r.catalog._apply_definition_overrides(
        [ExperimentalDefinitionOverride("skill", "deploy", "overrideskillterm")]
    )

    await r.skills.replace_all(
        [Skill(id="deploy", name="deploy", description="Deploy an application.")]
    )
    assert [hit.skill_id for hit in r.skills.search("overrideskillterm", 5)] == ["deploy"]

    await r.catalog._apply_definition_overrides([])
    assert r.skills.search("overrideskillterm", 5) == []
    assert [hit.skill_id for hit in r.skills.search("application", 5)] == ["deploy"]


async def test_removal_restores_the_registered_value_until_it_is_re_registered() -> None:
    r = ratel()
    tool = _tool("deploy", "Original tool", "originaltoolterm")
    tool.input_schema = {"type": "object", "properties": {"target": {"type": "string"}}}
    skill = Skill(
        id="deploy-skill",
        name="deploy-skill",
        description="Original skill",
        experimental_searchable_description="originalskillterm",
    )
    await r.tools.register(tool)
    await r.skills.register(skill)
    overrides = [
        ExperimentalDefinitionOverride("tool", "deploy", "overridetoolterm"),
        ExperimentalDefinitionOverride("skill", "deploy-skill", "overrideskillterm"),
    ]
    await r.catalog._apply_definition_overrides(overrides)

    tool.experimental_searchable_description = "mutatedtoolterm"
    tool.input_schema["properties"]["target"]["type"] = "number"
    skill.experimental_searchable_description = "mutatedskillterm"
    await r.catalog._apply_definition_overrides([])

    assert _tool_ids(r, "originaltoolterm") == ["deploy"]
    assert [hit.skill_id for hit in r.skills.search("originalskillterm", 5)] == ["deploy-skill"]
    assert r.catalog.snapshot()["tools"][0]["input_schema"] == {
        "type": "object",
        "properties": {"target": {"type": "string"}},
    }

    await r.tools.register(tool)
    await r.skills.register(skill)
    assert _tool_ids(r, "mutatedtoolterm") == ["deploy"]
    assert [hit.skill_id for hit in r.skills.search("mutatedskillterm", 5)] == ["deploy-skill"]


async def test_warns_once_per_continuous_shadowing_period() -> None:
    r = ratel()
    await r.tools.register(_tool("deploy", "Deploy an application.", "localcanaryterm"))
    override = ExperimentalDefinitionOverride("tool", "deploy", "overriderollbackterm")

    with pytest.warns(RuntimeWarning) as recorded:
        await r.catalog._apply_definition_overrides([override])
        await r.catalog._apply_definition_overrides([override])
        await r.catalog._apply_definition_overrides([])
        await r.catalog._apply_definition_overrides([override])

    messages = [str(w.message) for w in recorded]
    assert (
        messages
        == [
            'ratel: definition override for tool "deploy" shadows the local '
            "experimental_searchable_description while definition overrides are enabled"
        ]
        * 2
    )


async def test_does_not_warn_without_a_local_value_or_for_an_unregistered_id() -> None:
    r = ratel()
    await r.tools.register(_tool("deploy", "Deploy an application."))

    with warnings.catch_warnings():
        warnings.simplefilter("error")
        await r.catalog._apply_definition_overrides(
            [
                ExperimentalDefinitionOverride("tool", "deploy", "overriderollbackterm"),
                ExperimentalDefinitionOverride("tool", "unknown", "overrideunknownterm"),
            ]
        )

    assert _tool_ids(r, "overriderollbackterm") == ["deploy"]


async def test_a_failed_apply_restores_every_catalog(
    failing_embedding_endpoint: tuple[str, threading.Event],
) -> None:
    url, failing = failing_embedding_endpoint
    r = ratel(method="semantic", embedding={"url": url, "model": "m"})
    await r.tools.register(_tool("deploy", "Deploy an application.", "localtoolterm"))
    await r.skills.register(
        Skill(
            id="deploy-skill",
            name="deploy-skill",
            description="Deploy an application.",
            experimental_searchable_description="localskillterm",
        )
    )
    failing.set()

    with pytest.raises(DefinitionOverlayError) as raised:
        await r.catalog._apply_definition_overrides(
            [
                ExperimentalDefinitionOverride("tool", "deploy", "overridetoolterm"),
                ExperimentalDefinitionOverride("skill", "deploy-skill", "overrideskillterm"),
            ]
        )

    assert raised.value.code == "apply_failed"
    assert raised.value.__cause__ is not None
    tool_hits = r.tools.catalog.search("localtoolterm", 5, "direct", "bm25")
    assert [hit.tool_id for hit in tool_hits] == ["deploy"]
    assert r.tools.catalog.search("overridetoolterm", 5, "direct", "bm25") == []
    skill_hits = r.skills.search("localskillterm", 5, "direct", "bm25")
    assert [hit.skill_id for hit in skill_hits] == ["deploy-skill"]
    assert r.skills.search("overrideskillterm", 5, "direct", "bm25") == []


@pytest.fixture
def failing_embedding_endpoint() -> Iterator[tuple[str, threading.Event]]:
    """An OpenAI-compatible embedding endpoint that answers 500 once `failing` is set."""
    failing = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("content-length", "0"))
            payload = json.loads(self.rfile.read(length))
            if failing.is_set():
                self.send_response(500)
                self.end_headers()
                return
            data = [
                {"embedding": [1.0, float(index + 1)], "index": index}
                for index, _ in enumerate(payload["input"])
            ]
            body = json.dumps({"data": data, "model": payload["model"]}).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/embeddings", failing
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()
