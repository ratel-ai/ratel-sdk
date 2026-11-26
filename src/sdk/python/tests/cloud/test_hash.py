"""Catalog snapshot hashes: reproduce the shared vectors that the TypeScript SDK also reads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ratel_ai.cloud.runtime import RuntimeCatalogSnapshot, hash_catalog_snapshot

PROTOCOL_DIR = Path(__file__).resolve().parents[5] / "protocol"
VECTORS = json.loads(
    (PROTOCOL_DIR / "v1" / "conformance" / "snapshot-hash-vectors.json").read_text("utf-8")
)
BY_NAME = {vector["name"]: vector for vector in VECTORS["vectors"]}

# The wire names of the vector file, mapped to the Python snapshot input.
_WIRE_TO_PYTHON = {"searchable_description": "experimental_searchable_description"}


def _snapshot(name: str) -> RuntimeCatalogSnapshot:
    wire = BY_NAME[name]["snapshot"]
    tools: list[Any] = [
        {_WIRE_TO_PYTHON.get(key, key): value for key, value in tool.items()}
        for tool in wire["tools"]
    ]
    return {"source_id": wire["source_id"], "tools": tools}


@pytest.mark.parametrize("name", sorted(BY_NAME))
def test_reproduces_the_shared_typescript_hash(name: str) -> None:
    assert hash_catalog_snapshot(_snapshot(name)) == BY_NAME[name]["hash"]


@pytest.mark.parametrize("group", VECTORS["equalHashes"])
def test_equal_groups_share_one_hash(group: list[str]) -> None:
    assert len({hash_catalog_snapshot(_snapshot(name)) for name in group}) == 1


@pytest.mark.parametrize("group", VECTORS["distinctHashes"])
def test_distinct_groups_have_distinct_hashes(group: list[str]) -> None:
    assert len({hash_catalog_snapshot(_snapshot(name)) for name in group}) == len(group)


def test_python_values_hash_like_their_json_form() -> None:
    snapshot: RuntimeCatalogSnapshot = {
        "source_id": "worker-exotic",
        "tools": [
            {
                "id": "exotic",
                "name": "exotic",
                "input_schema": {
                    "createdAt": "2026-08-14T00:00:00.000Z",
                    "nan": float("nan"),
                    "infinity": float("inf"),
                    "pattern": {},
                },
            }
        ],
    }

    assert hash_catalog_snapshot(snapshot) == BY_NAME["json-coerced-values"]["hash"]
