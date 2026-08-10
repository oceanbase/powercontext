"""Immutable public values for the Handoff Artifact Family."""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, InstanceOf, field_validator, model_validator

from powercontext.artifacts import Artifact, ArtifactDraft, ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryCitation, MemoryEntryVersion
from powercontext.sources import Source, SourceRef

DEFAULT_HANDOFF_MAX_BYTES = 8000
MAX_HANDOFF_BYTES = 32_768
MIN_HANDOFF_MAX_BYTES = 512
MAX_HANDOFF_CITATIONS = 32
MAX_HANDOFF_OMISSIONS = 64
MAX_HANDOFF_STATE_STATEMENTS = 64
MAX_HANDOFF_TEXT_LENGTH = 8192

HandoffAudience: TypeAlias = Literal["human", "agent"]
HandoffClaim: TypeAlias = Literal["state", "next_action"]
HandoffDisposition: TypeAlias = Literal["continuable", "blocked", "complete"]
HandoffEvidenceStatus: TypeAlias = Literal["available", "unavailable"]
HandoffResolutionSelection: TypeAlias = Literal["prepared", "exact", "latest"]
HandoffResolutionStatus: TypeAlias = Literal["empty", "resolved"]
HandoffActivationStatus: TypeAlias = Literal["generated", "ignored"]


class _HandoffValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class HandoffSourceCitation(_HandoffValue):
    """Cite one Source in the originating scope."""

    kind: Literal["source"] = "source"
    source_ref: SourceRef


class HandoffArtifactCitation(_HandoffValue):
    """Cite one exact Artifact Revision in the originating scope."""

    kind: Literal["artifact"] = "artifact"
    artifact_ref: ArtifactRef


class HandoffMemoryCitation(_HandoffValue):
    """Cite one exact Memory entry version."""

    kind: Literal["memory"] = "memory"
    memory_citation: MemoryCitation


HandoffCitation: TypeAlias = Annotated[
    HandoffSourceCitation | HandoffArtifactCitation | HandoffMemoryCitation,
    Field(discriminator="kind"),
]


class PrepareHandoff(_HandoffValue):
    """Standard action for generating one bounded, inspectable Handoff."""

    objective: Annotated[str, Field(max_length=MAX_HANDOFF_TEXT_LENGTH)]
    evidence: Annotated[
        tuple[HandoffCitation, ...],
        Field(min_length=1, max_length=MAX_HANDOFF_CITATIONS),
    ]
    max_bytes: Annotated[int, Field(ge=MIN_HANDOFF_MAX_BYTES, le=MAX_HANDOFF_BYTES)] = DEFAULT_HANDOFF_MAX_BYTES

    @field_validator("objective")
    @classmethod
    def require_objective(cls, value: str) -> str:
        return _require_text("objective", value)

    @model_validator(mode="after")
    def require_unique_evidence(self) -> PrepareHandoff:
        for index, citation in enumerate(self.evidence):
            if citation in self.evidence[:index]:
                raise ValueError("Handoff preparation evidence must be unique")  # noqa: TRY003
        return self


class ActivateHandoff(_HandoffValue):
    """Request Handoff generation at one provider-observed Source boundary."""

    boundary_source: SourceRef
    objective: Annotated[str, Field(max_length=MAX_HANDOFF_TEXT_LENGTH)]
    evidence: Annotated[
        tuple[HandoffCitation, ...],
        Field(max_length=MAX_HANDOFF_CITATIONS),
    ] = ()
    max_bytes: Annotated[int, Field(ge=MIN_HANDOFF_MAX_BYTES, le=MAX_HANDOFF_BYTES)] = DEFAULT_HANDOFF_MAX_BYTES

    @field_validator("objective")
    @classmethod
    def require_objective(cls, value: str) -> str:
        return _require_text("objective", value)

    @model_validator(mode="after")
    def require_bounded_unique_evidence(self) -> ActivateHandoff:
        citations = self.action_evidence()
        if len(citations) > MAX_HANDOFF_CITATIONS:
            raise ValueError("Handoff activation evidence exceeds the citation limit")  # noqa: TRY003
        for index, citation in enumerate(citations):
            if citation in citations[:index]:
                raise ValueError("Handoff activation evidence must be unique")  # noqa: TRY003
        return self

    def action_evidence(self) -> tuple[HandoffCitation, ...]:
        """Include the boundary Source once as direct generation evidence."""

        boundary = HandoffSourceCitation(source_ref=self.boundary_source)
        return (boundary, *(citation for citation in self.evidence if citation != boundary))


class HandoffSourceEvidence(_HandoffValue):
    """One exact Source resolved for Handoff generation."""

    citation: HandoffSourceCitation
    source: InstanceOf[Source]


class HandoffArtifactEvidence(_HandoffValue):
    """One exact Artifact Revision resolved for Handoff generation."""

    citation: HandoffArtifactCitation
    artifact: InstanceOf[Artifact[object]]

    @model_validator(mode="after")
    def require_exact_revision(self) -> HandoffArtifactEvidence:
        if self.citation.artifact_ref != self.artifact.as_ref():
            raise ValueError("resolved Artifact does not match its Handoff citation")  # noqa: TRY003
        return self


