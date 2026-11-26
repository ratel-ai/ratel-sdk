"""The `ratel()` core: the Python mirror of ratel.test.ts (without adaptTo)."""

from __future__ import annotations

import warnings
from typing import Any

import pytest

from ratel_ai import (
    GET_SKILL_CONTENT_ID,
    INVOKE_TOOL_ID,
    SEARCH_CAPABILITIES_ID,
    ExecutableTool,
    Ratel,
    Skill,
    ToolCatalog,
    current_turn_id,
    ratel,
)
from ratel_ai.experimental import Fact


def _tool(tool_id: str, description: str) -> ExecutableTool:
    return ExecutableTool(
        id=tool_id,
        name=tool_id,
        description=description,
        input_schema={"type": "object", "properties": {"city": {"type": "string"}}},
        execute=lambda args: {"tool": tool_id, "args": args},
    )


def _core(**kwargs: Any) -> Ratel:
    return ratel(events={"session_id": "s-1", "source_id": "src-1"}, **kwargs)


async def test_register_then_search_and_invoke_through_the_tool_collection() -> None:
    r = _core()

    await r.tools.register(
        [_tool("get_weather", "weather forecast for a city"), _tool("send_email", "send email")]
    )

    assert r.tools.has("get_weather")
    assert r.tools.get("get_weather") is not None
    assert r.tools.get("missing") is None
    assert isinstance(r.tools.catalog, ToolCatalog)
    assert [hit.tool_id for hit in r.tools.search("weather in rome", 1)] == ["get_weather"]
    hits = await r.tools.search_async("send an email", 1)
    assert [hit.tool_id for hit in hits] == ["send_email"]
    assert await r.tools.invoke("get_weather", {"city": "Rome"}) == {
        "tool": "get_weather",
        "args": {"city": "Rome"},
    }


async def test_tool_search_clamps_top_k() -> None:
    r = _core()
    await r.tools.register([_tool(f"weather_{i}", "weather forecast") for i in range(60)])

    assert len(r.tools.search("weather", 1_000)) == 50
    assert len(r.tools.search("weather", 0)) == 5
    assert len(await r.tools.search_async("weather", -3)) == 5


@pytest.mark.parametrize("reserved", [SEARCH_CAPABILITIES_ID, INVOKE_TOOL_ID, GET_SKILL_CONTENT_ID])
def test_register_rejects_a_reserved_capability_tool_id(reserved: str) -> None:
    r = _core()

    with pytest.raises(ValueError, match="reserved"):
        r.tools.register([_tool("ok_tool", "fine"), _tool(reserved, "shadow")])

    assert not r.tools.has("ok_tool")  # the batch validates before anything registers


def test_register_rejects_a_non_executable_tool() -> None:
    r = _core()

    with pytest.raises(TypeError, match="ExecutableTool"):
        r.tools.register([{"id": "get_weather"}])  # type: ignore[list-item]


async def test_model_tools_is_the_fixed_capability_set() -> None:
    r = _core()
    before = r.model_tools()
    await r.skills.register(Skill(id="trip", name="trip", description="plan a trip", body="Go."))
    after = r.model_tools()

    assert list(before) == [SEARCH_CAPABILITIES_ID, INVOKE_TOOL_ID, GET_SKILL_CONTENT_ID]
    assert before[SEARCH_CAPABILITIES_ID].description == after[SEARCH_CAPABILITIES_ID].description
    assert "get_skill_content" in before[SEARCH_CAPABILITIES_ID].description
    assert before[SEARCH_CAPABILITIES_ID] is not after[SEARCH_CAPABILITIES_ID]


async def test_model_tools_search_and_invoke_the_live_catalog() -> None:
    r = _core()
    tools = r.model_tools()
    await r.tools.register(_tool("get_weather", "weather forecast for a city"))

    found = await tools[SEARCH_CAPABILITIES_ID].execute({"query": "weather"})
    result = await tools[INVOKE_TOOL_ID].execute(
        {"toolId": "get_weather", "args": {"city": "Oslo"}}
    )

    assert found["tools"]["groups"][0]["hits"][0]["toolId"] == "get_weather"
    assert result == {"tool": "get_weather", "args": {"city": "Oslo"}}


