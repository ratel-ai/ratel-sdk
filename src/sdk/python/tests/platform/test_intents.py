from __future__ import annotations

import pytest

from ratel_ai.platform import ConversationMessage, PlatformError, RatelPlatformClient
from ratel_ai.platform.testing import MockPlatform

CATALOG = {
    "global": [
        {
            "id": "g1",
            "name": "deploy-app",
            "description": "d",
            "tags": [],
            "tools": [],
            "metadata": {},
            "body": "b",
        }
    ]
}


@pytest.fixture
def mock() -> MockPlatform:
    return MockPlatform(catalog=CATALOG)  # type: ignore[arg-type]


def user(content: str) -> ConversationMessage:
    return {"role": "user", "content": content}


async def test_analyze_extracts_intents_with_coverage_and_drafts_nothing(
    client: RatelPlatformClient, mock: MockPlatform
) -> None:
    result = await client.intents.analyze(
        {
            "messages": [
                user("how do I deploy the app to prod?"),
                {"role": "assistant", "content": "…"},
                user("rotate the database credentials"),
            ]
        }
    )
    assert result["runId"].startswith("run_")
    assert result["cached"] is False
    assert result["catalogVersion"] is not None
    assert len(result["intents"]) == 2
    assert "suggestionIds" not in result
    assert (await client.suggestions.list(status="pending"))["count"] == 0

    covered = next(intent for intent in result["intents"] if "deploy" in intent["text"])
    assert covered["covered"] is True
    assert covered["matchedSkillId"] == "g1"
    gap = next(intent for intent in result["intents"] if "rotate" in intent["text"])
    assert gap["covered"] is False
    assert gap["matchedSkillId"] is None
    assert mock.requests[0].timeout == 120.0


async def test_analyzing_an_unchanged_conversation_again_is_a_cache_hit(
    client: RatelPlatformClient,
) -> None:
    first = await client.intents.analyze({"messages": [user("rotate credentials")]})
    second = await client.intents.analyze({"messages": [user("rotate credentials")]})
    assert second["cached"] is True
    assert second["runId"] == first["runId"]
    assert second["intents"][0]["id"] == first["intents"][0]["id"]


async def test_a_catalog_change_busts_the_cache(client: RatelPlatformClient) -> None:
    first = await client.intents.analyze({"messages": [user("rotate credentials")]})
    skill = await client.skills.create(
        {"name": "rotate-credentials", "description": "d", "body": "b"}
    )
    await client.skills.publish(skill["id"], expected_version=skill["version"])

    fresh = await client.intents.analyze({"messages": [user("rotate credentials")]})
    assert fresh["cached"] is False
    assert fresh["catalogVersion"] != first["catalogVersion"]
    assert fresh["intents"][0]["covered"] is True


async def test_no_cache_forces_a_live_run_and_replaces_the_stored_one(
    client: RatelPlatformClient,
) -> None:
    first = await client.intents.analyze({"messages": [user("rotate credentials")]})
    forced = await client.intents.analyze(
        {"messages": [user("rotate credentials")], "noCache": True}
    )
    assert forced["cached"] is False
    assert forced["runId"] != first["runId"]

    third = await client.intents.analyze({"messages": [user("rotate credentials")]})
    assert third["cached"] is True
    assert third["runId"] == forced["runId"]


async def test_list_counts_occurrences_most_frequent_first(
    client: RatelPlatformClient, mock: MockPlatform
) -> None:
    await client.intents.analyze({"messages": [user("reset my password")]})
    await client.intents.analyze({"messages": [user("reset my password"), user("cancel my plan")]})
    ledger = await client.intents.list()
    assert ledger["total"] == 2
    assert ledger["intents"][0]["text"] == "reset my password"
    assert ledger["intents"][0]["occurrences"] == 2
    assert ledger["intents"][1]["occurrences"] == 1

    await client.intents.list(page=1)
    assert mock.requests[-1].url.endswith("/intents?page=1")
    assert (await client.intents.list(page=1))["intents"] == []


async def test_suggest_drafts_a_pending_new_skill_scoped_to_the_end_user(
    client: RatelPlatformClient,
) -> None:
    run = await client.intents.analyze(
        {"messages": [user("rotate the database credentials")], "endUserId": "alice"}
    )
    gap = next(intent for intent in run["intents"] if not intent["covered"])

    suggested = await client.intents.suggest(gap["id"])
    job = await client.jobs.wait_for(suggested["jobId"], interval=0.001)
    assert job["kind"] == "suggest_skill"
    assert job["status"] == "done"
    suggestion_id = job["result"]["suggestionId"]
    assert suggestion_id

    suggestion = await client.suggestions.get(suggestion_id)
    assert suggestion["type"] == "new_skill"
    assert suggestion["signalKind"] == "coverage_gap"
    assert suggestion["status"] == "pending"
    assert suggestion["endUserId"] == "alice"
    assert suggestion["sourceQueryIntentId"] == gap["id"]


async def test_a_second_suggest_for_the_same_intent_reports_exists(
    client: RatelPlatformClient,
) -> None:
    run = await client.intents.analyze({"messages": [user("rotate the database credentials")]})
    intent_id = run["intents"][0]["id"]

    first = await client.jobs.wait_for((await client.intents.suggest(intent_id))["jobId"])
    assert first["result"]["suggestionId"]
    second = await client.jobs.wait_for((await client.intents.suggest(intent_id))["jobId"])
    assert second["result"] == {"suggestionId": None, "reason": "exists"}


async def test_suggest_on_an_unknown_intent_is_not_found(client: RatelPlatformClient) -> None:
    with pytest.raises(PlatformError) as info:
        await client.intents.suggest("qi_nope")
    assert info.value.code == "not_found"


async def test_closes_the_loop_suggest_approve_publish_then_covered(
    client: RatelPlatformClient,
) -> None:
    messages = [user("rotate credentials now")]
    gap_run = await client.intents.analyze({"messages": messages, "endUserId": "alice"})
    suggested = await client.intents.suggest(gap_run["intents"][0]["id"])
    job = await client.jobs.wait_for(suggested["jobId"])

    approved = await client.suggestions.approve(job["result"]["suggestionId"])
    assert approved["createdSkillId"] is not None
    draft = await client.skills.get(approved["createdSkillId"])
    await client.skills.publish(draft["id"], expected_version=draft["version"])

    rerun = await client.intents.analyze(
        {"messages": [*messages, user("rotate credentials now please")], "endUserId": "alice"}
    )
    intent = next(i for i in rerun["intents"] if i["text"] == "rotate credentials now please")
    assert intent["covered"] is True
    assert intent["matchedSkillId"] == draft["id"]
