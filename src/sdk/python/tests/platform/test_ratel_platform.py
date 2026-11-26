"""`ratel(platform=...)`: one option turns on Ratel Platform (port of ratel-platform.test.ts)."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import warnings
from typing import Any

import pytest

from ratel_ai import ExecutableTool, ratel
from ratel_ai.platform import PlatformError, UrllibTransport
from ratel_ai.platform.runtime import RetryOptions
from ratel_ai.platform.testing import MockPlatform, MockToolPick

MOCK_BASE_URL = "https://mock.test/api/v1"


def _tool(tool_id: str, description: str, result: str) -> ExecutableTool:
    return ExecutableTool(
        id=tool_id,
        name=tool_id,
        description=description,
        input_schema={"type": "object"},
        output_schema={"type": "string"},
        execute=lambda _args: result,
    )


DEPLOY = _tool("deploy", "Prepare a production release.", "deployed")
ROLLBACK = _tool("rollback", "Roll back the last deploy.", "rolled back")


def _platform(mock: MockPlatform, **options: Any) -> dict[str, Any]:
    return {"api_key": mock.api_key, "base_url": MOCK_BASE_URL, "transport": mock, **options}


async def test_publishes_events_and_a_catalog_snapshot(mock: MockPlatform) -> None:
    r = ratel(platform=_platform(mock))
    await r.tools.register([DEPLOY, ROLLBACK])
    r.tools.search("roll back the deploy", 3)
    await r.tools.invoke("rollback", {})
    await r.platform.flush()

    assert "search" in [event["type"] for event in mock.runtime_events]
    assert sorted(tool["id"] for tool in mock.catalog_snapshots[-1]["tools"]) == [
        "deploy",
        "rollback",
    ]
    assert r.platform.status()["events"]["accepted"] > 0
    await r.platform.close()


async def test_platform_true_reads_the_key_from_the_env(
    mock: MockPlatform, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RATEL_API_KEY", mock.api_key)
    monkeypatch.setenv("RATEL_BASE_URL", MOCK_BASE_URL)
    monkeypatch.setattr(UrllibTransport, "send", lambda _self, request: mock.send(request))

    r = ratel(platform=True)
    await r.tools.register([DEPLOY, ROLLBACK])
    r.tools.search("roll back the deploy", 3)
    await r.tools.invoke("rollback", {})
    await r.platform.flush()

    assert "search" in [event["type"] for event in mock.runtime_events]
    assert len(mock.catalog_snapshots) == 1
    assert all(
        request.headers["authorization"] == f"Bearer {mock.api_key}" for request in mock.requests
    )
    await r.platform.close()


def test_a_core_made_before_the_loop_starts_delivers_on_the_first_flush() -> None:
    mock = MockPlatform()
    r = ratel(platform=_platform(mock))

    async def main() -> None:
        await r.tools.register([DEPLOY, ROLLBACK])
        r.tools.search("roll back the deploy", 3)
        await r.tools.invoke("rollback", {})
        await asyncio.sleep(0.2)
        assert mock.requests == []
        await r.platform.flush()
        await r.platform.close()

    asyncio.run(main())

    types = {event["type"] for event in mock.runtime_events}
    assert {"index_churn", "search", "invoke_start"} <= types
    assert len(mock.catalog_snapshots) == 1


async def test_reaches_the_management_api_with_the_same_key(mock: MockPlatform) -> None:
    r = ratel(platform=_platform(mock, events=False))

    created = await r.platform.skills.create(
        {"name": "deploy-checklist", "description": "How to deploy safely.", "body": "# Deploy"}
    )
    listed = await r.platform.skills.list()

    assert mock.skills[created["id"]]["name"] == "deploy-checklist"
    assert [skill["name"] for skill in listed["skills"]] == ["deploy-checklist"]
    await r.platform.close()


async def test_publishes_nothing_with_events_off(mock: MockPlatform) -> None:
    r = ratel(platform=_platform(mock, events=False))
    await r.tools.register([DEPLOY])
    r.tools.search("deploy", 3)
    await r.platform.flush()

    assert mock.requests == []
    assert r.platform.status()["events"]["accepted"] == 0
    await r.platform.close()


async def test_the_deprecated_ratel_cloud_events_off_still_disables_events(
    mock: MockPlatform, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RATEL_PLATFORM_EVENTS", raising=False)
    monkeypatch.setenv("RATEL_CLOUD_EVENTS", "off")
    r = ratel(platform=_platform(mock))
    await r.tools.register([DEPLOY])
    r.tools.search("deploy", 3)
    await r.platform.flush()

    assert mock.runtime_events == []
    assert r.platform.status()["overall"] == "disabled"
    await r.platform.close()


async def test_ratel_platform_events_wins_over_ratel_cloud_events(
    mock: MockPlatform, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RATEL_CLOUD_EVENTS", "off")
    monkeypatch.setenv("RATEL_PLATFORM_EVENTS", "on")
    r = ratel(platform=_platform(mock))
    await r.tools.register([DEPLOY])
    r.tools.search("deploy", 3)
    await r.platform.flush()

    assert "search" in [event["type"] for event in mock.runtime_events]
    await r.platform.close()


async def test_the_deprecated_ratel_cloud_intent_graph_off_still_skips_the_graph(
    mock: MockPlatform, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RATEL_PLATFORM_INTENT_GRAPH", raising=False)
    monkeypatch.setenv("RATEL_CLOUD_INTENT_GRAPH", "off")
    r = ratel(platform=_platform(mock, events=False, intent_graph=True))

    await r.platform.ready()

    assert mock.requests == []
    await r.platform.close()


async def test_ratel_platform_intent_graph_wins_over_ratel_cloud_intent_graph(
    mock: MockPlatform, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RATEL_CLOUD_INTENT_GRAPH", "off")
    monkeypatch.setenv("RATEL_PLATFORM_INTENT_GRAPH", "on")
    r = ratel(
        events={"source_id": "billing-agent"},
        platform=_platform(mock, events=False, intent_graph=True),
    )

    await r.platform.ready()

    assert [request.url for request in mock.requests] == [
        f"{MOCK_BASE_URL}/intent-graph?source=billing-agent"
    ]
    await r.platform.close()


@pytest.mark.parametrize(
    ("base_url_env", "root"),
    [
        (None, "https://app.ratel.sh/api/v1"),
        ("https://ratel.internal.test/api/v1", "https://ratel.internal.test/api/v1"),
    ],
)
async def test_each_request_and_both_otlp_urls_use_one_root(
    mock: MockPlatform,
    monkeypatch: pytest.MonkeyPatch,
    base_url_env: str | None,
    root: str,
) -> None:
    from ratel_ai_telemetry.otlp import resolve_otlp_config

    for name in ("RATEL_BASE_URL", "RATEL_OTLP_ENDPOINT", "RATEL_OTLP_LOGS_ENDPOINT", "RATEL_URL"):
        monkeypatch.delenv(name, raising=False)
    if base_url_env is not None:
        monkeypatch.setenv("RATEL_BASE_URL", base_url_env)
    monkeypatch.setenv("RATEL_API_KEY", mock.api_key)
    monkeypatch.setattr(UrllibTransport, "send", lambda _self, request: mock.send(request))

    r = ratel(platform=True)
    await r.tools.register([DEPLOY])
    r.tools.search("deploy", 3)
    await r.platform.skills.list()
    await r.platform.flush()
    otlp = resolve_otlp_config()

    urls = [f"{request.method} {request.url}" for request in mock.requests]
    assert f"GET {root}/skills" in urls
    assert f"POST {root}/events" in urls
    assert all(request.url.startswith(f"{root}/") for request in mock.requests)
    assert (otlp.url, otlp.logs_url) == (f"{root}/traces", f"{root}/logs")
    await r.platform.close()


async def test_applies_platform_definitions(mock: MockPlatform) -> None:
    mock.seed_runtime_catalog_override(
        {"kind": "tool", "entryId": "deploy", "searchableDescription": "quasar zephyr rollback"}
    )
    r = ratel(platform=_platform(mock, definitions=True))
    await r.tools.register([DEPLOY, ROLLBACK])

    await r.platform.ready()
    await r.platform.refresh_definitions()

    assert [hit.tool_id for hit in r.tools.search("quasar zephyr", 3)] == ["deploy"]
    await r.platform.close()


async def test_intent_graph_loads_ranks_and_pushes_the_graph(mock: MockPlatform) -> None:
    r = ratel(
        events={"source_id": "billing-agent"},
        platform=_platform(mock, events=False, intent_graph=True),
    )
    await r.tools.register([DEPLOY, ROLLBACK])

    await r.platform.ready()
    assert str(r.tools.catalog.experimental_adaptive_ranking_status).startswith("active")

    r.tools.catalog.experimental_record_baseline_turn(
        query="roll back production", invoked=["rollback"]
    )
    await r.platform.flush()

    assert "billing-agent" in mock.intent_graphs
    assert mock.intent_graphs["billing-agent"].rev > 0
    await r.platform.close()


async def test_tool_picker_ranks_the_search(mock: MockPlatform) -> None:
    r = ratel(platform=_platform(mock, tool_picker="precise"))
    await r.tools.register([DEPLOY, ROLLBACK])
    await r.platform.flush()

    hits = await r.tools.search_async("roll back", 2)

    assert hits[0].tool_id == "rollback"
    assert mock.tool_picks == [MockToolPick(query="roll back", mode="precise", top_k=2)]
    assert r.platform.tool_picker.mode == "precise"
    await r.platform.close()


def test_raises_at_construction_with_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RATEL_API_KEY", raising=False)

    with pytest.raises(PlatformError, match="RATEL_API_KEY") as raised:
        ratel(platform=True)
    assert raised.value.code == "config"


async def _no_ranking(_query: str, _candidates: list[Any], _top_k: int) -> list[Any]:
    return []


@pytest.mark.parametrize(
    "ranking", [{"method": "bm25"}, {"method": "custom", "retrieve_fn": _no_ranking}]
)
def test_tool_picker_with_a_caller_ranking_raises(ranking: dict[str, Any]) -> None:
    with pytest.raises(PlatformError, match="tool_picker"):
        ratel(**ranking, platform={"api_key": "rtl_k", "tool_picker": True})


async def test_ready_never_raises() -> None:
    async def offline(_request: Any) -> Any:
        raise OSError("offline")

    class _Offline:
        send = staticmethod(offline)

    r = ratel(
        platform={
            "api_key": "rtl_k",
            "base_url": MOCK_BASE_URL,
            "transport": _Offline(),
            "definitions": True,
            "intent_graph": {"on_error": lambda _error: None},
            "events": {"retry": RetryOptions(max_attempts=1), "warn_on_failure": False},
        }
    )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        await r.platform.ready()
        await r.platform.close()


_NO_PLATFORM_SCRIPT = """
import asyncio, socket, sys, urllib.request

def refuse(*_args, **_kwargs):
    raise AssertionError("ratel() must not touch the network")

urllib.request.urlopen = refuse
socket.socket.connect = refuse

from ratel_ai import ExecutableTool, ratel

async def main():
    r = ratel()
    await r.tools.register([ExecutableTool(
        id="deploy", name="deploy", description="Prepare a production release.",
        execute=lambda _args: "deployed",
    )])
    hits = r.tools.search("deploy", 3)
    result = await r.tools.invoke("deploy", {})
    await r.recall("deploy")
    await asyncio.sleep(0.02)
    assert [hit.tool_id for hit in hits] == ["deploy"], hits
    assert result == "deployed", result
    assert r.platform is None

asyncio.run(main())
assert "ratel_ai.platform" not in sys.modules, sorted(m for m in sys.modules if "platform" in m)
print("ok")
"""


def test_without_platform_no_platform_code_loads_and_no_request_is_sent() -> None:
    env = {**os.environ, "RATEL_API_KEY": "rtl_ambient"}
    result = subprocess.run(
        [sys.executable, "-c", _NO_PLATFORM_SCRIPT],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "ok"
