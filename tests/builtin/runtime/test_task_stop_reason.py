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

from __future__ import annotations

import asyncio
import json
from hashlib import sha256
from pathlib import Path
from typing import get_args

import pytest

from powercontext.builtin.artifacts.handoff import HandoffEvidenceUnavailableError, HandoffSourceCitation
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.work import RecordTaskOutcome, TaskOutcome, TaskStopReason, WorkClaim
from powercontext.builtin.work.models import TaskOutcomeStatus, WorkClaimBasis
from powercontext.sources import SourceRef


@pytest.mark.parametrize("status", ["blocked", "cancelled"])
@pytest.mark.parametrize("basis", ["declared", "verified"])
def test_stop_reason_survives_sqlite_reopen_and_continuity(
    tmp_path: Path, status: TaskOutcomeStatus, basis: WorkClaimBasis
) -> None:
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'work.db'}"))

    async def scenario() -> None:
        async with open_builtin_runtime(config) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(summary="Work outcome test", title="Stop reason", idempotency_key="stop-reason")
            )
            sources = runtime.sources.for_scope(scope.scope_id)
            evidence = await sources.capture(
                CaptureSource(metadata={}, source_id="evidence-1", content="Required condition is absent.")
            )
            reason = TaskStopReason(
                kind="safety" if status == "blocked" else "scope",
                condition="继续执行超出已确认的安全范围。",
                basis=basis,
                evidence=() if basis == "declared" else (HandoffSourceCitation(source_ref=evidence.source_ref),),
            )
            outcome = TaskOutcome(
                objective="Attempt an action.",
                status=status,
                summary="The caller deliberately stopped.",
                observations=(WorkClaim(text="The caller reported stopping."),),
                stop_reason=reason,
            )
            receipt = await runtime.work.for_scope(scope.scope_id).record_outcome(
                RecordTaskOutcome(source_id="outcome-1", outcome=outcome)
            )

        async with open_builtin_runtime(config) as runtime:
            stored = await runtime.records.for_scope(scope.scope_id).get_source("content", "outcome-1")
            assert isinstance(stored.content, str)
            assert TaskOutcome.model_validate_json(stored.content) == outcome
            assert json.loads(stored.content)["stop_reason"] == reason.model_dump(mode="json")
            continuity = await runtime.work.for_scope(scope.scope_id).continuity()
            assert continuity.invalid_record_count == 0
            assert continuity.coverage.outcome_records == 1
            assert continuity.events[0].status == status
            assert continuity.events[0].source_ref == receipt.source_ref
            assert continuity.coverage.transfer_state == "not_applicable"
            assert continuity.coverage.outcome_state == "not_expected"

    asyncio.run(scenario())


def test_legacy_outcomes_preserve_source_bytes_and_digests_for_every_status() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(summary="Work outcome test", title="Legacy outcomes", idempotency_key="legacy-outcomes")
            )
            work = runtime.work.for_scope(scope.scope_id)
            for status in get_args(TaskOutcomeStatus):
                legacy = {
                    "schema": "powercontext.task-outcome.v1",
                    "trust": "untrusted_observation",
                    "objective": "Attempt an action.",
                    "status": status,
                    "summary": "The caller reported a result.",
                    "handoff_receipt_ref": None,
                    "observations": [{"text": "The caller reported a result.", "basis": "declared", "evidence": []}],
                    "checks": [],
                    "produced_artifacts": [],
                    "remaining_work": [],
                }
                compact = json.dumps(legacy, separators=(",", ":"))
                outcome = TaskOutcome.model_validate_json(compact)
                assert outcome.stop_reason is None
                assert outcome.model_dump_json(by_alias=True, exclude_none=False) == compact
                # Explicit null and nesting must also preserve the old representation.
                assert (
                    TaskOutcome.model_validate_json(json.dumps({**legacy, "stop_reason": None})).model_dump(
                        mode="json", by_alias=True
                    )
                    == legacy
                )
                request = RecordTaskOutcome(source_id=f"outcome-{status}", outcome=outcome)
                assert request.model_dump(mode="json", by_alias=True)["outcome"] == legacy
                receipt = await work.record_outcome(request)
                assert receipt.content_digest == f"sha256:{sha256(compact.encode()).hexdigest()}"
                stored = await runtime.records.for_scope(scope.scope_id).get_source("content", f"outcome-{status}")
                assert stored.content == json.dumps(legacy, indent=2)
            continuity = await work.continuity()
            assert continuity.invalid_record_count == 0
            assert [event.status for event in continuity.events] == list(get_args(TaskOutcomeStatus))

    asyncio.run(scenario())


def test_stop_reason_rejects_unavailable_evidence_before_recording() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            assert runtime.scopes is not None
            other = await runtime.scopes.create(
                ScopeDraft(summary="Work outcome test", title="Other scope", idempotency_key="other")
            )
            await runtime.sources.for_scope(other.scope_id).capture(
                CaptureSource(metadata={}, source_id="other-evidence", content="Other scope.")
            )
            scope = await runtime.scopes.create(
                ScopeDraft(summary="Work outcome test", title="Stop reason", idempotency_key="stop-reason")
            )
            work = runtime.work.for_scope(scope.scope_id)
            for source_id in ("missing", "other-evidence"):
                outcome = TaskOutcome(
                    objective="Attempt an action.",
                    status="blocked",
                    summary="The caller stopped.",
                    observations=(WorkClaim(text="The caller reported stopping."),),
                    stop_reason=TaskStopReason(
                        kind="authorization",
                        condition="Authorization was not confirmed.",
                        basis="verified",
                        evidence=(
                            HandoffSourceCitation(source_ref=SourceRef(source_type="content", source_id=source_id)),
                        ),
                    ),
                )
                with pytest.raises(HandoffEvidenceUnavailableError):
                    await work.record_outcome(RecordTaskOutcome(source_id="outcome-1", outcome=outcome))
            continuity = await work.continuity()
            assert continuity.total_event_count == 0

    asyncio.run(scenario())
