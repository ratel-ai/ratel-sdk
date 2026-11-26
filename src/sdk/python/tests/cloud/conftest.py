"""Fixtures for the Ratel Cloud client tests: an in-process MockCloud and a client on it."""

from __future__ import annotations

import pytest

from ratel_ai.cloud import RatelCloudClient
from ratel_ai.cloud.testing import MockCloud

MOCK_BASE_URL = "https://mock.test/api/v1"


@pytest.fixture
def mock() -> MockCloud:
    return MockCloud()


@pytest.fixture
def client(mock: MockCloud) -> RatelCloudClient:
    return RatelCloudClient(api_key=mock.api_key, base_url=MOCK_BASE_URL, transport=mock)
