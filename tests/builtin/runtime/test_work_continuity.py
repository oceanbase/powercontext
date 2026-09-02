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

import pytest
from pydantic import ValidationError

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    BuiltinRuntime,
    CaptureSource,
    HandoffContent,
    HandoffSourceCitation,
    HandoffStatement,
    InvalidRuntimeRequestError,
    PreparedHandoff,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.work import (
    AcknowledgeHandoff,
    CreateWorkContract,
    CurrentWorkHandoff,
    HandoffCurrentWork,
    ReceiverChecks,
    RecordTaskOutcome,
    TaskOutcome,
    WorkClaim,
    WorkContract,
)
from powercontext.sources import SourceRef

CONFIRMED_RECEIVER_CHECKS = ReceiverChecks(
    live_state="confirmed",
    capability="confirmed",
    authorization="confirmed",
)
UNCHECKED_RECEIVER_CHECKS = ReceiverChecks(
    live_state="not_checked",
    capability="not_checked",
    authorization="not_checked",
)


async def _create_scope(runtime: BuiltinRuntime, idempotency_key: str) -> str:
    assert runtime.scopes is not None
    scope = await runtime.scopes.create(
        ScopeDraft(title="Work Test", summary="Work continuity test", idempotency_key=idempotency_key)
    )
    return scope.scope_id


def test_verified_work_claims_require_exact_evidence() -> None:
    with pytest.raises(ValidationError, match="verified Work claims require exact evidence"):
        WorkClaim(text="Tests pass.", basis="verified")

    with pytest.raises(ValidationError, match="declared Work claims cannot present evidence as verified"):
        WorkClaim(
            text="Tests pass.",
            basis="declared",
            evidence=(
                HandoffSourceCitation(
                    source_ref=SourceRef(source_type="content", source_id="test-output"),
                ),
            ),
        )

    with pytest.raises(ValidationError, match="at most 31 items"):
        WorkClaim(
            text="The handoff reserves one citation for its captured boundary.",
            basis="verified",
            evidence=tuple(
                HandoffSourceCitation(
                    source_ref=SourceRef(source_type="content", source_id=f"evidence-{index}"),
                )
                for index in range(32)
            ),
        )


def test_acknowledgement_cannot_accept_unavailable_handoff_evidence() -> None:
    async def scenario() -> None:
        missing = HandoffSourceCitation(
            source_ref=SourceRef(source_type="content", source_id="missing-output"),
        )
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope_id = await _create_scope(runtime, "unavailable-handoff-evidence")
            prepared = PreparedHandoff(
                scope_id=scope_id,
                base=None,
                content=HandoffContent(
                    objective="Continue a partially verified change.",
                    state=(HandoffStatement(text="The change was reported as implemented.", citations=(missing,)),),
                    disposition="continuable",
                ),
            )
            work = runtime.work.for_scope(scope_id)

            with pytest.raises(InvalidRuntimeRequestError, match="handoff-evidence-unavailable"):
                await work.acknowledge(
                    AcknowledgeHandoff(
                        source_id="receipt-accepted",
                        receiver="receiver-agent",
                        status="accepted",
                        selection="prepared",
                        receiver_checks=CONFIRMED_RECEIVER_CHECKS,
                        prepared=prepared,
                    )
                )

            clarification = await work.acknowledge(
                AcknowledgeHandoff(
                    source_id="receipt-clarification",
                    receiver="receiver-agent",
                    status="needs_clarification",
                    selection="prepared",
                    prepared=prepared,
                    message="The cited implementation output is unavailable.",
                )
            )

        assert clarification.resolution.evidence_checks[0].status == "unavailable"
        assert clarification.receipt.kind == "handoff-receipt"
        assert clarification.receipt.position == 1

    asyncio.run(scenario())


