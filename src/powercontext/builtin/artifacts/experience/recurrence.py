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

"""Pure recurrence matching, ledger keys, and verdict derivation.

This module performs no IO. It imports no repository, no connection, and no
database driver, because ``prepare_context`` and every retrieval path stay
read-only: the ledger is written only by the task-outcome incubation window.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Annotated, Any, Literal, TypeAlias

from pydantic import BaseModel, Field, model_validator

from powercontext.artifacts import ArtifactRef
from powercontext.artifacts.models import _ArtifactValue
from powercontext.builtin.artifacts.experience.incubation import MAX_EXPERIENCE_CANDIDATE_EVIDENCE
from powercontext.builtin.artifacts.experience.models import ExperienceContent
from powercontext.builtin.evidence.models import content_digest
from powercontext.builtin.work.models import TaskCheck, TaskOutcome, WorkClaim
from powercontext.sources import SourceRef

RECURRENCE_REVIEW_STREAK_THRESHOLD = 3
NEAR_DUPLICATE_BIGRAM_OVERLAP = 0.8
MAX_RECURRENCE_HANDOFF_SCAN = 64

RecurrenceEvent: TypeAlias = Literal["selected", "recurred", "avoided"]
MatchBasis: TypeAlias = Literal["exact"]
MatchResult: TypeAlias = Literal["matched", "unmatched", "ambiguous"]
CandidateSetMode: TypeAlias = Literal["handoff_citations", "scope_heads"]
TaskOutcomeItemKind: TypeAlias = Literal["observation", "check"]

_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _is_digest(value: str) -> bool:
    return _DIGEST_PATTERN.fullmatch(value) is not None


_FAILURE_OUTCOME_STATUSES = frozenset({"failed", "blocked"})
_FAILURE_CHECK_STATUSES = frozenset({"failed", "timed_out", "unavailable"})


def canonical_digest(value: BaseModel, /) -> str:
    """Return the canonical ``sha256:`` digest for one ledger value.

    The result is always 71 characters, which is why every ledger digest column
    is declared ``identity_string(71)``.
    """

    return content_digest(value.model_dump_json(by_alias=True, exclude_none=False).encode())


def normalize_match_text(value: str, /) -> str:
    """Fold one free-text match input into its comparison key.

    Unicode NFKC, case folding, whitespace collapsing, then edge punctuation
    removal. The normalized value is a comparison aid only; it is never the
    stored identity of a record.
    """

    folded = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
    normalized = _strip_edges(folded)
    if not normalized:
        raise ValueError("recurrence match text must contain visible characters")  # noqa: TRY003
    return normalized


def signature_key(cue: str, /) -> str:
    """Return the normalized comparison key carried by one failure signature."""

    return normalize_match_text(cue)


def near_duplicate_overlap(left: str, right: str, /) -> float:
    """Return the token bigram overlap of two cues, in ``[0, 1]``.

    This value only ever produces an advisory warning. Fuzzy similarity must
    never write a ledger counter: one wrong association silently corrupts the
    recurrence count this feature exists to produce.
    """

    left_bigrams = _bigrams(left)
    right_bigrams = _bigrams(right)
    if not left_bigrams or not right_bigrams:
        return 0.0
    return len(left_bigrams & right_bigrams) / len(left_bigrams | right_bigrams)


def item_digest(item: WorkClaim | TaskCheck, /) -> str:
    """Return the canonical digest of one immutable Task Outcome item."""

    return canonical_digest(item)


def failure_item_text(item: WorkClaim | TaskCheck, /) -> str:
    """Return the only text eligible as an exact match input for one item.

    ``TaskCheck.details``, an optional ``symptom``, and Task Outcome narration
    are deliberately never match inputs.
    """

    if isinstance(item, WorkClaim):
        return item.text
    return item.name


def is_failure_evidence(outcome: TaskOutcome, kind: TaskOutcomeItemKind, index: int, /) -> bool:
    """Report whether one Task Outcome item is evidence *for a failure*.

    Observations qualify only under a ``failed`` or ``blocked`` parent with a
    verified claim and non-empty exact evidence. Checks qualify only when
    verified with non-empty exact evidence and a ``failed``, ``timed_out``, or
    ``unavailable`` status. ``skipped``, ``cancelled``, and ``unknown`` never
    qualify.
    """

    if kind == "observation":
        if index >= len(outcome.observations) or outcome.status not in _FAILURE_OUTCOME_STATUSES:
            return False
        claim = outcome.observations[index]
        return claim.basis == "verified" and bool(claim.evidence)
    if index >= len(outcome.checks):
        return False
    check = outcome.checks[index]
    return check.basis == "verified" and bool(check.evidence) and check.status in _FAILURE_CHECK_STATUSES


def failure_refs(outcome: TaskOutcome, task_outcome_ref: SourceRef, /) -> tuple[TaskOutcomeItemRef, ...]:
    """Return every failure-evidence locator inside one Task Outcome.

    Checks precede observations and each kind keeps ascending index order, so
    the same Source window always yields the same locators.
    """

    return (
        *_refs_for_kind(outcome, task_outcome_ref, "check"),
        *_refs_for_kind(outcome, task_outcome_ref, "observation"),
    )


def freeze_candidate_set(
    *,
    mode: CandidateSetMode,
    refs: tuple[ArtifactRef, ...],
) -> tuple[ArtifactRef, ...]:
    """Sort and deduplicate one complete candidate snapshot before digesting it."""

    unique: dict[tuple[str, str, int], ArtifactRef] = {}
    for ref in refs:
        unique.setdefault(_ref_identity(ref), ref)
    ordered = tuple(unique[key] for key in sorted(unique))
    return ordered


def candidate_set_digest(refs: tuple[ArtifactRef, ...], /) -> str:
    """Return the canonical digest of one already normalized candidate snapshot."""

    return canonical_digest(_CandidateSetSnapshot(refs=refs))


def eligible_candidates(
    failure_text: str,
    candidates: tuple[tuple[ArtifactRef, ExperienceContent], ...],
    /,
) -> tuple[ArtifactRef, ...]:
    """Return the candidates whose stored cue matches one failure item exactly.

    Candidates arrive as pairs because ``ArtifactRef`` is a mutable-valued
    Pydantic model and therefore cannot key a mapping.
    """

    try:
        key = normalize_match_text(failure_text)
    except ValueError:
        return ()
    eligible: dict[tuple[str, str, int], ArtifactRef] = {}
    for ref, content in sorted(candidates, key=lambda pair: _ref_identity(pair[0])):
        if _stored_signature_key(content) == key:
            eligible.setdefault(_ref_identity(ref), ref)
    return tuple(eligible.values())


def match_result(count: int, /) -> MatchResult:
    """Return the deterministic verdict for one eligible-candidate count.

    Zero candidates is ``unmatched``, one is ``matched``, and two or more is
    ``ambiguous``: a generator may explain a result but never choose it.
    """

    if count <= 0:
        return "unmatched"
    if count == 1:
        return "matched"
    return "ambiguous"


def observation_id(**parts: object) -> str:
    """Derive the idempotency key of one ledger event from its positive evidence.

    The parts are the event type, its Task Outcome and immutable journal
    position, the Handoff and Receipt references, the exact artifact revision,
    the normalized signature key, the frozen match digest when applicable, and
    every applicable item locator including its digest. Replaying one Source
    window therefore reproduces the same key.
    """

    identity = RecurrenceObservationIdentity.model_validate(parts)
    return canonical_digest(identity)


def match_key(match: RecurrenceMatch, /) -> str:
    """Return the stable key of one immutable matching decision."""

    return canonical_digest(match)


def selection_key(observation: RecurrenceObservation, /) -> str:
    """Return the key that collapses duplicate ``selected`` rows for one Outcome.

    Only ``selected`` events use the linked-observation form. Every other event
    derives a non-colliding key so the uniqueness constraint cannot reject an
    ``avoided`` or ``recurred`` event that shares its Outcome with a ``selected``.
    """

    kind = "selection" if observation.event == "selected" else "unselected"
    return _scoped_key(kind, observation)


def verdict_key(observation: RecurrenceObservation, /) -> str:
    """Return the key that allows at most one terminal verdict per Outcome."""

    kind = "verdict" if observation.event != "selected" else "unverdicted"
    return _scoped_key(kind, observation)


def terminal_streak(verdicts: tuple[RecurrenceObservation, ...], /) -> int:
    """Return the terminal ``recurred`` streak since the last ``avoided``.

    Verdicts are ordered by the immutable ``task_outcome_position``, so replay,
    delayed processing, and wall-clock time cannot change a streak.
    """

    streak = 0
    for verdict in sorted(verdicts, key=lambda item: item.task_outcome_position):
        if verdict.event == "avoided":
            streak = 0
        elif verdict.event == "recurred":
            streak += 1
    return streak


def needing_review(verdicts: tuple[RecurrenceObservation, ...], /) -> bool:
    """Report whether one revision's streak has reached the Review threshold."""

    return terminal_streak(verdicts) >= RECURRENCE_REVIEW_STREAK_THRESHOLD


