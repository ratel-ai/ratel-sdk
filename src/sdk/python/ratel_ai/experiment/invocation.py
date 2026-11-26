"""In-process invocation attribution (internal).

The Python mirror of `src/sdk/ts/src/experiment-invocation.ts`. One bounded buffer
per experiment remembers the ranked ids of recent selections per unit, so a later
tool invoke can be attributed to the selection that ranked it. The buffer keeps
at most 1,000 units and 32 selections per unit, and evicts the oldest first.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .definitions import ExperimentInvocationReference
from .validation import hash_unit_id

MAX_TRACKED_UNITS = 1_000
MAX_SELECTIONS_PER_UNIT = 32
_EXPIRY_SWEEP_BATCH = 8


@dataclass(frozen=True)
class ExperimentInvocationAttribution:
    """The attribution of one invoke to one selection, or no attribution.

    ``rank`` is the zero-based position of the tool in the selection's ranking,
    or -1 when the selection did not rank it.
    """

    attributed: bool
    selection_id: str | None = None
    effective_arm: str | None = None
    rank: int | None = None
    age_ms: float | None = None


@dataclass
class _SelectionRecord:
    selection_id: str
    effective_arm: str
    ids: list[str]
    completed_at_ms: float


class ExperimentInvocationBuffer:
    """The bounded selection window of one experiment instance."""

    def __init__(
        self,
        policy: ExperimentInvocationReference,
        clock: Callable[[], float] | None = None,
    ) -> None:
        """Build the buffer for ``policy``. ``clock`` returns monotonic milliseconds."""
        window = policy["window"]
        self._turns = window.get("turns")
        self._max_age_ms = window.get("max_age_ms")
        self._all_in_window = policy.get("attribution") == "all-in-window"
        self._clock = clock or _monotonic_ms
        self._selections: dict[str, list[_SelectionRecord]] = {}

    def record_selection(
        self, unit_id: str, selection_id: str, effective_arm: str, ids: Sequence[str]
    ) -> None:
        """Remember the ranked ids of one completed selection."""
        now = self._clock()
        self._sweep(now)
        unit_hash = hash_unit_id(unit_id)
        records = self._selections.pop(unit_hash, [])
        records.append(_SelectionRecord(selection_id, effective_arm, list(ids), now))
        del records[:-MAX_SELECTIONS_PER_UNIT]
        self._selections[unit_hash] = records
        while len(self._selections) > MAX_TRACKED_UNITS:
            del self._selections[next(iter(self._selections))]

    def evaluate_invocation(
        self, unit_id: str, tool_id: str
    ) -> list[ExperimentInvocationAttribution]:
        """Attribute one invoke of ``tool_id`` to the selections in the window."""
        now = self._clock()
        self._sweep(now)
        records = self._selections.get(hash_unit_id(unit_id))
        if records is None:
            return [ExperimentInvocationAttribution(attributed=False)]
        positional = records if self._turns is None else records[-self._turns :]
        in_window = [
            record
            for record in positional
            if self._max_age_ms is None
            or _elapsed_ms(now, record.completed_at_ms) <= self._max_age_ms
        ]
        matched = in_window if self._all_in_window else in_window[-1:]
        if not matched:
            return [ExperimentInvocationAttribution(attributed=False)]
        return [
            ExperimentInvocationAttribution(
                attributed=True,
                selection_id=record.selection_id,
                effective_arm=record.effective_arm,
                rank=record.ids.index(tool_id) if tool_id in record.ids else -1,
                age_ms=_elapsed_ms(now, record.completed_at_ms),
            )
            for record in matched
        ]

    def _sweep(self, now: float) -> None:
        if self._max_age_ms is None:
            return
        for examined, unit_hash in enumerate(list(self._selections)):
            if examined >= _EXPIRY_SWEEP_BATCH:
                return
            records = self._selections[unit_hash]
            live = [r for r in records if _elapsed_ms(now, r.completed_at_ms) <= self._max_age_ms]
            if not live:
                del self._selections[unit_hash]
            elif len(live) != len(records):
                records[:] = live


def _elapsed_ms(now: float, completed_at_ms: float) -> float:
    return max(0.0, now - completed_at_ms)


def _monotonic_ms() -> float:
    return time.perf_counter() * 1_000
