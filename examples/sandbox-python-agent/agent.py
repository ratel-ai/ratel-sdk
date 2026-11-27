"""The entry point of the image: `serve(run)` and the two tools with their modes.

The agent has no model, so a run needs no secret: it finds the order id in the message, looks the
order up and refunds it when the refund window allows it.
"""

from __future__ import annotations

import re
from typing import Any

import tools
from ratel_ai.serve import RunArgs, serve, tool

lookup_order = tool("lookup_order", execute=tools.lookup_order, fake=tools.lookup_order)
refund_order = tool(
    "refund_order", execute=tools.refund_order, fake=tools.fake_refund_order
)

ORDER_ID = re.compile(r"\b[A-Z]\d{3}\b")
OPENINGS = {"friendly": "Hi there!", "formal": "Dear customer,"}


def run(args: RunArgs) -> dict[str, Any]:
    """Answer one support message: `{"message": "..."}`."""
    message = str((args.input or {}).get("message", ""))
    opening = OPENINGS.get(str(args.params.get("tone")), OPENINGS["friendly"])
    match = ORDER_ID.search(message)
    if match is None:
        return {"reply": f"{opening} Which order is it? Give me its id, e.g. A100."}

    order = lookup_order(order_id=match.group())
    if not order["found"]:
        return {"reply": f"{opening} I cannot find the order {order['order_id']}."}
    if "refund" not in message.lower():
        return {
            "reply": f"{opening} Your {order['item']} was delivered.",
            "order": order,
        }
    if order["deliveredDaysAgo"] > tools.REFUND_WINDOW_DAYS:
        days = tools.REFUND_WINDOW_DAYS
        return {
            "reply": f"{opening} The refund window of {days} days is over.",
            "order": order,
        }

    refund = refund_order(order_id=order["order_id"])
    return {
        "reply": f"{opening} I refunded your {order['item']}: {order['total']} USD.",
        "refund": refund,
    }


if __name__ == "__main__":
    serve(run)
