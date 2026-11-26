"""A two-arm retrieval experiment with `experimental_define_experiment`.

The control arm searches the tool catalog with the user's words. The candidate arm
first expands the query with synonyms. Each user gets a stable arm from the
deterministic split, the other arm runs as a shadow, and the comparisons, the
invocation and the outcome arrive on `r.events`. Everything runs in process.

    uv run python main.py
"""

from __future__ import annotations

import asyncio
import sys
from collections import Counter
from typing import Any

from ratel_ai import ExecutableTool, ExperimentRankedItem, experimental_define_experiment, ratel
from ratel_ai.experiment import ExperimentArmContext

TOOLS = {
    "rollback": "Roll back the last production deploy of a service.",
    "deploy": "Deploy the current build of a service to production.",
    "logs": "Search the production logs of a service for errors.",
    "page": "Page the on-call engineer of a service.",
}
SYNONYMS = {"undo": "roll back", "revert": "roll back", "ship": "deploy", "alert": "page"}
REQUESTS = {
    "user-1": "undo the billing release",
    "user-2": "revert the search deploy",
    "user-3": "ship the api build",
    "user-4": "alert whoever is on call",
    "user-5": "undo yesterday's deploy",
    "user-6": "find errors in the logs",
}


async def main() -> int:
    """Run the experiment for six users and return the exit code."""
    r = ratel()
    await r.tools.register(
        [
            ExecutableTool(id=tool_id, name=tool_id, description=text, execute=lambda _args: "ok")
            for tool_id, text in TOOLS.items()
        ]
    )
    received: list[dict[str, Any]] = []
    subscription = r.events.subscribe(lambda batch: received.extend(batch))

    async def control(query: str, _context: ExperimentArmContext) -> list[str]:
        return [hit.tool_id for hit in r.tools.search(query, 3)]

    async def candidate(query: str, _context: ExperimentArmContext) -> list[str]:
        expanded = " ".join(SYNONYMS.get(word, word) for word in query.split())
        return [hit.tool_id for hit in r.tools.search(expanded, 3)]

    def ranking(ids: list[str]) -> list[ExperimentRankedItem]:
        return [{"id": tool_id} for tool_id in ids]

    experiment = experimental_define_experiment(
        {
            "id": "query-expansion",
            "arms": {"control": {"select": control}, "candidate": {"select": candidate}},
            "split": [{"arm": "control", "weight": 50}, {"arm": "candidate", "weight": 50}],
            "ranking": ranking,
            "evaluation": {
                "k": 3,
                "references": ["peer-selection", {"kind": "invocation", "window": {"turns": 5}}],
                "outcome": True,
            },
        },
        r.events,
    )

    units: dict[str, str] = {}
    print(f"{'unit':<8} {'arm':<10} {'request':<28} top hits")
    for unit_id, request in REQUESTS.items():
        selection = await experiment.select(request, unit_id=unit_id, shadow=True)
        units[selection.selection_id] = unit_id
        print(f"{unit_id:<8} {selection.effective_arm:<10} {request:<28} {selection.result}")
        if selection.result:
            experiment.report_invocation(unit_id=unit_id, tool_id=selection.result[0])
            experiment.report_outcome(selection_id=selection.selection_id, label="accepted")

    await experiment.drain()
    await subscription.flush()

    print("\nshadow comparisons (served vs shadow):")
    comparisons = [e for e in received if e["type"] == "experiment_comparison"]
    for event in comparisons:
        agreement = event["agreement"]
        unit_id = units[event["selection_id"]]
        print(
            f"  {unit_id:<8} {event['served_arm']:>9} vs {event['shadow_arm']:<9}"
            f" top1={str(agreement['top1']):<5} jaccard@{agreement['k']}="
            f"{agreement['jaccardAtK']:.2f}"
        )
    counts = Counter(e["type"] for e in received if e["type"].startswith("experiment_"))
    print("\nexperiment events on r.events:")
    for event_type, count in sorted(counts.items()):
        print(f"  {event_type:<24} x{count}")

    arms = {e["arm"] for e in received if e["type"] == "experiment_selection"}
    ok = arms == {"control", "candidate"} and len(comparisons) == len(REQUESTS)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
