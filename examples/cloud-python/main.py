"""Quickstart of `ratel(cloud=...)`: one option turns on Ratel Cloud.

`r.cloud` reaches the Cloud API from the same object. The script runs against
MockCloud, an in-process fake of the Cloud API, passed as the transport. It sends
no request over the network and uses no real key or user data.

    uv run python main.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections import Counter

from ratel_ai import ExecutableTool, ratel
from ratel_ai.cloud.testing import MockCloud

# A fake key: `cloud` reads RATEL_API_KEY when the option sets no `api_key`.
os.environ.setdefault("RATEL_API_KEY", "rk_test_demo")
BASE_URL = "https://mock.ratel.test/api/v1"


async def main() -> int:
    """Run the quickstart and return the exit code."""
    mock = MockCloud(api_key=os.environ["RATEL_API_KEY"])

    # One option turns on Cloud: runtime events and catalog snapshots go to MockCloud.
    r = ratel(cloud={"base_url": BASE_URL, "transport": mock})

    await r.tools.register(
        [
            ExecutableTool(
                id="deploy",
                name="deploy",
                description="Deploy the current build to production.",
                input_schema={"type": "object", "properties": {"service": {"type": "string"}}},
                execute=lambda args: f"deployed {args.get('service')}",
            ),
            ExecutableTool(
                id="rollback",
                name="rollback",
                description="Roll back the last production deploy.",
                input_schema={"type": "object", "properties": {"service": {"type": "string"}}},
                execute=lambda args: f"rolled back {args.get('service')}",
            ),
        ]
    )

    # One turn: a search, then an invoke of the best hit.
    async with r.turn(user_message="Roll back the billing deploy"):
        hits = r.tools.search("roll back the production deploy", 2)
        if not hits:
            raise RuntimeError("the search found no tool")
        best = hits[0].tool_id
        print(f"search  -> {best}")
        print(f"invoke  -> {await r.tools.invoke(best, {'service': 'billing'})}")

    # The same object reaches the Cloud management API, with the same key.
    await r.cloud.skills.create(
        {
            "name": "rollback-checklist",
            "description": "Steps to roll back a production deploy safely.",
            "body": "# Rollback\n1. Freeze deploys.\n2. Run `rollback`.\n3. Check the error rate.",
        }
    )
    skills = (await r.cloud.skills.list())["skills"]
    print(f"skills  -> {', '.join(skill['name'] for skill in skills)}\n")

    await r.cloud.flush()
    status = r.cloud.status()
    print("r.cloud.status():")
    print(json.dumps(status, indent=2))

    print("\nMockCloud received:")
    types = Counter(event["type"] for event in mock.runtime_events)
    summary = ", ".join(f"{name} x{count}" for name, count in types.items())
    print(f"  runtime events:    {len(mock.runtime_events)} ({summary})")
    print(f"  catalog snapshots: {len(mock.catalog_snapshots)}")
    for snapshot in mock.catalog_snapshots:
        tool_ids = ", ".join(tool["id"] for tool in snapshot["tools"])
        print(f"    source {snapshot['source_id']}: {tool_ids}")

    await r.cloud.close()

    ok = status["events"]["accepted"] > 0 and len(mock.catalog_snapshots) == 1 and len(skills) == 1
    print("\nquickstart passed" if ok else "\nquickstart failed")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