class HandoffMemoryEvidence(_HandoffValue):
    """One exact Memory entry version resolved for Handoff generation."""

    citation: HandoffMemoryCitation
    entry: MemoryEntryVersion

    @model_validator(mode="after")
    def require_exact_entry_version(self) -> HandoffMemoryEvidence:
        reference = self.citation.memory_citation
        if (
            reference.memory_ref.artifact_id != self.entry.memory_artifact_id
            or reference.entry_id != self.entry.entry_id
            or reference.entry_version_id != self.entry.entry_version_id
        ):
            raise ValueError("resolved Memory entry does not match its Handoff citation")  # noqa: TRY003
        return self


HandoffGenerationEvidence: TypeAlias = HandoffSourceEvidence | HandoffArtifactEvidence | HandoffMemoryEvidence


class HandoffGenerationRequest(_HandoffValue):
    """Canonical bounded evidence offered to a Handoff generation pipeline."""

    objective: Annotated[str, Field(max_length=MAX_HANDOFF_TEXT_LENGTH)]
    evidence: Annotated[
        tuple[HandoffGenerationEvidence, ...],
        Field(min_length=1, max_length=MAX_HANDOFF_CITATIONS),
    ]
    max_bytes: Annotated[int, Field(ge=MIN_HANDOFF_MAX_BYTES, le=MAX_HANDOFF_BYTES)]

    @field_validator("objective")
    @classmethod
    def require_objective(cls, value: str) -> str:
        return _require_text("objective", value)


class HandoffStatement(_HandoffValue):
    """One current claim and the evidence that supports it."""

    text: Annotated[str, Field(max_length=MAX_HANDOFF_TEXT_LENGTH)]
    citations: Annotated[
        tuple[HandoffCitation, ...],
        Field(min_length=1, max_length=MAX_HANDOFF_CITATIONS),
    ]

    @field_validator("text")
    @classmethod
    def require_text(cls, value: str) -> str:
        return _require_text("statement", value)


class HandoffOmission(_HandoffValue):
    """Known relevant material that was unavailable or not verified."""

    text: Annotated[str, Field(max_length=MAX_HANDOFF_TEXT_LENGTH)]
    citation: HandoffCitation | None = None

    @field_validator("text")
    @classmethod
    def require_text(cls, value: str) -> str:
        return _require_text("omission", value)


class HandoffContent(_HandoffValue):
    """The complete content shared by temporary and committed Handoffs."""

    schema_version: Literal["powercontext.handoff.v1"] = Field(
        default="powercontext.handoff.v1",
        alias="schema",
    )
    objective: Annotated[str, Field(max_length=MAX_HANDOFF_TEXT_LENGTH)]
    state: Annotated[
        tuple[HandoffStatement, ...],
        Field(min_length=1, max_length=MAX_HANDOFF_STATE_STATEMENTS),
    ]
    disposition: HandoffDisposition
    next_action: HandoffStatement | None = None
    omissions: Annotated[
        tuple[HandoffOmission, ...],
        Field(max_length=MAX_HANDOFF_OMISSIONS),
    ] = ()

    @field_validator("objective")
    @classmethod
    def require_objective(cls, value: str) -> str:
        return _require_text("objective", value)


class HandoffDraft(_HandoffValue):
    """Inspectable and correctable content before Handoff finalization."""

    objective: Annotated[str, Field(max_length=MAX_HANDOFF_TEXT_LENGTH)]
    state: Annotated[
        tuple[HandoffStatement, ...],
        Field(min_length=1, max_length=MAX_HANDOFF_STATE_STATEMENTS),
    ]
    disposition: HandoffDisposition
    next_action: HandoffStatement | None = None
    omissions: Annotated[
        tuple[HandoffOmission, ...],
        Field(max_length=MAX_HANDOFF_OMISSIONS),
    ] = ()

    @field_validator("objective")
    @classmethod
    def require_objective(cls, value: str) -> str:
        return _require_text("objective", value)

    def as_content(self) -> HandoffContent:
        """Build versioned content without rewriting the inspected draft."""

        return HandoffContent(
            objective=self.objective,
            state=self.state,
            disposition=self.disposition,
            next_action=self.next_action,
            omissions=self.omissions,
        )


class HandoffActivation(_HandoffValue):
    """Result of evaluating and executing one Handoff boundary signal."""

    status: HandoffActivationStatus
    boundary_source: SourceRef
    previous_position: Annotated[int, Field(ge=0)]
    current_position: Annotated[int, Field(ge=0)]
    draft: HandoffDraft | None

    @model_validator(mode="after")
    def validate_result(self) -> HandoffActivation:
        if self.current_position < self.previous_position:
            raise ValueError("Handoff activation position cannot move backwards")  # noqa: TRY003
        if self.status == "generated":
            if self.draft is None or self.current_position <= self.previous_position:
                raise ValueError("generated Handoff activation must advance with a Draft")  # noqa: TRY003
            return self
        if self.draft is not None or self.current_position != self.previous_position:
            raise ValueError("ignored Handoff activation cannot change state or contain a Draft")  # noqa: TRY003
        return self


