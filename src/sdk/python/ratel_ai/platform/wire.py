"""Frozen protocol/v1 and protocol/v2 canonicalization, scope overlay and ``If-None-Match``.

These are dependency-free ports of the protocol reference verifiers. They are
pure string functions: the SHA-256 that turns canonical bytes into an ETag is
not here. The shared vectors under ``protocol/v1/conformance`` and
``protocol/v2/conformance`` pin both versions.

Anything that changes a version's bytes (field set, order, canonicalization, or
the resolve/overlay rule) requires a new protocol version.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, TypeVar

from .types import Catalog, CatalogV2, WireSkill, WireSkillV2

__all__ = [
    "canonical_set",
    "canonical_set_v2",
    "canonical_skill",
    "canonical_skill_v2",
    "if_none_match_matches",
    "resolve",
    "resolve_v2",
]

_SkillT = TypeVar("_SkillT", bound=Mapping[str, Any])


def canonical_skill(skill: WireSkill) -> str:
    """Canonical protocol/v1 JSON for one projected skill.

    Fixed field order, metadata keys sorted by UTF-8 bytes, arrays in authored
    order, minimal JSON escaping, raw UTF-8, no insignificant whitespace.

    Args:
        skill: The skill to canonicalize.

    Returns:
        The canonical JSON text.
    """
    return (
        f'{{"id":{_s(skill["id"])},"name":{_s(skill["name"])},'
        f'"description":{_s(skill["description"])}'
        f',"tags":{_s(skill["tags"])},"tools":{_s(skill["tools"])},'
        f'"metadata":{_metadata(skill.get("metadata"))},"body":{_s(skill["body"])}}}'
    )


def canonical_skill_v2(skill: WireSkillV2) -> str:
    """Canonical protocol/v2 JSON for one projected skill.

    An unset override has the single representation ``null``.

    Args:
        skill: The skill to canonicalize.

    Returns:
        The canonical JSON text.
    """
    return (
        f'{{"id":{_s(skill["id"])},"name":{_s(skill["name"])},'
        f'"description":{_s(skill["description"])}'
        f',"searchableDescription":{_s(skill.get("searchableDescription"))}'
        f',"tags":{_s(skill["tags"])},"tools":{_s(skill["tools"])},'
        f'"metadata":{_metadata(skill.get("metadata"))},"body":{_s(skill["body"])}}}'
    )


def canonical_set(skills: Iterable[WireSkill]) -> str:
    """Canonical protocol/v1 bytes for a resolved set: sorted by id, compact JSON array.

    Args:
        skills: The resolved skills.

    Returns:
        The canonical JSON text.
    """
    return "[" + ",".join(canonical_skill(skill) for skill in _sort_by_id(skills)) + "]"


def canonical_set_v2(skills: Iterable[WireSkillV2]) -> str:
    """Canonical protocol/v2 bytes for a resolved set.

    Args:
        skills: The resolved skills.

    Returns:
        The canonical JSON text.
    """
    return "[" + ",".join(canonical_skill_v2(skill) for skill in _sort_by_id(skills)) + "]"


def resolve(catalog: Catalog, scope: str | None) -> list[WireSkill]:
    """Resolve the published protocol/v1 set for a scope.

    No scope gives the global layer. A named subject gives its layer overlaid on
    the global layer, the subject winning on a ``name`` collision. An unknown
    subject gives the global layer. The result is sorted by id.

    Args:
        catalog: The source-side catalog.
        scope: The subject, or `None` for the global layer.

    Returns:
        The resolved skills, sorted by id.
    """
    return _resolve(catalog.get("global") or [], catalog.get("subjects"), scope)


def resolve_v2(catalog: CatalogV2, scope: str | None) -> list[WireSkillV2]:
    """Resolve a protocol/v2 source catalog with the unchanged scope-overlay rule.

    Args:
        catalog: The source-side catalog.
        scope: The subject, or `None` for the global layer.

    Returns:
        The resolved skills, sorted by id.
    """
    return _resolve(catalog.get("global") or [], catalog.get("subjects"), scope)


def if_none_match_matches(header_value: str | None, current_etag: str) -> bool:
    """Match an ``If-None-Match`` header with weak comparison (RFC 7232 §3.2).

    Args:
        header_value: The header value, or `None` when absent.
        current_etag: The ETag of the current representation.

    Returns:
        `True` for a cache hit (304). ``*`` matches any current representation.
    """
    if header_value is None:
        return False
    value = header_value.strip()
    if value == "*":
        return True
    current = _opaque(current_etag)
    return any((token := _opaque(raw)) != "" and token == current for raw in value.split(","))


def _s(value: Any) -> str:
    """``JSON.stringify`` for strings, `None` and arrays of strings."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _metadata(metadata: Mapping[str, Sequence[str]] | None) -> str:
    entries = metadata or {}
    keys = sorted(entries, key=_utf8)
    return "{" + ",".join(f"{_s(key)}:{_s(list(entries[key]))}" for key in keys) + "}"


def _sort_by_id(skills: Iterable[_SkillT]) -> list[_SkillT]:
    return sorted(skills, key=lambda skill: _utf8(skill["id"]))


def _utf8(text: str) -> bytes:
    return text.encode("utf-8")


def _resolve(
    global_layer: Sequence[_SkillT],
    subjects: Mapping[str, Sequence[_SkillT]] | None,
    scope: str | None,
) -> list[_SkillT]:
    if scope is None:
        return _sort_by_id(global_layer)
    layer = (subjects or {}).get(scope) or []
    by_name: dict[str, _SkillT] = {}
    for skill in global_layer:
        by_name[skill["name"]] = skill
    for skill in layer:
        by_name[skill["name"]] = skill
    return _sort_by_id(by_name.values())


def _opaque(tag: str) -> str:
    """Strip a ``W/`` weak prefix and the surrounding quotes to the opaque tag value."""
    text = tag.strip()
    if text.startswith("W/"):
        text = text[2:].strip()
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    return text
