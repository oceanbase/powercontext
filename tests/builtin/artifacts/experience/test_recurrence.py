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

from typing import Any

import pytest
from pydantic import ValidationError

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.experience import (
    ExperienceContent,
    FailureRecord,
    FailureSignature,
    FailureVerification,
)
from powercontext.builtin.artifacts.experience.recurrence import (
    MAX_RECURRENCE_CANDIDATES,
    NEAR_DUPLICATE_BIGRAM_OVERLAP,
    RECURRENCE_REVIEW_STREAK_THRESHOLD,
    RecurrenceEvent,
    RecurrenceMatch,
    RecurrenceObservation,
    TaskOutcomeItemKind,
    TaskOutcomeItemRef,
    avoided_refs,
    candidate_set_digest,
    eligible_candidates,
    failure_item_text,
    failure_refs,
    freeze_candidate_set,
    is_failure_evidence,
    item_digest,
    match_result,
    near_duplicate_overlap,
    needing_review,
    new_observation,
    normalize_match_text,
    observation_id,
    selection_key,
    signature_key,
    terminal_streak,
    verdict_key,
    verified_check_refs,
    verified_observation_refs,
)
from powercontext.builtin.work.models import (
    TaskCheck,
    TaskCheckStatus,
    TaskOutcome,
    TaskOutcomeStatus,
    WorkClaim,
    WorkClaimBasis,
)
from powercontext.sources import SourceRef

CUE = "openapi contract changed without regenerating the client"
OUTCOME_REF = SourceRef(source_type="content", source_id="outcome-1")
RECEIPT_REF = SourceRef(source_type="content", source_id="receipt-1")
HANDOFF_REF = ArtifactRef(family="handoff", artifact_id="handoff-1", revision=1)
EXPERIENCE_REF = ArtifactRef(family="experience", artifact_id="experience-7", revision=1)


def _claim(text: str, *, basis: WorkClaimBasis = "verified") -> WorkClaim:
    from powercontext.builtin.artifacts.handoff import HandoffSourceCitation

    if basis == "declared":
        return WorkClaim(text=text)
    return WorkClaim(
        text=text,
        basis="verified",
        evidence=(HandoffSourceCitation(source_ref=SourceRef(source_type="content", source_id="evidence-1")),),
    )


def _check(name: str, *, status: TaskCheckStatus = "failed", basis: WorkClaimBasis = "verified") -> TaskCheck:
    from powercontext.builtin.artifacts.handoff import HandoffSourceCitation

    if basis == "declared":
        return TaskCheck(name=name, status=status)  # type: ignore[arg-type]
    return TaskCheck(
        name=name,
        status=status,
        basis="verified",
        evidence=(HandoffSourceCitation(source_ref=SourceRef(source_type="content", source_id="evidence-1")),),
    )


def _outcome(
    *,
    status: TaskOutcomeStatus = "failed",
    observations: tuple[WorkClaim, ...] = (),
    checks: tuple[TaskCheck, ...] = (),
    receipt_ref: SourceRef | None = None,
) -> TaskOutcome:
    return TaskOutcome(
        objective="Ship the regenerated client.",
        status=status,
        summary="The contract test failed again.",
        handoff_receipt_ref=receipt_ref,
        observations=observations or (_claim("The contract test failed."),),
        checks=checks,
    )


def _content(*, cue: str = CUE, condition: str = CUE, subject: str = "regenerate the client") -> ExperienceContent:
    return ExperienceContent(
        situation="The OpenAPI contract changed.",
        action="Regenerate the client.",
        outcome="The contract test passed.",
        lesson="Regenerate the client whenever the contract changes.",
        failure=FailureRecord(
            signature=FailureSignature(recall_cue=cue),
            repair_surface="experience_content",
            verification=FailureVerification(condition=condition, check_subject=subject),
        ),
    )