class TaskOutcomeItemRef(_ArtifactValue):
    """A ledger-internal locator into immutable Task Outcome content."""

    task_outcome_ref: SourceRef
    item_kind: TaskOutcomeItemKind
    item_index: Annotated[int, Field(ge=0)]
    item_digest: str

    @model_validator(mode="after")
    def validate_item_digest(self) -> TaskOutcomeItemRef:
        if _DIGEST_PATTERN.fullmatch(self.item_digest) is None:
            raise ValueError("item_digest must be a sha256 digest")  # noqa: TRY003
        return self


class RecurrenceMatch(_ArtifactValue):
    """One immutable, replayable matching decision."""

    scope_id: str
    task_outcome_ref: SourceRef
    task_outcome_position: Annotated[int, Field(ge=1)]
    failure_ref: TaskOutcomeItemRef
    candidate_set_mode: CandidateSetMode
    candidate_refs: tuple[ArtifactRef, ...]
    candidate_set_digest: str
    result: MatchResult
    artifact_ref: ArtifactRef | None = None
    signature_key: str | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> RecurrenceMatch:
        if self.failure_ref.task_outcome_ref != self.task_outcome_ref:
            raise ValueError("failure_ref must locate the matched Task Outcome")  # noqa: TRY003
        identities = tuple(_ref_identity(ref) for ref in self.candidate_refs)
        if identities != tuple(sorted(set(identities))):
            raise ValueError("candidate_refs must be sorted and unique")  # noqa: TRY003
        if self.candidate_set_digest != candidate_set_digest(self.candidate_refs):
            raise ValueError("candidate_set_digest must match the frozen candidate snapshot")  # noqa: TRY003
        if self.result != "matched":
            if self.artifact_ref is not None or self.signature_key is not None:
                raise ValueError("only a matched decision can identify a target")  # noqa: TRY003
            return self
        if self.artifact_ref is None or not self.signature_key:
            raise ValueError("a matched decision requires its exact target")  # noqa: TRY003
        if self.artifact_ref not in self.candidate_refs:
            raise ValueError("a matched target must come from the frozen candidate set")  # noqa: TRY003
        if normalize_match_text(self.signature_key) != self.signature_key:
            raise ValueError("signature_key must already be normalized")  # noqa: TRY003
        return self