async def test_recall_returns_the_capability_result_or_none() -> None:
    r = _core(recall_top_k=1)
    assert await r.recall("weather") is None

    await r.tools.register(
        [_tool("get_weather", "weather forecast"), _tool("weather_alerts", "weather alerts")]
    )
    await r.skills.register(
        Skill(id="trip", name="trip", description="plan a trip with weather", body="Go.")
    )
    result = await r.recall("weather")

    assert result is not None
    hits = [hit for group in result["tools"]["groups"] for hit in group["hits"]]
    assert len(hits) == 1
    assert [skill["skillId"] for skill in result["skills"]] == ["trip"]


async def test_recall_records_a_direct_search() -> None:
    r = _core()
    received: list[dict[str, Any]] = []
    subscription = r.events.subscribe(lambda batch: received.extend(batch))
    await r.tools.register(_tool("get_weather", "weather forecast"))

    await r.recall("weather")
    await subscription.flush()
    subscription.unsubscribe()

    searches = [event for event in received if event["type"] == "search"]
    assert searches and all(event["origin"] == "direct" for event in searches)


async def test_tool_and_skill_events_share_one_stream_and_source_id() -> None:
    r = _core()
    received: list[dict[str, Any]] = []
    subscription = r.events.subscribe(lambda batch: received.extend(batch))

    await r.tools.register(_tool("get_weather", "weather forecast"))
    await r.skills.register(Skill(id="trip", name="trip", description="plan a trip", body="Go."))
    await subscription.flush()
    subscription.unsubscribe()

    assert {"index_churn", "skill_churn"} <= {event["type"] for event in received}
    assert {event["source_id"] for event in received} == {"src-1"}
    assert {event["session_id"] for event in received} == {"s-1"}


async def test_catalog_snapshot_holds_the_tools_and_skills_of_the_core() -> None:
    r = _core()
    await r.tools.register(_tool("get_weather", "weather forecast"))
    await r.skills.register(Skill(id="trip", name="trip", description="plan a trip", body="Go."))

    snapshot = r.catalog.snapshot()

    assert snapshot["source_id"] == "src-1"
    assert [tool["id"] for tool in snapshot["tools"]] == ["get_weather"]
    assert [skill["id"] for skill in snapshot["skills"]] == ["trip"]


async def test_turn_scopes_the_turn_id_and_record_tool_call_reports_external_calls() -> None:
    r = _core()
    received: list[dict[str, Any]] = []
    subscription = r.events.subscribe(lambda batch: received.extend(batch))

    assert r.current_turn_id() is None
    async with r.turn(id="turn-1", end_user_id="user-9"):
        assert r.current_turn_id() == "turn-1"
        assert current_turn_id() == "turn-1"
        r.record_tool_call("web_search", took_ms=12)
    assert r.current_turn_id() is None
    await subscription.flush()
    subscription.unsubscribe()

    by_type = {event["type"]: event for event in received}
    assert by_type["turn_start"]["turn_id"] == "turn-1"
    assert by_type["invoke_end"]["origin"] == "external"
    assert by_type["invoke_end"]["turn_id"] == "turn-1"
    assert by_type["invoke_end"]["end_user_id"] == "user-9"


async def test_ground_injects_a_fact_once_then_skips_it() -> None:
    r = _core()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        facts = r.facts
    await facts.register(
        Fact(id="address", name="address", description="shop address", body="1 Main St")
    )

    first = await r.ground("where is the shop address", [])
    second = await r.ground("where is the shop address", ["1 Main St"])
    snapshot = await r.ground_snapshot("where is the shop address")

    assert [(item.id, item.reason) for item in first.inject] == [("address", "never")]
    assert second.inject == [] and second.skipped == ["address"]
    assert [item.id for item in snapshot] == ["address"]
    assert r.facts is facts


def test_facts_is_built_lazily(recwarn: pytest.WarningsRecorder) -> None:
    r = _core()
    r.model_tools()

    assert not [w for w in recwarn if "experimental" in str(w.message).lower()]


def test_ratel_validates_ranking_options_at_construction() -> None:
    with pytest.raises(ValueError):
        ratel(method="unknown")  # type: ignore[arg-type]