def _item_ref(
    *, kind: TaskOutcomeItemKind = "check", index: int = 0, outcome_ref: SourceRef = OUTCOME_REF
) -> TaskOutcomeItemRef:
    from powercontext.builtin.artifacts.experience.recurrence import item_digest

    item = _check("regenerate the client", status="failed") if kind == "check" else _claim("condition appeared")
    return TaskOutcomeItemRef(
        task_outcome_ref=outcome_ref,
        item_kind=kind,
        item_index=index,
        item_digest=item_digest(item),
    )


def _selected(*, position: int = 1, ref: ArtifactRef = EXPERIENCE_REF) -> RecurrenceObservation:
    return new_observation(
        event="selected",
        scope_id="scope-a",
        artifact_ref=ref,
        signature_key=CUE,
        task_outcome_ref=OUTCOME_REF,
        task_outcome_position=position,
        handoff_receipt_ref=RECEIPT_REF,
        handoff_ref=HANDOFF_REF,
    )


def _verdict(event: RecurrenceEvent, *, position: int = 2, failure: bool | None = None) -> RecurrenceObservation:
    outcome_ref = SourceRef(source_type="content", source_id=f"outcome-{position}")
    evidence = (
        {
            "failure_ref": _item_ref(outcome_ref=outcome_ref),
            "recurrence_match_digest": "sha256:" + "a" * 64,
        }
        if failure or (failure is None and event == "recurred")
        else {
            "handoff_receipt_ref": RECEIPT_REF,
            "handoff_ref": HANDOFF_REF,
            "condition_ref": _item_ref(kind="observation", outcome_ref=outcome_ref),
            "check_ref": _item_ref(outcome_ref=outcome_ref),
        }
    )
    return new_observation(
        event=event,
        scope_id="scope-a",
        artifact_ref=EXPERIENCE_REF,
        signature_key=CUE,
        task_outcome_ref=outcome_ref,
        task_outcome_position=position,
        **evidence,
    )


def test_normalization_is_stable_across_case_spacing_and_unicode_forms() -> None:
    assert normalize_match_text("  ＯｐｅｎＡＰＩ　Contract  ") == normalize_match_text("openapi contract")  # noqa: RUF001
    assert normalize_match_text("Contract:") == "contract"
    assert signature_key(CUE) == CUE


def test_normalization_rejects_text_without_visible_characters() -> None:
    with pytest.raises(ValueError, match="must contain visible characters"):
        normalize_match_text("...")
    with pytest.raises(ValueError, match="must contain visible characters"):
        normalize_match_text("   ")


def test_near_duplicate_overlap_is_advisory_and_bounded() -> None:
    assert near_duplicate_overlap(CUE, CUE) == 1.0
    assert near_duplicate_overlap(CUE, "pytest port already in use") == 0.0
    assert NEAR_DUPLICATE_BIGRAM_OVERLAP == 0.8
    near = "regenerate the openapi client after every contract change in the release"
    assert near_duplicate_overlap(CUE, near) < NEAR_DUPLICATE_BIGRAM_OVERLAP
    base = "regenerate the openapi client after every contract change in the repository"
    assert near_duplicate_overlap(base, near) >= NEAR_DUPLICATE_BIGRAM_OVERLAP


def test_only_verified_failure_evidence_becomes_a_locator() -> None:
    outcome = _outcome(
        observations=(_claim(CUE), _claim("declared claim", basis="declared")),
        checks=(_check(CUE), _check("skipped check", status="skipped")),
    )
    refs = failure_refs(outcome, OUTCOME_REF)

    assert [(ref.item_kind, ref.item_index) for ref in refs] == [("check", 0), ("observation", 0)]
    assert all(is_failure_evidence(outcome, ref.item_kind, ref.item_index) for ref in refs)  # type: ignore[arg-type]
    assert not is_failure_evidence(outcome, "check", 1)
    assert not is_failure_evidence(outcome, "observation", 1)


