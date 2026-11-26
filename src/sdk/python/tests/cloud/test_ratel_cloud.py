"""`ratel(cloud=...)`: one option turns on Ratel Cloud (port of ratel-cloud.test.ts)."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import warnings
from typing import Any

import pytest

from ratel_ai import ExecutableTool, ratel
from ratel_ai.cloud import CloudError, UrllibTransport
from ratel_ai.cloud.runtime import RetryOptions
from ratel_ai.cloud.testing import MockCloud, MockToolPick

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


def _cloud(mock: MockCloud, **options: Any) -> dict[str, Any]:
    return {"api_key": mock.api_key, "base_url": MOCK_BASE_URL, "transport": mock, **options}


async def test_publishes_events_and_a_catalog_snapshot(mock: MockCloud) -> None:
    r = ratel(cloud=_cloud(mock))
    await r.tools.register([DEPLOY, ROLLBACK])
    r.tools.search("roll back the deploy", 3)
    await r.tools.invoke("rollback", {})
    await r.cloud.flush()

    assert "search" in [event["type"] for event in mock.runtime_events]
    assert sorted(tool["id"] for tool in mock.catalog_snapshots[-1]["tools"]) == [
        "deploy",
        "rollback",
    ]
    assert r.cloud.status()["events"]["accepted"] > 0
    await r.cloud.close()


async def test_cloud_true_reads_the_key_from_the_env(
    mock: MockCloud, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RATEL_API_KEY", mock.api_key)
    monkeypatch.setenv("RATEL_BASE_URL", MOCK_BASE_URL)
    monkeypatch.setattr(UrllibTransport, "send", lambda _self, request: mock.send(request))

    r = ratel(cloud=True)
    await r.tools.register([DEPLOY, ROLLBACK])
    r.tools.search("roll back the deploy", 3)
    await r.tools.invoke("rollback", {})
    await r.cloud.flush()

    assert "search" in [event["type"] for event in mock.runtime_events]
    assert len(mock.catalog_snapshots) == 1
    assert all(
        request.headers["authorization"] == f"Bearer {mock.api_key}" for request in mock.requests
    )
    await r.cloud.close()


def test_a_core_made_before_the_loop_starts_delivers_on_the_first_flush() -> None:
    mock = MockCloud()
    r = ratel(cloud=_cloud(mock))

    async def main() -> None:
        await r.tools.register([DEPLOY, ROLLBACK])
        r.tools.search("roll back the deploy", 3)
        await r.tools.invoke("rollback", {})
        await asyncio.sleep(0.2)
        assert mock.requests == []
        await r.cloud.flush()
        await r.cloud.close()

    asyncio.run(main())

    types = {event["type"] for event in mock.runtime_events}
    assert {"index_churn", "search", "invoke_start"} <= types
    assert len(mock.catalog_snapshots) == 1


async def test_reaches_the_management_api_with_the_same_key(mock: MockCloud) -> None:
    r = ratel(cloud=_cloud(mock, events=False))

    created = await r.cloud.skills.create(
        {"name": "deploy-checklist", "description": "How to deploy safely.", "body": "# Deploy"}
    )
    listed = await r.cloud.skills.list()

    assert mock.skills[created["id"]]["name"] == "deploy-checklist"
    assert [skill["name"] for skill in listed["skills"]] == ["deploy-checklist"]
    await r.cloud.close()


async def test_publishes_nothing_with_events_off(mock: MockCloud) -> None:
    r = ratel(cloud=_cloud(mock, events=False))
    await r.tools.register([DEPLOY])
    r.tools.search("deploy", 3)
    await r.cloud.flush()

    assert mock.requests == []
    assert r.cloud.status()["events"]["accepted"] == 0
    await r.cloud.close()


async def test_applies_cloud_definitions(mock: MockCloud) -> None:
    mock.seed_runtime_catalog_override(
        {"kind": "tool", "entryId": "deploy", "searchableDescription": "quasar zephyr rollback"}
    )
    r = ratel(cloud=_cloud(mock, definitions=True))
    await r.tools.register([DEPLOY, ROLLBACK])

    await r.cloud.ready()
    await r.cloud.refresh_definitions()

    assert [hit.tool_id for hit in r.tools.search("quasar zephyr", 3)] == ["deploy"]
    await r.cloud.close()


async def test_intent_graph_loads_ranks_and_pushes_the_graph(mock: MockCloud) -> None:
    r = ratel(
        events={"source_id": "billing-agent"},
        cloud=_cloud(mock, events=False, intent_graph=True),
    )
    await r.tools.register([DEPLOY, ROLLBACK])

    await r.cloud.ready()
    assert str(r.tools.catalog.experimental_adaptive_ranking_status).startswith("active")

    r.tools.catalog.experimental_record_baseline_turn(
        query="roll back production", invoked=["rollback"]
    )
    await r.cloud.flush()

    assert "billing-agent" in mock.intent_graphs
    assert mock.intent_graphs["billing-agent"].rev > 0
    await r.cloud.close()


async def test_tool_picker_ranks_the_search(mock: MockCloud) -> None:
    r = ratel(cloud=_cloud(mock, tool_picker="precise"))
    await r.tools.register([DEPLOY, ROLLBACK])
    await r.cloud.flush()

    hits = await r.tools.search_async("roll back", 2)

    assert hits[0].tool_id == "rollback"
    assert mock.tool_picks == [MockToolPick(query="roll back", mode="precise", top_k=2)]
    assert r.cloud.tool_picker.mode == "precise"
    await r.cloud.close()


def test_raises_at_construction_with_no_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RATEL_API_KEY", raising=False)

    with pytest.raises(CloudError, match="RATEL_API_KEY") as raised:
        ratel(cloud=True)
    assert raised.value.code == "config"


async def _no_ranking(_query: str, _candidates: list[Any], _top_k: int) -> list[Any]:
    return []


@pytest.mark.parametrize(
    "ranking", [{"method": "bm25"}, {"method": "custom", "retrieve_fn": _no_ranking}]
)
def test_tool_picker_with_a_caller_ranking_raises(ranking: dict[str, Any]) -> None:
    with pytest.raises(CloudError, match="tool_picker"):
        ratel(**ranking, cloud={"api_key": "rtl_k", "tool_picker": True})


async def test_ready_never_raises() -> None:
    async def offline(_request: Any) -> Any:
        raise OSError("offline")

    class _Offline:
        send = staticmethod(offline)

    r = ratel(
        cloud={
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
        await r.cloud.ready()
        await r.cloud.close()


_NO_CLOUD_SCRIPT = """
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
    assert r.cloud is None

asyncio.run(main())
assert "ratel_ai.cloud" not in sys.modules, sorted(m for m in sys.modules if "cloud" in m)
print("ok")
"""


def test_without_cloud_no_cloud_code_loads_and_no_request_is_sent() -> None:
    env = {**os.environ, "RATEL_API_KEY": "rtl_ambient"}
    result = subprocess.run(
        [sys.executable, "-c", _NO_CLOUD_SCRIPT],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "ok"
