# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Deterministic read projection for the Work continuity loop."""

from __future__ import annotations

from collections import Counter
from typing import cast

from pydantic import BaseModel, ValidationError

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.sources import ContentSource, SourceJournalEntry
from powercontext.builtin.work.models import (
    MAX_WORK_CONTINUITY_EVENTS,
    CurrentWorkHandoff,
    HandoffReceipt,
    TaskOutcome,
    WorkContinuity,
    WorkContinuityCoverage,
    WorkContinuityEvent,
    WorkContract,
    WorkOutcomeState,
    WorkSourceKind,
    WorkTransferState,
)
from powercontext.sources import SourceRef

_RECORD_MODELS: dict[WorkSourceKind, type[BaseModel]] = {
    "work-contract": WorkContract,
    "handoff-boundary": CurrentWorkHandoff,
    "handoff-receipt": HandoffReceipt,
    "task-outcome": TaskOutcome,
}


class _UnsupportedWorkRecordError(TypeError):
    def __init__(self, record_type: type[BaseModel]) -> None:
        super().__init__(f"unsupported Work continuity record: {record_type!r}")


def project_work_continuity(
    scope_id: str,
    entries: tuple[SourceJournalEntry, ...],
    /,
    *,
    selected_handoff: ArtifactRef | None = None,
) -> WorkContinuity:
    """Project valid high-level Work records without treating malformed Sources as history."""

    events: list[WorkContinuityEvent] = []
    invalid_record_count = 0
    for entry in entries:
        source = entry.source
        if not isinstance(source, ContentSource):
            continue
        kind = source.metadata.get("kind")
        if not isinstance(kind, str) or kind not in _RECORD_MODELS:
            continue
        work_kind = cast(WorkSourceKind, kind)
        try:
            record = _RECORD_MODELS[work_kind].model_validate_json(source.content)
        except ValidationError:
            invalid_record_count += 1
            continue
        events.append(_event(entry, work_kind, record))

    coverage = _coverage(tuple(events), selected_handoff)
    projected_events = tuple(events[-MAX_WORK_CONTINUITY_EVENTS:])
    return WorkContinuity(
        scope_id=scope_id,
        selected_handoff=selected_handoff,
        total_event_count=len(events),
        invalid_record_count=invalid_record_count,
        truncated=len(events) > len(projected_events),
        events=projected_events,
        coverage=coverage,
    )


def _event(entry: SourceJournalEntry, kind: WorkSourceKind, record: BaseModel) -> WorkContinuityEvent:
    handoff_receipt_ref = None
    receiver_checks = None
    if isinstance(record, WorkContract):
        status = "delegated"
        summary = record.objective
        actor = None
        selected_revision = None
    elif isinstance(record, CurrentWorkHandoff):
        status = record.disposition
        summary = record.objective
        actor = None
        selected_revision = None
    elif isinstance(record, HandoffReceipt):
        status = record.status
        summary = record.message
        actor = record.receiver
        selected_revision = record.selected_revision
        receiver_checks = record.receiver_checks
    elif isinstance(record, TaskOutcome):
        status = record.status
        summary = record.summary
        actor = None
        selected_revision = None
        handoff_receipt_ref = record.handoff_receipt_ref
    else:  # pragma: no cover - guarded by the record model table
        raise _UnsupportedWorkRecordError(type(record))
    schema = record.model_dump(by_alias=True)["schema"]
    return WorkContinuityEvent(
        position=entry.position,
        kind=kind,
        source_ref=entry.source_ref,
        record_schema=cast(str, schema),
        status=status,
        summary=summary,
        actor=actor,
        selected_revision=selected_revision,
        handoff_receipt_ref=handoff_receipt_ref,
        receiver_checks=receiver_checks,
    )


def _coverage(
    events: tuple[WorkContinuityEvent, ...],
    selected_handoff: ArtifactRef | None,
) -> WorkContinuityCoverage:
    counts = Counter(event.kind for event in events)
    matching_receipts = tuple(
        event for event in events if event.kind == "handoff-receipt" and event.selected_revision == selected_handoff
    )
    transfer_state, outcome_state, active_receipt_ref = _coverage_states(events, selected_handoff, matching_receipts)
    return WorkContinuityCoverage(
        contract_records=counts["work-contract"],
        handoff_records=counts["handoff-boundary"],
        acknowledgement_records=counts["handoff-receipt"],
        outcome_records=counts["task-outcome"],
        transfer_state=transfer_state,
        outcome_state=outcome_state,
        active_receipt_ref=active_receipt_ref,
        handoff_result_covered=outcome_state == "covered",
    )


def _coverage_states(
    events: tuple[WorkContinuityEvent, ...],
    selected_handoff: ArtifactRef | None,
    matching_receipts: tuple[WorkContinuityEvent, ...],
) -> tuple[WorkTransferState, WorkOutcomeState, SourceRef | None]:
    if selected_handoff is None:
        return "not_applicable", "not_expected", None
    if not matching_receipts:
        return "awaiting_receipt", "not_expected", None

    latest_receipt = max(matching_receipts, key=lambda event: event.position)
    if latest_receipt.status == "needs_clarification":
        return "needs_clarification", "not_expected", None
    if latest_receipt.status == "declined":
        return "declined", "not_expected", None
    if latest_receipt.status != "accepted":  # pragma: no cover - receipt schema constrains this branch
        raise ValueError("unsupported Handoff receipt status")  # noqa: TRY003

    has_linked_outcome = any(
        event.kind == "task-outcome"
        and event.position > latest_receipt.position
        and event.handoff_receipt_ref == latest_receipt.source_ref
        for event in events
    )
    return "accepted", "covered" if has_linked_outcome else "awaiting_outcome", latest_receipt.source_ref


__all__ = ["project_work_continuity"]
