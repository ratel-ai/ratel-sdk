"""protocol/v1 and protocol/v2 conformance: reproduce every shared vector byte-for-byte."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from ratel_ai.cloud import (
    SKILL_FIELDS,
    V2_SKILL_FIELDS,
    canonical_set,
    canonical_set_v2,
    canonical_skill_v2,
    if_none_match_matches,
    resolve,
    resolve_v2,
)

PROTOCOL_DIR = Path(__file__).resolve().parents[5] / "protocol"
V1 = json.loads((PROTOCOL_DIR / "v1" / "conformance" / "vectors.json").read_text("utf-8"))
V2 = json.loads((PROTOCOL_DIR / "v2" / "conformance" / "vectors.json").read_text("utf-8"))
VECTORS = {"v1": V1, "v2": V2}


def etag_for(version: str, name: str) -> tuple[str, list[Any]]:
    vectors = VECTORS[version]
    vector = next(entry for entry in vectors["etag"] if entry["name"] == name)
    catalog = vectors["catalogs"][vector["catalog"]]
    if version == "v1":
        skills: list[Any] = list(resolve(catalog, vector["scope"]))
        canonical = canonical_set(skills)
    else:
        skills = list(resolve_v2(catalog, vector["scope"]))
        canonical = canonical_set_v2(skills)
    return f'"{hashlib.sha256(canonical.encode("utf-8")).hexdigest()}"', skills


def cases(key: str) -> list[Any]:
    return [
        pytest.param(version, entry, id=f"{version}:{_case_id(entry)}")
        for version, vectors in VECTORS.items()
        for entry in vectors.get(key, [])
    ]


def _case_id(entry: Any) -> str:
    return entry["name"] if isinstance(entry, dict) else "|".join(entry)


@pytest.mark.parametrize(("version", "vector"), cases("etag"))
def test_etag_vector(version: str, vector: dict[str, Any]) -> None:
    etag, skills = etag_for(version, vector["name"])
    assert [skill["id"] for skill in skills] == vector["expect"]["resolvedIds"]
    assert etag == vector["expect"]["etag"]


@pytest.mark.parametrize(("version", "group"), cases("equalEtags"))
def test_equal_etags(version: str, group: list[str]) -> None:
    assert len({etag_for(version, name)[0] for name in group}) == 1


@pytest.mark.parametrize(("version", "group"), cases("distinctEtags"))
def test_distinct_etags(version: str, group: list[str]) -> None:
    assert len({etag_for(version, name)[0] for name in group}) == len(group)


@pytest.mark.parametrize(("version", "vector"), cases("inm"))
def test_if_none_match_vector(version: str, vector: dict[str, Any]) -> None:
    current, _ = etag_for(version, vector["current"])
    other = etag_for(version, vector["of"])[0] if "of" in vector else None
    header = _header_for(vector["ifNoneMatch"]["kind"], current, other)
    assert (304 if if_none_match_matches(header, current) else 200) == vector["expect"]


@pytest.mark.parametrize(
    "vector", [pytest.param(entry, id=entry["name"]) for entry in V2["canonical"]]
)
def test_v2_canonical_vector(vector: dict[str, Any]) -> None:
    assert canonical_skill_v2(vector["skill"]) == vector["expect"]


@pytest.mark.parametrize(("version", "fields"), [("v1", SKILL_FIELDS), ("v2", V2_SKILL_FIELDS)])
def test_wire_projection_serves_the_frozen_field_set(version: str, fields: tuple[str, ...]) -> None:
    wire = VECTORS[version]["wire"]
    assert list(fields) == wire["skillFields"]
    for name in fields:
        for forbidden in wire["forbiddenFieldSubstrings"]:
            assert forbidden not in name.lower()


def _header_for(kind: str, current: str, other: str | None) -> str | None:
    if kind == "self":
        return current
    if kind == "weakSelf":
        return f"W/{current}"
    if kind == "star":
        return "*"
    if kind == "listWithSelf":
        return f'"deadbeef", {current}'
    if kind == "listMiss":
        return '"deadbeef", "cafebabe"'
    if kind == "absent":
        return None
    if kind == "other":
        assert other is not None, 'If-None-Match kind "other" requires a vector'
        return other
    raise AssertionError(f"unknown If-None-Match kind: {kind}")
