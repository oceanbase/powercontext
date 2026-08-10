"""Optimistic, read-only selection of exact Handoff heads."""

from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.handoff_report.errors import HandoffReportBusyError
from powercontext.builtin.handoff_report.models import (
    ReportSelectionEntry,
    WorkstreamDescriptor,
)
from powercontext.builtin.handoff_report.protocols import HandoffReadAdapter

DEFAULT_HANDOFF_SELECTION_ATTEMPTS = 3
MAX_HANDOFF_SELECTION_ATTEMPTS = 5


async def select_optimistic_stable_handoffs(
    adapter: HandoffReadAdapter,
    workstreams: Sequence[WorkstreamDescriptor],
    /,
    *,
    attempts: int = DEFAULT_HANDOFF_SELECTION_ATTEMPTS,
) -> tuple[ReportSelectionEntry, ...]:
    """Freeze exact heads after two equal vectors, retrying bounded instability."""

    _validate_attempts(attempts)
    ordered = _ordered_workstreams(workstreams)
    for _ in range(attempts):
        first = await _read_head_vector(adapter, ordered)
        second = await _read_head_vector(adapter, ordered)
        if first == second:
            return tuple(
                ReportSelectionEntry(
                    scope_id=workstream.scope_id,
                    workstream_revision=workstream.version,
                    status="no_handoff" if reference is None else "selected",
                    handoff_ref=reference,
                )
                for workstream, reference in zip(ordered, second, strict=True)
            )
    raise HandoffReportBusyError(attempts)


async def _read_head_vector(
    adapter: HandoffReadAdapter,
    workstreams: tuple[WorkstreamDescriptor, ...],
) -> tuple[ArtifactRef | None, ...]:
    values: list[ArtifactRef | None] = []
    for workstream in workstreams:
        handoff = await adapter.latest(workstream.scope_id)
        values.append(None if handoff is None else handoff.as_ref())
    return tuple(values)


def _ordered_workstreams(values: Sequence[WorkstreamDescriptor]) -> tuple[WorkstreamDescriptor, ...]:
    ordered = tuple(sorted(values, key=lambda value: value.scope_id))
    for previous, current in pairwise(ordered):
        if previous.scope_id == current.scope_id:
            raise ValueError(f"duplicate Workstream scope_id: {current.scope_id}")  # noqa: TRY003
    return ordered


def _validate_attempts(value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= MAX_HANDOFF_SELECTION_ATTEMPTS:
        raise ValueError(  # noqa: TRY003
            f"attempts must be between 1 and {MAX_HANDOFF_SELECTION_ATTEMPTS}"
        )


__all__ = [
    "DEFAULT_HANDOFF_SELECTION_ATTEMPTS",
    "MAX_HANDOFF_SELECTION_ATTEMPTS",
    "select_optimistic_stable_handoffs",
]