class RecurrenceObservation(_ArtifactValue):
    """One append-only ledger event derived from positive evidence only.

    There is deliberately no ``unknown`` member: missing evidence leaves the
    ledger untouched, and ``unknown`` is a derived reading computed from the
    linked observations that did receive a terminal verdict.
    """

    observation_id: str
    scope_id: str
    artifact_ref: ArtifactRef
    signature_key: str
    event: RecurrenceEvent
    match_basis: MatchBasis = "exact"
    task_outcome_ref: SourceRef
    task_outcome_position: Annotated[int, Field(ge=1)]
    handoff_receipt_ref: SourceRef | None = None
    handoff_ref: ArtifactRef | None = None
    condition_ref: TaskOutcomeItemRef | None = None
    check_ref: TaskOutcomeItemRef | None = None
    failure_ref: TaskOutcomeItemRef | None = None
    recurrence_match_digest: str | None = None

    @model_validator(mode="after")
    def validate_event_evidence(self) -> RecurrenceObservation:
        if not _is_digest(self.observation_id):
            raise ValueError("observation_id must be a sha256 digest")  # noqa: TRY003
        if self.event == "selected":
            _require_selected(self)
            return self
        if self.event == "avoided":
            _require_avoided(self)
            return self
        _require_recurred(self)
        return self


