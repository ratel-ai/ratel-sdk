"""ratel_cloud().tool_picker: the Cloud Tool Picker as a `RankFn` (port of tool-picker.test.ts)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from ratel_ai import ExecutableTool, RankCandidate, RetrieverError, ToolCatalog
from ratel_ai.cloud.runtime import ratel_cloud
from ratel_ai.cloud.testing import MockCloud, json_response
from ratel_ai.cloud.transport import TransportResponse

MOCK_BASE_URL = "https://mock.test/api/v1"

TOOLS = [
    RankCandidate(id="deploy_rollback", kind="tool", text="deploy_rollback Roll back a deploy"),
    RankCandidate(id="read_file", kind="tool", text="read_file Read a file"),
]


def _picker(mock: MockCloud, **options: Any) -> Any:
    return ratel_cloud(
        api_key=mock.api_key, base_url=MOCK_BASE_URL, transport=mock, **options
    ).tool_picker


def _pick_body(mock: MockCloud) -> dict[str, Any]:
    body = mock.requests[-1].body
    assert body is not None
    parsed: dict[str, Any] = json.loads(body)
    return parsed


async def test_ranks_a_real_custom_tool_catalog_with_the_ids_mockcloud_picked() -> None:
    mock = MockCloud()
    mock.seed_tool_ranking([("read_file", 0.9), ("deploy_rollback", 0.4)])
    catalog = ToolCatalog(method="custom", retrieve_fn=_picker(mock))
    await catalog.register(
        [
            ExecutableTool(
                id="deploy_rollback",
                name="deploy_rollback",
                description="Roll back the last deploy",
                execute=lambda _args: "rolled back",
            ),
            ExecutableTool(
                id="read_file",
                name="read_file",
                description="Read a file from disk",
                execute=lambda _args: "read",
            ),
        ]
    )

    hits = await catalog.search_async("open the config file", 5)

    assert [hit.tool_id for hit in hits] == ["read_file", "deploy_rollback"]
    request = mock.requests[-1]
    assert request.method == "POST"
    assert request.url == f"{MOCK_BASE_URL}/tools/pick"
    assert request.headers["authorization"] == f"Bearer {mock.api_key}"
    assert _pick_body(mock) == {"query": "open the config file", "mode": "precise", "top_k": 5}


@pytest.mark.parametrize(
    ("status", "code", "transient"),
    [
        (400, "InvalidRequest", False),
        (401, "Unauthorized", False),
        (402, "InsufficientCredits", False),
        (403, "Unauthorized", False),
        (409, "NoSyncedTools", False),
        (413, "InvalidRequest", False),
        (429, "RateLimited", True),
        (500, "Http", True),
        (502, "Unavailable", True),
        (503, "Unavailable", True),
        (504, "Timeout", True),
        (418, "Http", False),
    ],
)
async def test_maps_each_http_status_to_the_typescript_retriever_error(
    status: int, code: str, transient: bool
) -> None:
    mock = MockCloud()
    mock.script_responses("POST", "/tools/pick", status)

    with pytest.raises(RetrieverError) as raised:
        await _picker(mock)("roll back", TOOLS, 5)

    assert raised.value.code == code
    assert raised.value.transient is transient
    assert raised.value.status == status
    assert f"HTTP {status}: scripted HTTP {status}" in str(raised.value)


async def test_tells_the_caller_to_sync_the_catalog_on_409() -> None:
    mock = MockCloud()
    mock.script_responses("POST", "/tools/pick", 409)

    with pytest.raises(RetrieverError, match=r"no synced tools.*attach\(runtime\)"):
        await _picker(mock)("roll back", TOOLS, 5)


async def test_carries_retry_after_seconds_on_429() -> None:
    mock = MockCloud()
    mock.script_responses(
        "POST",
        "/tools/pick",
        json_response(
            {"error": {"message": "slow down"}}, status=429, headers={"retry-after": "7"}
        ),
    )

    with pytest.raises(RetrieverError) as raised:
        await _picker(mock)("roll back", TOOLS, 5)

    assert raised.value.retry_after_secs == 7
    assert "HTTP 429: slow down" in str(raised.value)


async def test_leaves_retry_after_secs_unset_without_a_numeric_retry_after() -> None:
    mock = MockCloud()
    mock.script_responses(
        "POST",
        "/tools/pick",
        json_response({}, status=429, headers={"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}),
    )

    with pytest.raises(RetrieverError) as raised:
        await _picker(mock)("roll back", TOOLS, 5)

    assert raised.value.retry_after_secs is None


async def test_falls_back_to_the_raw_text_when_the_error_body_is_not_json() -> None:
    mock = MockCloud()
    mock.script_responses(
        "POST", "/tools/pick", TransportResponse(status=500, headers={}, body=b"<html>oops</html>")
    )

    with pytest.raises(RetrieverError, match=r"HTTP 500: <html>oops</html>"):
        await _picker(mock)("roll back", TOOLS, 5)


async def test_maps_a_network_failure_to_unreachable() -> None:
    mock = MockCloud()
    mock.script_responses("POST", "/tools/pick", ConnectionResetError("reset by peer"))

    with pytest.raises(RetrieverError, match="unreachable: reset by peer") as raised:
        await _picker(mock)("roll back", TOOLS, 5)

    assert (raised.value.code, raised.value.transient) == ("Unreachable", True)


async def test_maps_a_client_timeout_to_timeout() -> None:
    mock = MockCloud()
    mock.script_responses("POST", "/tools/pick", TimeoutError("timed out"))

    with pytest.raises(RetrieverError, match="did not answer within 15 s") as raised:
        await _picker(mock)("roll back", TOOLS, 5)

    assert (raised.value.code, raised.value.transient) == ("Timeout", True)


@pytest.mark.parametrize(
    "response",
    [
        TransportResponse(status=200, headers={}, body=b"not json"),
        TransportResponse(status=200, headers={}, body=b'{"tools":[{"id":"a"}]}'),
        TransportResponse(status=200, headers={}, body=b'{"ranked":[]}'),
    ],
)
async def test_maps_an_unexpected_success_body_to_malformed(response: TransportResponse) -> None:
    mock = MockCloud()
    mock.script_responses("POST", "/tools/pick", response)

    with pytest.raises(RetrieverError) as raised:
        await _picker(mock)("roll back", TOOLS, 5)

    assert (raised.value.code, raised.value.transient, raised.value.status) == (
        "Malformed",
        True,
        200,
    )


async def test_fails_with_config_before_any_request_when_no_key_is_set() -> None:
    mock = MockCloud()
    picker = ratel_cloud(base_url=MOCK_BASE_URL, transport=mock, env={}).tool_picker

    with pytest.raises(RetrieverError, match="needs an API key") as raised:
        await picker("roll back", TOOLS, 5)

    assert (raised.value.code, raised.value.transient) == ("Config", False)
    assert mock.requests == []


async def test_caps_top_k_at_the_pickers_maximum_of_20() -> None:
    mock = MockCloud()
    await _picker(mock)("roll back", TOOLS, 50)

    assert _pick_body(mock)["top_k"] == 20


async def test_truncates_the_query_to_clouds_2000_character_limit() -> None:
    mock = MockCloud()
    await _picker(mock)("x" * 2_500, TOOLS, 5)

    assert _pick_body(mock)["query"] == "x" * 2_000


async def test_does_not_split_a_surrogate_pair_when_truncating() -> None:
    # Cloud counts UTF-16 units: 1999 of "x", then one emoji of 2 units.
    mock = MockCloud()
    await _picker(mock)("x" * 1_999 + "\U0001f600" + "tail", TOOLS, 5)

    assert _pick_body(mock)["query"] == "x" * 1_999


async def test_with_mode_returns_a_new_picker_and_leaves_the_original_alone() -> None:
    mock = MockCloud()
    picker = _picker(mock)
    exhaustive = picker.with_mode("exhaustive")

    await exhaustive("roll back", TOOLS, 5)
    await picker("roll back", TOOLS, 5)

    assert (picker.mode, exhaustive.mode) == ("precise", "exhaustive")
    assert [pick.mode for pick in mock.tool_picks] == ["exhaustive", "precise"]
    assert [request.timeout for request in mock.requests] == [60.0, 15.0]


async def test_an_explicit_timeout_overrides_both_mode_budgets() -> None:
    mock = MockCloud()
    picker = _picker(mock, timeout=3.0)

    await picker.with_mode("exhaustive")("roll back", TOOLS, 5)

    assert mock.requests[-1].timeout == 3.0


async def test_returns_nothing_for_skill_candidates_or_an_empty_request_without_calling_cloud() -> (
    None
):
    mock = MockCloud()
    picker = _picker(mock)
    skill = RankCandidate(id="triage", kind="skill", text="triage")

    assert await picker("roll back", [skill], 5) == []
    assert await picker("roll back", [], 5) == []
    assert await picker("roll back", TOOLS, 0) == []
    assert mock.requests == []


async def test_reads_ratel_base_url_and_ratel_api_key_on_each_pick() -> None:
    mock = MockCloud()
    env = {"RATEL_API_KEY": mock.api_key, "RATEL_BASE_URL": "https://first.test/api/v1/"}
    picker = ratel_cloud(transport=mock, env=env).tool_picker

    await picker("roll back", TOOLS, 5)
    env["RATEL_BASE_URL"] = "https://second.test/api/v1"
    await picker("roll back", TOOLS, 5)

    assert [request.url for request in mock.requests] == [
        "https://first.test/api/v1/tools/pick",
        "https://second.test/api/v1/tools/pick",
    ]


async def test_ranks_the_latest_catalog_snapshot_when_no_ranking_is_seeded() -> None:
    mock = MockCloud()
    mock.catalog_snapshots.append(
        {
            "source_id": "svc",
            "tools": [
                {"id": "read_file", "name": "read_file", "description": "Read a file"},
                {"id": "deploy_rollback", "name": "deploy_rollback", "description": "Roll back"},
            ],
        }
    )

    assert await _picker(mock)("roll back", TOOLS, 5) == [{"id": "deploy_rollback", "score": 1.0}]


async def test_ratel_cloud_also_carries_the_management_clients_of_the_same_configuration() -> None:
    mock = MockCloud()
    cloud = ratel_cloud(api_key=mock.api_key, base_url=MOCK_BASE_URL, transport=mock)

    await cloud.skills.list()

    assert mock.requests[-1].url == f"{MOCK_BASE_URL}/skills"
    for name in ("suggestions", "intents", "jobs", "runtime_catalog"):
        assert hasattr(cloud, name)