def test_acknowledgement_requires_an_inspected_target_and_receiver_checks() -> None:
    revision = ArtifactRef(family="handoff", artifact_id="handoff", revision=1)

    with pytest.raises(ValidationError, match="Input should be 'prepared' or 'exact'"):
        AcknowledgeHandoff.model_validate({
            "source_id": "receipt-latest",
            "receiver": "receiver-agent",
            "status": "accepted",
            "selection": "latest",
            "receiver_checks": CONFIRMED_RECEIVER_CHECKS.model_dump(),
        })

    with pytest.raises(ValidationError, match="requires all receiver checks"):
        AcknowledgeHandoff(
            source_id="receipt-missing-checks",
            receiver="receiver-agent",
            status="accepted",
            selection="exact",
            revision=revision,
        )

    with pytest.raises(ValidationError, match="requires all receiver checks"):
        AcknowledgeHandoff(
            source_id="receipt-unchecked",
            receiver="receiver-agent",
            status="accepted",
            selection="exact",
            receiver_checks=UNCHECKED_RECEIVER_CHECKS,
            revision=revision,
        )


def test_work_contract_rejects_a_verified_cross_record_claim_when_evidence_is_missing() -> None:
    async def scenario() -> None:
        contract = WorkContract(
            objective="Use only evidence-backed facts.",
            facts=(
                WorkClaim(
                    text="A regression test passed.",
                    basis="verified",
                    evidence=(
                        HandoffSourceCitation(
                            source_ref=SourceRef(source_type="content", source_id="missing-test-output"),
                        ),
                    ),
                ),
            ),
            in_scope=("Record a grounded delegation baseline.",),
            completion_criteria=("Reject unavailable verified evidence.",),
        )
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope_id = await _create_scope(runtime, "missing-contract-evidence")
            with pytest.raises(LookupError):
                await runtime.work.for_scope(scope_id).create_contract(
                    CreateWorkContract(source_id="contract-1", contract=contract)
                )

    asyncio.run(scenario())


def test_continuity_projects_the_result_loop_in_stable_journal_order() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope_id = await _create_scope(runtime, "continuity-result-loop")
            work = runtime.work.for_scope(scope_id)
            await work.create_contract(
                CreateWorkContract(
                    source_id="contract-1",
                    contract=WorkContract(
                        objective="Transfer an implementation safely.",
                        in_scope=("Implement the requested change.",),
                        completion_criteria=("Record the receiver's result.",),
                    ),
                )
            )
            prepared = await work.handoff_current(
                HandoffCurrentWork(
                    source_id="handoff-1",
                    handoff=CurrentWorkHandoff(
                        objective="Transfer an implementation safely.",
                        state=(WorkClaim(text="The implementation is ready for review."),),
                        disposition="continuable",
                        next_action=WorkClaim(text="Run the focused acceptance test."),
                    ),
                )
            )
            committed = await runtime.handoff.for_scope(scope_id).commit(prepared.handoff)
            before_acceptance = await work.continuity()
            acknowledgement = await work.acknowledge(
                AcknowledgeHandoff(
                    source_id="receipt-1",
                    receiver="receiver-agent",
                    status="accepted",
                    selection="exact",
                    receiver_checks=CONFIRMED_RECEIVER_CHECKS,
                    revision=committed.as_ref(),
                )
            )
            before_outcome = await work.continuity()
            await work.record_outcome(
                RecordTaskOutcome(
                    source_id="outcome-1",
                    outcome=TaskOutcome(
                        objective="Transfer an implementation safely.",
                        status="succeeded",
                        summary="The receiver completed the focused acceptance test.",
                        handoff_receipt_ref=acknowledgement.receipt.source_ref,
                        observations=(WorkClaim(text="The acceptance test passed."),),
                    ),
                )
            )
            complete = await work.continuity()

        assert before_acceptance.coverage.transfer_state == "awaiting_receipt"
        assert before_acceptance.coverage.outcome_state == "not_expected"
        assert before_outcome.coverage.transfer_state == "accepted"
        assert before_outcome.coverage.outcome_state == "awaiting_outcome"
        assert before_outcome.coverage.active_receipt_ref == acknowledgement.receipt.source_ref
        assert complete.coverage.transfer_state == "accepted"
        assert complete.coverage.outcome_state == "covered"
        assert complete.coverage.handoff_result_covered is True
        assert complete.coverage.active_receipt_ref == acknowledgement.receipt.source_ref
        assert complete.coverage.contract_records == 1
        assert complete.coverage.handoff_records == 1
        assert complete.coverage.acknowledgement_records == 1
        assert complete.coverage.outcome_records == 1
        assert [event.position for event in complete.events] == [1, 2, 3, 4]
        assert [event.kind for event in complete.events] == [
            "work-contract",
            "handoff-boundary",
            "handoff-receipt",
            "task-outcome",
        ]

    asyncio.run(scenario())