class RecurrenceRevisionProposal(_ArtifactValue):
    """A recurrence-triggered Experience revision write ready for the Review Inbox."""

    target: ArtifactRef
    proposal: ExperienceContent
    sources: tuple[SourceRef, ...] = Field(min_length=1, max_length=MAX_EXPERIENCE_CANDIDATE_EVIDENCE)
    reason: str


class RecurrenceObservationIdentity(_ArtifactValue):
    """Every positive-evidence part that makes one ledger event idempotent."""

    event: RecurrenceEvent
    scope_id: str
    artifact_ref: ArtifactRef
    signature_key: str
    match_basis: MatchBasis = "exact"
    task_outcome_ref: SourceRef
    task_outcome_position: Annotated[int, Field(ge=1)]
    handoff_receipt_ref: SourceRef | None = None
    handoff_ref: ArtifactRef | None = None
    condition_ref: TaskOutcomeItemRef | None = None
    check_ref: TaskOutcomeItemRef | None = None
    failure_ref: TaskOutcomeItemRef | None = None
    recurrence_match_digest: str | None = None


def observation_identity(observation: RecurrenceObservation, /) -> RecurrenceObservationIdentity:
    """Return the idempotency parts of one ledger event."""

    return RecurrenceObservationIdentity(
        event=observation.event,
        scope_id=observation.scope_id,
        artifact_ref=observation.artifact_ref,
        signature_key=observation.signature_key,
        match_basis=observation.match_basis,
        task_outcome_ref=observation.task_outcome_ref,
        task_outcome_position=observation.task_outcome_position,
        handoff_receipt_ref=observation.handoff_receipt_ref,
        handoff_ref=observation.handoff_ref,
        condition_ref=observation.condition_ref,
        check_ref=observation.check_ref,
        failure_ref=observation.failure_ref,
        recurrence_match_digest=observation.recurrence_match_digest,
    )


def new_observation(**evidence: Any) -> RecurrenceObservation:
    """Build one ledger event whose id is derived from its own positive evidence."""

    return RecurrenceObservation(observation_id=observation_id(**evidence), **evidence)


def verified_observation_refs(
    outcome: TaskOutcome,
    task_outcome_ref: SourceRef,
    condition: str,
    /,
) -> tuple[TaskOutcomeItemRef, ...]:
    """Return every verified observation whose text equals one bound condition.

    More than one return value is ambiguity, never a choice: a generator may
    explain a result but must never select among equally plausible items.
    """

    key = _optional_key(condition)
    if key is None:
        return ()
    return tuple(
        TaskOutcomeItemRef(
            task_outcome_ref=task_outcome_ref,
            item_kind="observation",
            item_index=index,
            item_digest=item_digest(claim),
        )
        for index, claim in enumerate(outcome.observations)
        if claim.basis == "verified" and claim.evidence and _optional_key(claim.text) == key
    )