def test_a_successful_outcome_provides_no_observation_evidence() -> None:
    succeeded = _outcome(status="succeeded", observations=(_claim(CUE),), checks=(_check(CUE, status="passed"),))
    assert failure_refs(succeeded, OUTCOME_REF) == ()


def test_match_result_is_deterministic_and_never_chooses() -> None:
    assert match_result(0) == "unmatched"
    assert match_result(1) == "matched"
    assert match_result(2) == "ambiguous"
    assert match_result(9) == "ambiguous"


def test_candidate_sets_are_deduplicated_sorted_and_bounded() -> None:
    refs = tuple(
        ArtifactRef(family="experience", artifact_id=f"experience-{index}", revision=1)
        for index in range(MAX_RECURRENCE_CANDIDATES + 5)
    )
    frozen = freeze_candidate_set(mode="scope_heads", refs=refs + refs[:1])

    assert len(frozen) == MAX_RECURRENCE_CANDIDATES
    assert frozen == tuple(sorted(frozen, key=lambda ref: (ref.family, ref.artifact_id, ref.revision)))
    assert candidate_set_digest(frozen) == candidate_set_digest(frozen)
    assert candidate_set_digest(frozen) != candidate_set_digest(frozen[:-1])


def test_eligible_candidates_match_only_the_normalized_cue() -> None:
    exact = _content()
    other = _content(cue="pytest port already in use")
    candidates = ((EXPERIENCE_REF, exact), (ArtifactRef(family="experience", artifact_id="e8", revision=1), other))

    assert eligible_candidates(CUE, candidates) == (EXPERIENCE_REF,)
    assert eligible_candidates("OpenAPI  Contract   changed without regenerating the client", candidates) == (
        EXPERIENCE_REF,
    )
    assert eligible_candidates("something else", candidates) == ()
    assert eligible_candidates(CUE, ((EXPERIENCE_REF, ExperienceContent(**_plain())),)) == ()


def test_eligible_candidates_deduplicate_immutable_revisions() -> None:
    exact = _content()
    assert eligible_candidates(CUE, ((EXPERIENCE_REF, exact), (EXPERIENCE_REF, exact))) == (EXPERIENCE_REF,)


def test_a_stored_cue_without_a_failure_block_is_never_a_candidate() -> None:
    plain = ExperienceContent(**_plain())
    assert eligible_candidates(plain.situation, ((EXPERIENCE_REF, plain),)) == ()


def test_task_outcome_item_ref_rejects_noncanonical_digest() -> None:
    with pytest.raises(ValidationError, match="item_digest must be a sha256 digest"):
        TaskOutcomeItemRef(
            task_outcome_ref=OUTCOME_REF,
            item_kind="check",
            item_index=0,
            item_digest="sha256:not-a-digest",
        )


def test_task_outcome_item_ref_digest_is_the_immutable_item_identity() -> None:
    check = _check("regenerate the client")
    ref = TaskOutcomeItemRef(
        task_outcome_ref=OUTCOME_REF,
        item_kind="check",
        item_index=0,
        item_digest=item_digest(check),
    )
    assert ref.item_digest == item_digest(check)


def test_verdicts_are_ordered_by_immutable_journal_position() -> None:
    verdicts = (_verdict("recurred", position=5), _verdict("avoided", position=3), _verdict("recurred", position=9))
    assert terminal_streak(verdicts) == 2
    assert terminal_streak((_verdict("recurred", position=1), _verdict("recurred", position=2))) == 2


def test_avoided_clears_the_streak_without_changing_artifact_state() -> None:
    verdicts = (
        _verdict("recurred", position=1),
        _verdict("recurred", position=2),
        _verdict("avoided", position=3),
        _verdict("recurred", position=4),
    )
    assert terminal_streak(verdicts) == 1
    assert terminal_streak(verdicts) < RECURRENCE_REVIEW_STREAK_THRESHOLD


