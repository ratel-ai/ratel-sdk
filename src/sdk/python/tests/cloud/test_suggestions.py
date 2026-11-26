from __future__ import annotations

from typing import Any

import pytest

from ratel_ai.cloud import CloudError, RatelCloudClient
from ratel_ai.cloud.testing import MockCloud


async def test_lists_with_status_and_type_filters_and_rejects_bad_values(
    client: RatelCloudClient, mock: MockCloud
) -> None:
    mock.seed_suggestion({"type": "new_skill", "status": "pending"})
    mock.seed_suggestion({"type": "edit_skill", "status": "rejected"})

    pending = await client.suggestions.list(status="pending")
    assert pending["count"] == 1
    assert pending["suggestions"][0]["type"] == "new_skill"
    assert (await client.suggestions.list(type="edit_skill"))["count"] == 1

    bogus: Any = "bogus"
    with pytest.raises(CloudError) as info:
        await client.suggestions.list(status=bogus)
    assert info.value.code == "invalid"


async def test_sends_limit_and_end_user_as_query_keys(
    client: RatelCloudClient, mock: MockCloud
) -> None:
    for _ in range(3):
        mock.seed_suggestion({"endUserId": "alice"})
    result = await client.suggestions.list(limit=2, end_user_id="alice")
    assert result["count"] == 2
    assert mock.requests[-1].url.endswith("/suggestions?limit=2&endUserId=alice")


async def test_filters_by_end_user(client: RatelCloudClient, mock: MockCloud) -> None:
    mock.seed_suggestion({"endUserId": "alice"})
    mock.seed_suggestion({"endUserId": None})
    alices = await client.suggestions.list(end_user_id="alice")
    assert alices["count"] == 1
    assert alices["suggestions"][0]["endUserId"] == "alice"


async def test_generate_returns_a_job_id_with_a_long_timeout(
    client: RatelCloudClient, mock: MockCloud
) -> None:
    result = await client.suggestions.generate()
    assert result["jobId"].startswith("job_")
    assert result["coalesced"] is False
    assert mock.requests[-1].timeout == 300.0


async def test_get_reads_one_suggestion(client: RatelCloudClient, mock: MockCloud) -> None:
    seeded = mock.seed_suggestion({"rationale": "why"})
    assert (await client.suggestions.get(seeded["id"]))["rationale"] == "why"


async def test_approving_a_new_skill_drafts_it_and_stamps_created_skill_id(
    client: RatelCloudClient, mock: MockCloud
) -> None:
    seeded = mock.seed_suggestion(
        {
            "type": "new_skill",
            "endUserId": "alice",
            "patch": {
                "name": "alice-helper",
                "description": "d",
                "tags": [],
                "body": "b",
                "model": "m",
            },
        }
    )
    approved = await client.suggestions.approve(seeded["id"])
    assert approved["status"] == "approved"
    assert approved["createdSkillId"] is not None

    skill = await client.skills.get(approved["createdSkillId"])
    assert skill["name"] == "alice-helper"
    assert skill["status"] == "draft"
    assert skill["endUserId"] == "alice"


async def test_approving_a_new_skill_dedupes_a_taken_name(
    client: RatelCloudClient, mock: MockCloud
) -> None:
    await client.skills.create({"name": "taken", "description": "d", "body": "b"})
    seeded = mock.seed_suggestion(
        {
            "type": "new_skill",
            "patch": {"name": "taken", "description": "d", "tags": [], "body": "b", "model": "m"},
        }
    )
    approved = await client.suggestions.approve(seeded["id"])
    assert approved["createdSkillId"] is not None
    assert (await client.skills.get(approved["createdSkillId"]))["name"] == "taken-2"


async def test_approving_an_edit_applies_the_patch_through_the_version_guard(
    client: RatelCloudClient, mock: MockCloud
) -> None:
    target = await client.skills.create({"name": "target", "description": "old", "body": "b"})
    seeded = mock.seed_suggestion(
        {
            "type": "edit_skill",
            "targetSkillId": target["id"],
            "targetSkillExpectedVersion": target["version"],
            "patch": {"description": "improved"},
        }
    )
    await client.suggestions.approve(seeded["id"])
    after = await client.skills.get(target["id"])
    assert after["description"] == "improved"
    assert after["version"] == target["version"] + 1


async def test_approving_an_edit_against_a_stale_version_is_a_version_conflict(
    client: RatelCloudClient, mock: MockCloud
) -> None:
    target = await client.skills.create({"name": "target", "description": "old", "body": "b"})
    seeded = mock.seed_suggestion(
        {
            "type": "edit_skill",
            "targetSkillId": target["id"],
            "targetSkillExpectedVersion": target["version"],
            "patch": {"description": "improved"},
        }
    )
    await client.skills.update(target["id"], {"expectedVersion": target["version"], "body": "x"})
    with pytest.raises(CloudError) as info:
        await client.suggestions.approve(seeded["id"])
    assert (info.value.code, info.value.reason) == ("conflict", "version_conflict")


async def test_reject_then_a_second_review_is_not_pending(
    client: RatelCloudClient, mock: MockCloud
) -> None:
    seeded = mock.seed_suggestion()
    rejected = await client.suggestions.reject(seeded["id"])
    assert rejected["status"] == "rejected"
    assert rejected["reviewedAt"] is not None

    with pytest.raises(CloudError) as info:
        await client.suggestions.approve(seeded["id"])
    assert (info.value.code, info.value.reason) == ("conflict", "not_pending")


async def test_an_unknown_suggestion_is_not_found(client: RatelCloudClient) -> None:
    with pytest.raises(CloudError) as info:
        await client.suggestions.approve("sug_missing")
    assert info.value.code == "not_found"
