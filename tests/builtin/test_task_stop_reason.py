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

"""Deliberate-stop provenance and status constraints."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from powercontext.builtin.artifacts.experience.recurrence import failure_refs
from powercontext.builtin.artifacts.handoff import HandoffSourceCitation
from powercontext.builtin.work import TaskOutcome, TaskStopReason, WorkClaim
from powercontext.builtin.work.models import TaskOutcomeStatus
from powercontext.sources import SourceRef

CITATION = HandoffSourceCitation(source_ref=SourceRef(source_type="content", source_id="evidence-1"))


@pytest.mark.parametrize("status", ["succeeded", "partial", "failed", "unknown"])
def test_stop_reason_rejects_completed_or_indeterminate_status(status: TaskOutcomeStatus) -> None:
    with pytest.raises(ValidationError, match=f"requires blocked or cancelled status; got {status}"):
        TaskOutcome(
            objective="Attempt an action.",
            status=status,
            summary="The attempt stopped.",
            observations=(WorkClaim(text="The caller reported stopping."),),
            stop_reason=TaskStopReason(kind="safety", condition="Completion could not be guaranteed."),
        )


def test_stop_reason_requires_evidence_exactly_when_verified() -> None:
    with pytest.raises(ValidationError, match="verified Task stop reasons require exact evidence"):
        TaskStopReason(kind="safety", condition="Completion could not be guaranteed.", basis="verified")
    with pytest.raises(ValidationError, match="declared Task stop reasons cannot present evidence as verified"):
        TaskStopReason(kind="safety", condition="Completion could not be guaranteed.", evidence=(CITATION,))


@pytest.mark.parametrize("condition", ["", " ", "\n", " leading", "trailing ", "x" * 8193])
def test_stop_reason_requires_bounded_trimmed_condition(condition: str) -> None:
    with pytest.raises(ValidationError):
        TaskStopReason(kind="safety", condition=condition)


def test_stop_reason_rejects_unknown_kinds_and_inexact_evidence() -> None:
    with pytest.raises(ValidationError, match="kind"):
        TaskStopReason.model_validate({"kind": "refused", "condition": "Cannot continue."})
    with pytest.raises(ValidationError):
        TaskStopReason.model_validate_json(
            '{"kind":"safety","condition":"Cannot continue.","basis":"verified",'
            '"evidence":[{"kind":"artifact","artifact_ref":{"family":"handoff","artifact_id":"h"}}]}'
        )


def test_stop_reason_keeps_claim_evidence_bound() -> None:
    reason = TaskStopReason(
        kind="missing_evidence", condition="A prerequisite is unproven.", basis="verified", evidence=(CITATION,) * 31
    )
    assert len(reason.evidence) == 31
    with pytest.raises(ValidationError, match="at most 31 items"):
        TaskStopReason(
            kind="missing_evidence",
            condition="A prerequisite is unproven.",
            basis="verified",
            evidence=(CITATION,) * 32,
        )


@pytest.mark.parametrize("status", ["blocked", "cancelled"])
def test_verified_stop_reason_is_not_recurrence_failure_evidence(status: TaskOutcomeStatus) -> None:
    outcome = TaskOutcome(
        objective="Attempt an action.",
        status=status,
        summary="The attempt stopped.",
        observations=(WorkClaim(text="The caller reported stopping."),),
        stop_reason=TaskStopReason(
            kind="safety", condition="Completion could not be guaranteed.", basis="verified", evidence=(CITATION,)
        ),
    )
    assert failure_refs(outcome, SourceRef(source_type="content", source_id="outcome-1")) == ()