def test_needing_review_reaches_the_threshold_at_three() -> None:
    verdicts = tuple(_verdict("recurred", position=position) for position in (1, 2))
    assert needing_review(verdicts) is False
    assert needing_review((*verdicts, _verdict("recurred", position=3))) is True
    assert RECURRENCE_REVIEW_STREAK_THRESHOLD == 3


def test_ledger_keys_are_stable_and_do_not_collide_across_events() -> None:
    selected = _selected()
    assert selection_key(selected) == selection_key(_selected())
    assert verdict_key(selected) != verdict_key(_verdict("recurred"))
    assert selection_key(selected) != selection_key(_verdict("recurred"))
    assert len(selection_key(selected)) == 71


def test_observation_id_is_derived_from_the_event_evidence() -> None:
    first = _selected()
    assert first.observation_id == observation_id(
        event="selected",
        scope_id="scope-a",
        artifact_ref=EXPERIENCE_REF,
        signature_key=CUE,
        task_outcome_ref=OUTCOME_REF,
        task_outcome_position=1,
        handoff_receipt_ref=RECEIPT_REF,
        handoff_ref=HANDOFF_REF,
    )
    assert first.observation_id != _selected(position=2).observation_id


def test_a_matched_decision_must_name_a_target_from_its_own_candidate_set() -> None:
    candidates = freeze_candidate_set(mode="handoff_citations", refs=(EXPERIENCE_REF,))
    with pytest.raises(ValidationError, match="a matched decision requires its exact target"):
        RecurrenceMatch(
            scope_id="scope-a",
            task_outcome_ref=OUTCOME_REF,
            task_outcome_position=1,
            failure_ref=_item_ref(),
            candidate_set_mode="handoff_citations",
            candidate_refs=candidates,
            candidate_set_digest=candidate_set_digest(candidates),
            result="matched",
        )
    with pytest.raises(ValidationError, match="only a matched decision can identify a target"):
        RecurrenceMatch(
            scope_id="scope-a",
            task_outcome_ref=OUTCOME_REF,
            task_outcome_position=1,
            failure_ref=_item_ref(),
            candidate_set_mode="handoff_citations",
            candidate_refs=candidates,
            candidate_set_digest=candidate_set_digest(candidates),
            result="unmatched",
            artifact_ref=EXPERIENCE_REF,
        )


def test_the_decision_rejects_a_stale_candidate_set_digest() -> None:
    candidates = freeze_candidate_set(mode="handoff_citations", refs=(EXPERIENCE_REF,))
    with pytest.raises(ValidationError, match="candidate_set_digest must match"):
        RecurrenceMatch(
            scope_id="scope-a",
            task_outcome_ref=OUTCOME_REF,
            task_outcome_position=1,
            failure_ref=_item_ref(),
            candidate_set_mode="handoff_citations",
            candidate_refs=candidates,
            candidate_set_digest=candidate_set_digest(()),
            result="unmatched",
        )


def test_selected_cannot_carry_verdict_evidence() -> None:
    with pytest.raises(ValidationError, match="selected requires a Handoff receipt reference"):
        new_observation(
            event="selected",
            scope_id="scope-a",
            artifact_ref=EXPERIENCE_REF,
            signature_key=CUE,
            task_outcome_ref=OUTCOME_REF,
            task_outcome_position=1,
        )


def test_avoided_requires_both_locators_in_the_same_outcome() -> None:
    with pytest.raises(ValidationError, match="avoided requires a condition locator"):
        new_observation(
            event="avoided",
            scope_id="scope-a",
            artifact_ref=EXPERIENCE_REF,
            signature_key=CUE,
            task_outcome_ref=OUTCOME_REF,
            task_outcome_position=1,
            handoff_receipt_ref=RECEIPT_REF,
            handoff_ref=HANDOFF_REF,
        )
    with pytest.raises(ValidationError, match="must resolve inside the event's Task Outcome"):
        new_observation(
            event="avoided",
            scope_id="scope-a",
            artifact_ref=EXPERIENCE_REF,
            signature_key=CUE,
            task_outcome_ref=OUTCOME_REF,
            task_outcome_position=1,
            handoff_receipt_ref=RECEIPT_REF,
            handoff_ref=HANDOFF_REF,
            condition_ref=_item_ref(kind="observation"),
            check_ref=TaskOutcomeItemRef(
                task_outcome_ref=SourceRef(source_type="content", source_id="outcome-2"),
                item_kind="check",
                item_index=0,
                item_digest="sha256:" + "b" * 64,
            ),
        )