class Handoff(Artifact[HandoffContent]):
    """An immutable committed Handoff milestone."""

    family: ClassVar[str] = "handoff"
    model_config = ConfigDict(frozen=True)


class HandoffArtifactDraft(ArtifactDraft[HandoffContent]):
    """Complete content and direct evidence for one Handoff commit."""

    family: ClassVar[str] = "handoff"
    model_config = ConfigDict(frozen=True)


class PreparedHandoff(_HandoffValue):
    """Finalized temporary Handoff associated with one scope and observed head."""

    schema_version: Literal["powercontext.prepared-handoff.v1"] = Field(
        default="powercontext.prepared-handoff.v1",
        alias="schema",
    )
    scope_id: str
    base: ArtifactRef | None
    content: HandoffContent

    @field_validator("scope_id")
    @classmethod
    def require_scope_id(cls, value: str) -> str:
        return _require_text("scope_id", value)


class HandoffEvidenceCheck(_HandoffValue):
    """Reference availability for one state statement or next action."""

    claim: HandoffClaim
    state_index: Annotated[int, Field(ge=0)] | None = None
    status: HandoffEvidenceStatus
    unavailable_evidence: Annotated[
        tuple[HandoffCitation, ...],
        Field(max_length=MAX_HANDOFF_CITATIONS),
    ] = ()

    @model_validator(mode="after")
    def validate_claim(self) -> HandoffEvidenceCheck:
        if self.claim == "state" and self.state_index is None:
            raise ValueError("state evidence check requires a state index")  # noqa: TRY003
        if self.claim == "next_action" and self.state_index is not None:
            raise ValueError("next-action evidence check cannot contain a state index")  # noqa: TRY003
        if self.status == "available" and self.unavailable_evidence:
            raise ValueError("available evidence check cannot identify unavailable evidence")  # noqa: TRY003
        if self.status == "unavailable" and not self.unavailable_evidence:
            raise ValueError("unavailable evidence check must identify unavailable evidence")  # noqa: TRY003
        return self


class HandoffResolution(_HandoffValue):
    """Untrusted Handoff content and reference checks resolved for Continue."""

    trust: Literal["untrusted_history"] = "untrusted_history"
    status: HandoffResolutionStatus
    scope_id: str
    content: HandoffContent | None
    selection: HandoffResolutionSelection | None = None
    selected_revision: ArtifactRef | None = None
    current_revision: ArtifactRef | None = None
    evidence_checks: Annotated[
        tuple[HandoffEvidenceCheck, ...],
        Field(max_length=MAX_HANDOFF_STATE_STATEMENTS + 1),
    ] = ()

    @model_validator(mode="after")
    def validate_resolution(self) -> HandoffResolution:
        if self.status == "empty":
            self._validate_empty()
            return self

        if self.content is None or self.selection is None:
            raise ValueError("resolved Handoff must contain content and selection")  # noqa: TRY003
        self._validate_selection()
        self._validate_evidence_checks(self.content)
        return self

    def _validate_empty(self) -> None:
        values = (
            self.content,
            self.selection,
            self.selected_revision,
            self.current_revision,
        )
        if any(value is not None for value in values) or self.evidence_checks:
            raise ValueError("empty resolution cannot contain Handoff state")  # noqa: TRY003

    def _validate_selection(self) -> None:
        if self.selection == "prepared" and self.selected_revision is not None:
            raise ValueError("prepared selection cannot identify a committed Revision")  # noqa: TRY003
        if self.selection != "prepared" and self.selected_revision is None:
            raise ValueError("committed selection must identify its exact Revision")  # noqa: TRY003
        if self.selection != "prepared" and self.current_revision is None:
            raise ValueError("committed selection must identify the current Revision")  # noqa: TRY003
        if self.selection == "latest" and self.selected_revision != self.current_revision:
            raise ValueError("latest selection must select the current Revision")  # noqa: TRY003
        if (
            self.selected_revision is not None
            and self.current_revision is not None
            and (
                self.selected_revision.family,
                self.selected_revision.artifact_id,
            )
            != (
                self.current_revision.family,
                self.current_revision.artifact_id,
            )
        ):
            raise ValueError("selected and current Revisions must share one Artifact identity")  # noqa: TRY003

    def _validate_evidence_checks(self, content: HandoffContent) -> None:
        expected_claims = tuple(
            [("state", index) for index in range(len(content.state))]
            + ([("next_action", None)] if content.next_action is not None else [])
        )
        actual_claims = tuple((check.claim, check.state_index) for check in self.evidence_checks)
        if actual_claims != expected_claims:
            raise ValueError("evidence checks must match Handoff statements in order")  # noqa: TRY003
        statements = content.state + ((content.next_action,) if content.next_action is not None else ())
        for statement, check in zip(statements, self.evidence_checks, strict=True):
            if any(citation not in statement.citations for citation in check.unavailable_evidence):
                raise ValueError("unavailable evidence must belong to the checked statement")  # noqa: TRY003


def _require_text(field: str, value: str) -> str:
    if not value.strip():
        raise ValueError(f"{field} must contain non-whitespace content")  # noqa: TRY003
    return value
