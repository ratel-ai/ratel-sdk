"""The canonical Cloud content hash of a catalog snapshot.

It is the Python port of ``hash.ts``. Both SDKs reproduce the shared vectors in
``protocol/v1/conformance/snapshot-hash-vectors.json``: compact JSON with object
keys and tool ids sorted by UTF-8 bytes, JavaScript number formatting, and raw
UTF-8 with minimal escaping.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from .types import RuntimeCatalogSnapshot

__all__ = ["hash_catalog_snapshot"]

# JavaScript writes a number in exponent form outside of 1e-7 < |x| < 1e21.
_JS_MAX_FIXED_EXPONENT = 21
_JS_MIN_FIXED_EXPONENT = -6


def hash_catalog_snapshot(snapshot: RuntimeCatalogSnapshot) -> str:
    """Return the canonical SHA-256 content hash of a full source snapshot.

    Args:
        snapshot: The snapshot. Tool order and object-key order do not change the hash.

    Returns:
        The lowercase hex SHA-256 digest.

    Raises:
        TypeError: A value cannot be serialized as JSON.
    """
    canonical = _stable_json(_canonical_snapshot(snapshot))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_snapshot(snapshot: RuntimeCatalogSnapshot) -> dict[str, Any]:
    tools: list[dict[str, Any]] = []
    for tool in snapshot["tools"]:
        canonical: dict[str, Any] = {
            "toolId": tool["id"],
            "name": tool["name"],
            "description": tool.get("description") or "",
            "inputSchema": tool.get("input_schema"),
            "outputSchema": tool.get("output_schema"),
            "metadata": tool.get("metadata"),
        }
        # Set only when non-empty. An unset searchable description keeps the
        # hashes of older publishers stable, and Cloud's canonical form agrees.
        searchable = tool.get("experimental_searchable_description")
        if searchable:
            canonical["searchableDescription"] = searchable
        tools.append(canonical)
    tools.sort(key=lambda item: _utf8_key(item["toolId"]))
    return {"source_id": snapshot["source_id"], "tools": tools}


def _stable_json(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return _js_number(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, Mapping):
        properties = sorted(value.items(), key=lambda item: _utf8_key(str(item[0])))
        body = ",".join(
            f"{json.dumps(str(key), ensure_ascii=False)}:{_stable_json(item)}"
            for key, item in properties
        )
        return f"{{{body}}}"
    if isinstance(value, (list, tuple)):
        return f"[{','.join(_stable_json(item) for item in value)}]"
    raise TypeError(f"catalog snapshot value of type {type(value).__name__} cannot be serialized")


def _js_number(value: int | float) -> str:
    """Format a number like JavaScript ``JSON.stringify`` (ECMAScript ``Number::toString``)."""
    if isinstance(value, int):
        return str(value)
    if not math.isfinite(value):
        return "null"
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    # repr() gives the shortest digits that round-trip, as JavaScript does.
    digits_tuple = Decimal(repr(abs(value))).normalize().as_tuple()
    digits = "".join(str(digit) for digit in digits_tuple.digits)
    exponent = int(digits_tuple.exponent)
    k = len(digits)
    n = exponent + k
    if k <= n <= _JS_MAX_FIXED_EXPONENT:
        return sign + digits + "0" * (n - k)
    if 0 < n <= _JS_MAX_FIXED_EXPONENT:
        return sign + digits[:n] + "." + digits[n:]
    if _JS_MIN_FIXED_EXPONENT < n <= 0:
        return sign + "0." + "0" * (-n) + digits
    e = n - 1
    mantissa = digits if k == 1 else digits[0] + "." + digits[1:]
    return f"{sign}{mantissa}e{'+' if e > 0 else '-'}{abs(e)}"


def _utf8_key(value: str) -> bytes:
    return value.encode("utf-8", "surrogatepass")