def test_recurred_carries_only_failure_evidence() -> None:
    with pytest.raises(ValidationError, match="recurred cannot carry a Handoff receipt reference"):
        new_observation(
            event="recurred",
            scope_id="scope-a",
            artifact_ref=EXPERIENCE_REF,
            signature_key=CUE,
            task_outcome_ref=OUTCOME_REF,
            task_outcome_position=1,
            failure_ref=_item_ref(),
            recurrence_match_digest="sha256:" + "a" * 64,
            handoff_receipt_ref=RECEIPT_REF,
        )
    with pytest.raises(ValidationError, match="recurred requires the digest of one frozen matching decision"):
        new_observation(
            event="recurred",
            scope_id="scope-a",
            artifact_ref=EXPERIENCE_REF,
            signature_key=CUE,
            task_outcome_ref=OUTCOME_REF,
            task_outcome_position=1,
            failure_ref=_item_ref(),
        )


def test_avoided_gates_require_a_unique_verified_condition_and_a_passed_check() -> None:
    content = _content()
    passed = _outcome(
        status="succeeded",
        observations=(_claim(CUE),),
        checks=(_check("regenerate the client", status="passed"),),
    )
    condition_ref, check_ref = avoided_refs(passed, OUTCOME_REF, content) or (None, None)

    assert condition_ref is not None and check_ref is not None
    assert condition_ref.item_kind == "observation"
    assert check_ref.item_kind == "check"


def test_avoided_is_not_written_when_the_check_did_not_pass() -> None:
    content = _content()
    failed = _outcome(
        status="failed",
        observations=(_claim(CUE),),
        checks=(_check("regenerate the client", status="failed"),),
    )
    assert avoided_refs(failed, OUTCOME_REF, content) is None


def test_avoided_is_not_written_when_evidence_is_declared_or_missing() -> None:
    content = _content()
    declared = _outcome(
        status="succeeded",
        observations=(_claim(CUE, basis="declared"),),
        checks=(_check("regenerate the client", status="passed"),),
    )
    assert avoided_refs(declared, OUTCOME_REF, content) is None
    assert verified_observation_refs(declared, OUTCOME_REF, CUE) == ()
    assert avoided_refs(_outcome(status="succeeded"), OUTCOME_REF, content) is None


def test_avoided_is_not_written_when_two_items_are_equally_plausible() -> None:
    content = _content()
    ambiguous = _outcome(
        status="succeeded",
        observations=(_claim(CUE), _claim(CUE)),
        checks=(_check("regenerate the client", status="passed"),),
    )
    assert len(verified_observation_refs(ambiguous, OUTCOME_REF, CUE)) == 2
    assert avoided_refs(ambiguous, OUTCOME_REF, content) is None
    assert len(verified_check_refs(ambiguous, OUTCOME_REF, "regenerate the client")) == 1


def test_a_content_without_a_failure_block_has_no_avoided_evidence() -> None:
    assert avoided_refs(_outcome(status="succeeded"), OUTCOME_REF, ExperienceContent(**_plain())) is None


def test_failure_item_text_uses_only_the_matchable_field() -> None:
    assert failure_item_text(_claim(CUE)) == CUE
    assert failure_item_text(_check(CUE, status="failed")) == CUE


def _plain() -> dict[str, Any]:
    return {
        "situation": "The OpenAPI contract changed.",
        "action": "Regenerate the client.",
        "outcome": "The contract test passed.",
        "lesson": "Regenerate the client whenever the contract changes.",
    }