def verified_check_refs(
    outcome: TaskOutcome,
    task_outcome_ref: SourceRef,
    check_subject: str,
    /,
) -> tuple[TaskOutcomeItemRef, ...]:
    """Return every verified Task Check whose name equals one bound check subject."""

    key = _optional_key(check_subject)
    if key is None:
        return ()
    return tuple(
        TaskOutcomeItemRef(
            task_outcome_ref=task_outcome_ref,
            item_kind="check",
            item_index=index,
            item_digest=item_digest(check),
        )
        for index, check in enumerate(outcome.checks)
        if check.basis == "verified" and check.evidence and _optional_key(check.name) == key
    )


def avoided_refs(
    outcome: TaskOutcome,
    task_outcome_ref: SourceRef,
    content: ExperienceContent,
    /,
) -> tuple[TaskOutcomeItemRef, TaskOutcomeItemRef] | None:
    """Return the avoided evidence pair, or ``None`` when any gate stays unproven.

    ``None`` never becomes an ``avoided`` row: missing evidence is ``unknown``,
    and unknown is a derived reading rather than a ledger event.
    """

    failure = content.failure
    if failure is None:
        return None
    conditions = verified_observation_refs(outcome, task_outcome_ref, failure.verification.condition)
    checks = verified_check_refs(outcome, task_outcome_ref, failure.verification.check_subject)
    if len(conditions) != 1 or len(checks) != 1:
        return None
    check_ref = checks[0]
    if outcome.checks[check_ref.item_index].status != "passed":
        return None
    return conditions[0], check_ref


class _CandidateSetSnapshot(_ArtifactValue):
    """Canonical digest input for one ordered candidate snapshot."""

    refs: tuple[ArtifactRef, ...] = ()


class _RecurrenceKey(_ArtifactValue):
    """Canonical digest input for one derived ledger key."""

    kind: str
    scope_id: str
    family: str
    artifact_id: str
    revision: int
    signature_key: str
    task_outcome_source_type: str
    task_outcome_source_id: str
    task_outcome_position: int


def _scoped_key(kind: str, observation: RecurrenceObservation, /) -> str:
    return canonical_digest(
        _RecurrenceKey(
            kind=kind,
            scope_id=observation.scope_id,
            family=observation.artifact_ref.family,
            artifact_id=observation.artifact_ref.artifact_id,
            revision=observation.artifact_ref.revision,
            signature_key=observation.signature_key,
            task_outcome_source_type=observation.task_outcome_ref.source_type,
            task_outcome_source_id=observation.task_outcome_ref.source_id,
            task_outcome_position=observation.task_outcome_position,
        )
    )


def _refs_for_kind(
    outcome: TaskOutcome,
    task_outcome_ref: SourceRef,
    kind: TaskOutcomeItemKind,
    /,
) -> tuple[TaskOutcomeItemRef, ...]:
    items: tuple[WorkClaim | TaskCheck, ...] = outcome.checks if kind == "check" else outcome.observations
    return tuple(
        TaskOutcomeItemRef(
            task_outcome_ref=task_outcome_ref,
            item_kind=kind,
            item_index=index,
            item_digest=item_digest(item),
        )
        for index, item in enumerate(items)
        if is_failure_evidence(outcome, kind, index)
    )


def _optional_key(value: str, /) -> str | None:
    try:
        return normalize_match_text(value)
    except ValueError:
        return None


def _stored_signature_key(content: ExperienceContent) -> str | None:
    if content.failure is None:
        return None
    return signature_key(content.failure.signature.recall_cue)


def _ref_identity(ref: ArtifactRef) -> tuple[str, str, int]:
    return ref.family, ref.artifact_id, ref.revision


def _require_selected(observation: RecurrenceObservation) -> None:
    _require(observation.handoff_receipt_ref is not None, "selected requires a Handoff receipt reference")
    _require(observation.handoff_ref is not None, "selected requires the referencing Handoff revision")
    _require(observation.condition_ref is None, "selected cannot carry a condition locator")
    _require(observation.check_ref is None, "selected cannot carry a check locator")
    _require(observation.failure_ref is None, "selected cannot carry a failure locator")
    _require(observation.recurrence_match_digest is None, "selected cannot carry a match digest")


