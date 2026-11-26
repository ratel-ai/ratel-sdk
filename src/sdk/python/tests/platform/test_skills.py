from __future__ import annotations

import json

import pytest

from ratel_ai.platform import PlatformError, RatelPlatformClient
from ratel_ai.platform.testing import MockPlatform


async def test_round_trips_a_searchable_description_through_create_get_update_and_clear(
    client: RatelPlatformClient,
) -> None:
    created = await client.skills.create(
        {
            "name": "deploy-checklist",
            "description": "How to deploy safely.",
            "searchableDescription": "release production rollback canary",
            "body": "# Deploy\n…",
        }
    )
    assert created["searchableDescription"] == "release production rollback canary"

    fetched = await client.skills.get(created["id"])
    assert fetched["searchableDescription"] == "release production rollback canary"

    updated = await client.skills.update(
        created["id"],
        {"expectedVersion": fetched["version"], "searchableDescription": "deploy production"},
    )
    assert updated["searchableDescription"] == "deploy production"

    cleared = await client.skills.update(
        created["id"], {"expectedVersion": updated["version"], "searchableDescription": None}
    )
    assert cleared["searchableDescription"] is None


async def test_creates_a_draft_and_reads_it_back_through_get_and_list(
    client: RatelPlatformClient,
) -> None:
    created = await client.skills.create(
        {"name": "deploy-checklist", "description": "d", "body": "b", "tags": ["ops"]}
    )
    assert created["status"] == "draft"
    assert created["version"] == 1
    assert (await client.skills.get(created["id"]))["name"] == "deploy-checklist"

    listed = await client.skills.list(status="draft")
    assert listed["count"] == 1
    assert listed["skills"][0]["id"] == created["id"]


async def test_sends_the_wire_body_and_query_keys_unchanged(
    client: RatelPlatformClient, mock: MockPlatform
) -> None:
    skill = await client.skills.create(
        {"name": "s", "description": "d", "body": "b", "endUserId": "alice"}
    )
    await client.skills.list(status="draft", end_user_id="alice")
    await client.skills.publish(skill["id"])
    await client.skills.archive(skill["id"], expected_version=2)

    create, listing, publish, archive = mock.requests
    assert create.method == "POST"
    assert create.url == "https://mock.test/api/v1/skills"
    assert create.body == b'{"name":"s","description":"d","body":"b","endUserId":"alice"}'
    assert listing.url == "https://mock.test/api/v1/skills?status=draft&endUserId=alice"
    assert publish.url.endswith(f"/skills/{skill['id']}/publish")
    assert publish.body == b"{}"
    assert archive.body is not None
    assert json.loads(archive.body) == {"expectedVersion": 2}


async def test_rejects_a_name_that_is_not_kebab_case(client: RatelPlatformClient) -> None:
    with pytest.raises(PlatformError) as info:
        await client.skills.create({"name": "Not Kebab", "description": "d", "body": "b"})
    assert info.value.code == "invalid"


async def test_rejects_a_duplicate_active_name(client: RatelPlatformClient) -> None:
    await client.skills.create({"name": "dupe", "description": "d", "body": "b"})
    with pytest.raises(PlatformError) as info:
        await client.skills.create({"name": "dupe", "description": "d2", "body": "b2"})
    assert (info.value.code, info.value.reason) == ("conflict", "name_conflict")


async def test_updates_through_the_version_guard_and_bumps_the_version(
    client: RatelPlatformClient,
) -> None:
    skill = await client.skills.create({"name": "s", "description": "old", "body": "b"})
    with pytest.raises(PlatformError) as info:
        await client.skills.update(skill["id"], {"expectedVersion": 99, "description": "new"})
    assert (info.value.code, info.value.reason) == ("conflict", "version_conflict")

    updated = await client.skills.update(
        skill["id"], {"expectedVersion": skill["version"], "description": "new"}
    )
    assert updated["description"] == "new"
    assert updated["version"] == skill["version"] + 1


async def test_an_unguarded_update_applies_unconditionally(client: RatelPlatformClient) -> None:
    skill = await client.skills.create({"name": "s", "description": "old", "body": "b"})
    await client.skills.update(
        skill["id"], {"expectedVersion": skill["version"], "description": "theirs"}
    )
    updated = await client.skills.update(skill["id"], {"description": "mine"})
    assert updated["description"] == "mine"
    assert updated["version"] == skill["version"] + 2


