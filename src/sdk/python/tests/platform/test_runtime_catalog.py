from __future__ import annotations

import json

import pytest

from ratel_ai.platform import (
    PlatformError,
    RatelPlatformClient,
    RuntimeCatalogOverridesFresh,
    RuntimeCatalogOverridesNotModified,
    TransportRequest,
    TransportResponse,
)
from ratel_ai.platform.testing import MockPlatform

ETAG = '"runtime-catalog-overrides-v1"'
EMPTY_ETAG = '"a2c526aab2f489db93635255e88cdd8e07c21b650e8f4e7a323ab9ed4b9c367f"'


class Fixed:
    def __init__(self, response: TransportResponse) -> None:
        self.response = response
        self.requests: list[TransportRequest] = []

    async def send(self, request: TransportRequest) -> TransportResponse:
        self.requests.append(request)
        return self.response


def fixed_client(transport: Fixed) -> RatelPlatformClient:
    return RatelPlatformClient(api_key="rtl_test_key", transport=transport, env={})


async def test_a_fresh_response_carries_its_etag() -> None:
    transport = Fixed(TransportResponse(200, {"ETag": ETAG}, b'{"overrides":[]}'))
    result = await fixed_client(transport).runtime_catalog.list_overrides()
    assert result == RuntimeCatalogOverridesFresh(etag=ETAG, overrides=[])
    assert "if-none-match" not in transport.requests[0].headers


async def test_a_304_sends_if_none_match_and_returns_not_modified() -> None:
    transport = Fixed(TransportResponse(304, {"ETag": ETAG}, b""))
    result = await fixed_client(transport).runtime_catalog.list_overrides(if_none_match=ETAG)
    assert result == RuntimeCatalogOverridesNotModified(etag=ETAG)
    assert result.not_modified is True
    assert transport.requests[0].headers["if-none-match"] == ETAG
    assert transport.requests[0].url == "https://app.ratel.sh/api/v1/runtime-catalog/overrides"


async def test_lists_no_overrides_when_none_are_set(client: RatelPlatformClient) -> None:
    result = await client.runtime_catalog.list_overrides()
    assert result == RuntimeCatalogOverridesFresh(etag=EMPTY_ETAG, overrides=[])


async def test_lists_seeded_overrides_sorted_by_kind_then_entry_id(
    client: RatelPlatformClient, mock: MockPlatform
) -> None:
    mock.seed_runtime_catalog_override(
        {"kind": "tool", "entryId": "zebra", "searchableDescription": "Deploy safely."}
    )
    mock.seed_runtime_catalog_override(
        {"kind": "fact", "entryId": "account-tier", "searchableDescription": "Customer plan."}
    )
    mock.seed_runtime_catalog_override(
        {"kind": "tool", "entryId": "alpha", "searchableDescription": "Find records."}
    )
    result = await client.runtime_catalog.list_overrides()
    assert isinstance(result, RuntimeCatalogOverridesFresh)
    assert result.overrides == [
        {"kind": "fact", "entryId": "account-tier", "searchableDescription": "Customer plan."},
        {"kind": "tool", "entryId": "alpha", "searchableDescription": "Find records."},
        {"kind": "tool", "entryId": "zebra", "searchableDescription": "Deploy safely."},
    ]


async def test_clears_seeded_overrides(client: RatelPlatformClient, mock: MockPlatform) -> None:
    mock.seed_runtime_catalog_override(
        {"kind": "skill", "entryId": "incident", "searchableDescription": "Handle incidents."}
    )
    mock.clear_runtime_catalog_overrides()
    result = await client.runtime_catalog.list_overrides()
    assert result == RuntimeCatalogOverridesFresh(etag=EMPTY_ETAG, overrides=[])


async def test_the_etag_moves_when_an_override_is_set_and_cleared(
    client: RatelPlatformClient, mock: MockPlatform
) -> None:
    empty = await client.runtime_catalog.list_overrides()
    assert empty.etag is not None

    mock.seed_runtime_catalog_override(
        {"kind": "tool", "entryId": "weather", "searchableDescription": "Forecasts."}
    )
    populated = await client.runtime_catalog.list_overrides(if_none_match=empty.etag)
    assert isinstance(populated, RuntimeCatalogOverridesFresh)
    assert populated.etag is not None and populated.etag != empty.etag
    assert populated.overrides == [
        {"kind": "tool", "entryId": "weather", "searchableDescription": "Forecasts."}
    ]

    unchanged = await client.runtime_catalog.list_overrides(if_none_match=populated.etag)
    assert unchanged == RuntimeCatalogOverridesNotModified(etag=populated.etag)

    mock.clear_runtime_catalog_overrides()
    cleared = await client.runtime_catalog.list_overrides(if_none_match=populated.etag)
    assert cleared == RuntimeCatalogOverridesFresh(etag=empty.etag, overrides=[])


async def test_an_invalid_key_is_unauthorized(mock: MockPlatform) -> None:
    client = RatelPlatformClient(
        api_key="wrong", base_url="https://mock.test/api/v1", transport=mock
    )
    with pytest.raises(PlatformError) as info:
        await client.runtime_catalog.list_overrides()
    assert (info.value.code, info.value.status) == ("unauthorized", 401)


async def test_mock_serves_a_strong_etag_and_an_empty_304(mock: MockPlatform) -> None:
    def request(**headers: str) -> TransportRequest:
        return TransportRequest(
            method="GET",
            url="https://mock.test/v1/runtime-catalog/overrides",
            headers={"authorization": f"Bearer {mock.api_key}", **headers},
            body=None,
            timeout=1.0,
        )

    fresh = await mock.send(request())
    assert fresh.status == 200
    assert fresh.headers["etag"] == EMPTY_ETAG
    assert json.loads(fresh.body) == {"overrides": []}

    unchanged = await mock.send(request(**{"if-none-match": EMPTY_ETAG}))
    assert unchanged.status == 304
    assert unchanged.headers["etag"] == EMPTY_ETAG
    assert unchanged.body == b""
    assert len(mock.requests) == 2
