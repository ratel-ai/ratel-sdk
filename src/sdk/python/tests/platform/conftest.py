"""Fixtures for the Ratel Platform client tests: an in-process MockPlatform and a client on it."""

from __future__ import annotations

import pytest

from ratel_ai.platform import RatelPlatformClient
from ratel_ai.platform.testing import MockPlatform

MOCK_BASE_URL = "https://mock.test/api/v1"


@pytest.fixture
def mock() -> MockPlatform:
    return MockPlatform()


@pytest.fixture
def client(mock: MockPlatform) -> RatelPlatformClient:
    return RatelPlatformClient(api_key=mock.api_key, base_url=MOCK_BASE_URL, transport=mock)