async def test_publish_and_archive_move_the_skill_through_its_states(
    client: RatelPlatformClient,
) -> None:
    skill = await client.skills.create({"name": "pub-me", "description": "d", "body": "b"})
    published = await client.skills.publish(skill["id"], expected_version=skill["version"])
    assert published["status"] == "published"
    assert published["publishedAt"] is not None
    listed = await client.skills.list(status="published")
    assert "pub-me" in [entry["name"] for entry in listed["skills"]]

    archived = await client.skills.archive(skill["id"])
    assert archived["status"] == "archived"
    listed = await client.skills.list(status="published")
    assert "pub-me" not in [entry["name"] for entry in listed["skills"]]


async def test_a_stale_guard_on_publish_is_a_version_conflict(client: RatelPlatformClient) -> None:
    skill = await client.skills.create({"name": "s", "description": "d", "body": "b"})
    with pytest.raises(PlatformError) as info:
        await client.skills.publish(skill["id"], expected_version=99)
    assert (info.value.code, info.value.reason) == ("conflict", "version_conflict")


async def test_scopes_a_skill_to_an_end_user_and_filters_the_list(
    client: RatelPlatformClient,
) -> None:
    await client.skills.create(
        {"name": "for-alice", "description": "d", "body": "b", "endUserId": "alice"}
    )
    await client.skills.create({"name": "global", "description": "d", "body": "b"})
    alices = await client.skills.list(end_user_id="alice")
    assert [entry["name"] for entry in alices["skills"]] == ["for-alice"]


async def test_import_reports_created_updated_and_unchanged_by_name(
    client: RatelPlatformClient,
) -> None:
    await client.skills.create({"name": "keep", "description": "same", "body": "same"})
    await client.skills.create({"name": "change", "description": "old", "body": "old"})
    report = await client.skills.import_(
        [
            {"name": "keep", "description": "same", "body": "same"},
            {"name": "change", "description": "new", "body": "new"},
            {"name": "fresh", "description": "d", "body": "b"},
        ]
    )
    assert report == {"created": ["fresh"], "updated": ["change"], "unchanged": ["keep"]}


async def test_import_reports_an_override_only_edit_as_updated(client: RatelPlatformClient) -> None:
    existing = await client.skills.create(
        {"name": "deploy-checklist", "description": "How to deploy.", "body": "# Deploy"}
    )
    report = await client.skills.import_(
        [
            {
                "name": existing["name"],
                "description": existing["description"],
                "searchableDescription": "release production rollback canary",
                "body": existing["body"],
            }
        ]
    )
    assert report == {"created": [], "updated": [existing["name"]], "unchanged": []}
    updated = await client.skills.get(existing["id"])
    assert updated["searchableDescription"] == "release production rollback canary"
    assert updated["version"] == existing["version"] + 1


async def test_get_of_an_unknown_id_is_not_found(client: RatelPlatformClient) -> None:
    with pytest.raises(PlatformError) as info:
        await client.skills.get("sk_missing")
    assert (info.value.code, info.value.status) == ("not_found", 404)


async def test_a_wrong_key_is_unauthorized(mock: MockPlatform) -> None:
    client = RatelPlatformClient(
        api_key="wrong", base_url="https://mock.test/api/v1", transport=mock
    )
    with pytest.raises(PlatformError) as info:
        await client.skills.list()
    assert (info.value.code, info.value.status) == ("unauthorized", 401)
    assert str(info.value) == "Invalid or revoked API key."


async def test_a_request_without_any_key_names_the_env_variable(
    mock: MockPlatform, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RATEL_API_KEY", raising=False)
    client = RatelPlatformClient(base_url="https://mock.test/api/v1", transport=mock)
    with pytest.raises(PlatformError) as info:
        await client.skills.list()
    assert "RATEL_API_KEY" in str(info.value)
    assert info.value.code == "config"
    assert mock.requests == []


async def test_the_client_reads_the_key_from_the_env(
    mock: MockPlatform, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RATEL_API_KEY", mock.api_key)
    client = RatelPlatformClient(base_url="https://mock.test/api/v1", transport=mock)
    assert (await client.skills.list())["count"] == 0
    assert mock.requests[0].headers["authorization"] == f"Bearer {mock.api_key}"