def test_continuity_does_not_cover_an_acceptance_with_an_unlinked_outcome() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope_id = await _create_scope(runtime, "continuity-unlinked-outcome")
            work = runtime.work.for_scope(scope_id)
            prepared = await work.handoff_current(
                HandoffCurrentWork(
                    source_id="handoff-1",
                    handoff=CurrentWorkHandoff(
                        objective="Transfer one exact attempt.",
                        state=(WorkClaim(text="The attempt is ready."),),
                        disposition="continuable",
                    ),
                )
            )
            committed = await runtime.handoff.for_scope(scope_id).commit(prepared.handoff)
            await work.acknowledge(
                AcknowledgeHandoff(
                    source_id="receipt-1",
                    receiver="receiver-agent",
                    status="accepted",
                    selection="exact",
                    receiver_checks=CONFIRMED_RECEIVER_CHECKS,
                    revision=committed.as_ref(),
                )
            )
            await work.record_outcome(
                RecordTaskOutcome(
                    source_id="unlinked-outcome",
                    outcome=TaskOutcome(
                        objective="Complete unrelated work.",
                        status="succeeded",
                        summary="An unrelated attempt completed.",
                        observations=(WorkClaim(text="This result does not identify the accepted receipt."),),
                    ),
                )
            )

            continuity = await work.continuity()

        assert continuity.coverage.transfer_state == "accepted"
        assert continuity.coverage.outcome_state == "awaiting_outcome"
        assert continuity.coverage.handoff_result_covered is False

    asyncio.run(scenario())


def test_task_outcome_rejects_a_non_receipt_result_link() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope_id = await _create_scope(runtime, "non-receipt-result-link")
            with pytest.raises(InvalidRuntimeRequestError, match="task-outcome-handoff-receipt"):
                await runtime.work.for_scope(scope_id).record_outcome(
                    RecordTaskOutcome(
                        source_id="outcome-1",
                        outcome=TaskOutcome(
                            objective="Link one exact accepted receipt.",
                            status="unknown",
                            summary="The requested Receipt does not exist.",
                            handoff_receipt_ref=SourceRef(
                                source_type="content",
                                source_id="missing-receipt",
                            ),
                            observations=(WorkClaim(text="No accepted Receipt was found."),),
                        ),
                    )
                )

    asyncio.run(scenario())


def test_continuity_excludes_malformed_work_records_without_failing_the_report() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            scope_id = await _create_scope(runtime, "malformed-work-records")
            sources = runtime.sources.for_scope(scope_id)
            await sources.capture(CaptureSource(source_id="ordinary-1", content="ordinary", metadata={"kind": []}))
            await sources.capture(
                CaptureSource(source_id="malformed-1", content="{}", metadata={"kind": "task-outcome"})
            )

            continuity = await runtime.work.for_scope(scope_id).continuity()

        assert continuity.total_event_count == 0
        assert continuity.invalid_record_count == 1
        assert continuity.events == ()

    asyncio.run(scenario())
