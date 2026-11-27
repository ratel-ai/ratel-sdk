"""The two tools of the support agent: a read-only lookup and a refund with a side effect."""

from __future__ import annotations

import sys
from typing import Any

ORDERS: dict[str, dict[str, Any]] = {
    "A100": {"item": "desk lamp", "deliveredDaysAgo": 12, "total": 39.0},
    "A200": {"item": "glass vase", "deliveredDaysAgo": 95, "total": 64.5},
}
REFUND_WINDOW_DAYS = 30


def lookup_order(order_id: str) -> dict[str, Any]:
    """The order with this id, or `{"found": False}`. Read-only."""
    order = ORDERS.get(order_id)
    return (
        {"order_id": order_id, "found": False}
        if order is None
        else {
            "order_id": order_id,
            "found": True,
            **order,
        }
    )


def refund_order(order_id: str) -> dict[str, Any]:
    """The real refund: a side effect. In a run of Ratel it is simulated."""
    print(f"REAL REFUND of order {order_id}", file=sys.stderr)
    return {"order_id": order_id, "status": "refunded", "real": True}


def fake_refund_order(order_id: str) -> dict[str, Any]:
    """The fake refund: the same answer, no side effect."""
    return {"order_id": order_id, "status": "refunded", "real": False}