def _require_avoided(observation: RecurrenceObservation) -> None:
    _require(observation.handoff_receipt_ref is not None, "avoided requires a Handoff receipt reference")
    _require(observation.handoff_ref is not None, "avoided requires the referencing Handoff revision")
    _require(observation.condition_ref is not None, "avoided requires a condition locator")
    _require(observation.check_ref is not None, "avoided requires a check locator")
    _require(observation.failure_ref is None, "avoided cannot carry a failure locator")
    _require(observation.recurrence_match_digest is None, "avoided cannot carry a match digest")
    if observation.condition_ref is not None:
        _require(
            observation.condition_ref.item_kind == "observation",
            "avoided condition_ref must locate an observation",
        )
        _require_same_outcome(observation, observation.condition_ref)
    if observation.check_ref is not None:
        _require(observation.check_ref.item_kind == "check", "avoided check_ref must locate a check")
        _require_same_outcome(observation, observation.check_ref)


def _require_recurred(observation: RecurrenceObservation) -> None:
    _require(observation.handoff_receipt_ref is None, "recurred cannot carry a Handoff receipt reference")
    _require(observation.handoff_ref is None, "recurred cannot carry a Handoff revision")
    _require(observation.condition_ref is None, "recurred cannot carry a condition locator")
    _require(observation.check_ref is None, "recurred cannot carry a check locator")
    _require(observation.failure_ref is not None, "recurred requires a failure locator")
    _require(
        observation.recurrence_match_digest is not None,
        "recurred requires the digest of one frozen matching decision",
    )
    if observation.failure_ref is not None:
        _require_same_outcome(observation, observation.failure_ref)
    if observation.recurrence_match_digest is not None and not _is_digest(observation.recurrence_match_digest):
        raise ValueError("recurrence_match_digest must be a sha256 digest")  # noqa: TRY003


def _require_same_outcome(observation: RecurrenceObservation, ref: TaskOutcomeItemRef) -> None:
    if ref.task_outcome_ref != observation.task_outcome_ref:
        raise ValueError("every item locator must resolve inside the event's Task Outcome")  # noqa: TRY003


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _strip_edges(value: str) -> str:
    start, end = 0, len(value)
    while start < end and _is_edge(value[start]):
        start += 1
    while end > start and _is_edge(value[end - 1]):
        end -= 1
    return value[start:end]


def _is_edge(character: str) -> bool:
    return unicodedata.category(character)[0] in {"C", "P", "Z"}


def _bigrams(value: str) -> frozenset[tuple[str, ...]]:
    tokens = tuple(unicodedata.normalize("NFKC", value).casefold().split())
    if not tokens:
        return frozenset()
    if len(tokens) == 1:
        return frozenset({tokens})
    return frozenset(tokens[index : index + 2] for index in range(len(tokens) - 1))


__all__ = [
    "MAX_RECURRENCE_HANDOFF_SCAN",
    "NEAR_DUPLICATE_BIGRAM_OVERLAP",
    "RECURRENCE_REVIEW_STREAK_THRESHOLD",
    "RecurrenceEvent",
    "RecurrenceMatch",
    "RecurrenceObservation",
    "RecurrenceObservationIdentity",
    "RecurrenceRevisionProposal",
    "TaskOutcomeItemRef",
    "avoided_refs",
    "candidate_set_digest",
    "canonical_digest",
    "eligible_candidates",
    "failure_item_text",
    "failure_refs",
    "freeze_candidate_set",
    "is_failure_evidence",
    "item_digest",
    "match_key",
    "match_result",
    "near_duplicate_overlap",
    "needing_review",
    "new_observation",
    "normalize_match_text",
    "observation_id",
    "observation_identity",
    "selection_key",
    "signature_key",
    "terminal_streak",
    "verdict_key",
    "verified_check_refs",
    "verified_observation_refs",
]
