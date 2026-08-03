"""Product orchestration for temporary and committed Handoffs."""

from __future__ import annotations

from collections.abc import Iterable

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.handoff.errors import (
    HandoffEvidenceUnavailableError,
    HandoffGenerationUnavailableError,
    HandoffScopeMismatchError,
    InvalidHandoffGenerationError,
    InvalidHandoffReferenceError,
)
from powercontext.builtin.artifacts.handoff.models import (
    Handoff,
    HandoffArtifactCitation,
    HandoffArtifactDraft,
    HandoffAudience,
    HandoffCitation,
    HandoffClaim,
    HandoffContent,
    HandoffDraft,
    HandoffEvidenceCheck,
    HandoffGenerationRequest,
    HandoffMemoryCitation,
    HandoffResolution,
    HandoffResolutionSelection,
    HandoffSourceCitation,
    PreparedHandoff,
    PrepareHandoff,
)
from powercontext.builtin.artifacts.handoff.protocols import (
    HandoffBackend,
    HandoffEvidenceResolver,
    HandoffGenerationPipeline,
)
from powercontext.errors import RevisionConflictError
from powercontext.sources import SourceRef


class HandoffService:
    """Generate, finalize, commit, inspect, and resolve one scope's Handoff lifecycle."""

    def __init__(
        self,
        *,
        scope_id: str,
        artifact_id: str,
        backend: HandoffBackend,
        evidence_resolver: HandoffEvidenceResolver,
        generation_pipeline: HandoffGenerationPipeline | None = None,
    ) -> None:
        self.scope_id = scope_id
        self.artifact_id = artifact_id
        self._backend = backend
        self._evidence_resolver = evidence_resolver
        self._generation_pipeline = generation_pipeline
        ArtifactRef(family=Handoff.family, artifact_id=artifact_id, revision=1)

    async def prepare(self, action: PrepareHandoff, /) -> HandoffDraft:
        """Generate an inspectable Draft from one standard bounded action."""

        if self._generation_pipeline is None:
            raise HandoffGenerationUnavailableError
        evidence = tuple([await self._evidence_resolver.resolve(citation) for citation in action.evidence])
        draft = await self._generation_pipeline.generate(
            HandoffGenerationRequest(
                objective=action.objective,
                evidence=evidence,
                max_bytes=action.max_bytes,
            )
        )
        self._validate_generated_draft(action, draft)
        return draft

    async def finalize(self, draft: HandoffDraft, /) -> PreparedHandoff:
        """Finalize inspected content after validating its direct evidence."""

        content = draft.as_content()
        await self._validate_evidence(content)
        current = await self._backend.latest(self.artifact_id)
        return PreparedHandoff(
            scope_id=self.scope_id,
            base=None if current is None else current.as_ref(),
            content=content,
        )

    async def commit(self, prepared: PreparedHandoff, /) -> Handoff:
        """Commit an explicit milestone with no-op and optimistic concurrency semantics."""

        self._require_prepared(prepared)
        current = await self._backend.latest(self.artifact_id)
        if current is not None and current.content == prepared.content:
            return current

        self._require_current_base(prepared.base, current)
        await self._validate_evidence(prepared.content)
        draft = HandoffArtifactDraft(
            content=prepared.content,
            sources=_source_lineage(prepared.content),
            artifacts=_artifact_lineage(prepared.content),
        )
        if current is None:
            return await self._backend.create(self.artifact_id, draft)
        return await self._backend.revise(current, draft)

    async def latest(self) -> Handoff | None:
        """Return the current committed milestone."""

        return await self._backend.latest(self.artifact_id)

    async def revision(self, reference: ArtifactRef, /) -> Handoff:
        """Return one exact committed milestone."""

        self._require_reference(reference)
        return await self._backend.get(reference)

    async def revisions(self) -> tuple[Handoff, ...]:
        """Return committed milestones in ascending Revision order."""

        return await self._backend.revisions(self.artifact_id)

    async def continue_from(
        self,
        handoff: PreparedHandoff | ArtifactRef,
        /,
    ) -> HandoffResolution:
        """Resolve Handoff content without treating historical claims as current truth."""

        current = await self._backend.latest(self.artifact_id)
        if isinstance(handoff, PreparedHandoff):
            self._require_prepared(handoff)
            content = handoff.content
            selection: HandoffResolutionSelection = "prepared"
            selected_revision = None
        else:
            selected = await self.revision(handoff)
            content = selected.content
            selection = "exact"
            selected_revision = selected.as_ref()
        return await self._resolve(
            content,
            selection=selection,
            selected_revision=selected_revision,
            current=current,
        )

    async def continue_latest(self) -> HandoffResolution:
        """Resolve the latest milestone after the caller selects the current workstream."""

        current = await self._backend.latest(self.artifact_id)
        if current is None:
            return HandoffResolution(
                status="empty",
                scope_id=self.scope_id,
                content=None,
            )
        return await self._resolve(
            current.content,
            selection="latest",
            selected_revision=current.as_ref(),
            current=current,
        )

    async def _resolve(
        self,
        content: HandoffContent,
        *,
        selection: HandoffResolutionSelection,
        selected_revision: ArtifactRef | None,
        current: Handoff | None,
    ) -> HandoffResolution:
        return HandoffResolution(
            status="resolved",
            scope_id=self.scope_id,
            content=content,
            selection=selection,
            selected_revision=selected_revision,
            current_revision=None if current is None else current.as_ref(),
            evidence_checks=await self._evidence_checks(content),
        )

    @staticmethod
    def render(
        handoff: HandoffDraft | PreparedHandoff | Handoff,
        /,
        *,
        audience: HandoffAudience,
    ) -> str:
        """Render one lossless document for either supported audience."""

        if audience not in {"human", "agent"}:
            raise ValueError(f"unsupported Handoff audience: {audience}")  # noqa: TRY003
        content = handoff.as_content() if isinstance(handoff, HandoffDraft) else handoff.content
        return content.model_dump_json(by_alias=True, indent=2)

    def _require_prepared(self, prepared: PreparedHandoff) -> None:
        if prepared.scope_id != self.scope_id:
            raise HandoffScopeMismatchError(self.scope_id, prepared.scope_id)
        if prepared.base is not None:
            self._require_reference(prepared.base)

    def _require_reference(self, reference: ArtifactRef) -> None:
        if reference.family != Handoff.family or reference.artifact_id != self.artifact_id:
            raise InvalidHandoffReferenceError(reference)

    @staticmethod
    def _require_current_base(base: ArtifactRef | None, current: Handoff | None) -> None:
        current_ref = None if current is None else current.as_ref()
        if base != current_ref:
            raise RevisionConflictError(base, current)

    async def _validate_evidence(self, content: HandoffContent) -> None:
        for citation in _direct_citations(content):
            await self._evidence_resolver.validate(citation)

    @staticmethod
    def _validate_generated_draft(action: PrepareHandoff, draft: object) -> None:
        if not isinstance(draft, HandoffDraft):
            raise InvalidHandoffGenerationError("output")
        if draft.objective != action.objective:
            raise InvalidHandoffGenerationError("objective")
        allowed_evidence = action.evidence
        if any(citation not in allowed_evidence for citation in _all_citations(draft.as_content())):
            raise InvalidHandoffGenerationError("evidence")
        content_bytes = len(draft.as_content().model_dump_json(by_alias=True, indent=2).encode())
        if content_bytes > action.max_bytes:
            raise InvalidHandoffGenerationError("budget")

    async def _evidence_checks(self, content: HandoffContent) -> tuple[HandoffEvidenceCheck, ...]:
        checks = [
            await self._check_evidence(
                statement.citations,
                claim="state",
                state_index=index,
            )
            for index, statement in enumerate(content.state)
        ]
        if content.next_action is not None:
            checks.append(
                await self._check_evidence(
                    content.next_action.citations,
                    claim="next_action",
                )
            )
        return tuple(checks)

    async def _check_evidence(
        self,
        citations: tuple[HandoffCitation, ...],
        *,
        claim: HandoffClaim,
        state_index: int | None = None,
    ) -> HandoffEvidenceCheck:
        unavailable: list[HandoffCitation] = []
        for citation in citations:
            try:
                await self._evidence_resolver.validate(citation)
            except HandoffEvidenceUnavailableError:
                if citation not in unavailable:
                    unavailable.append(citation)
        return HandoffEvidenceCheck(
            claim=claim,
            state_index=state_index,
            status="unavailable" if unavailable else "available",
            unavailable_evidence=tuple(unavailable),
        )


def _direct_citations(content: HandoffContent) -> Iterable[HandoffCitation]:
    for statement in content.state:
        yield from statement.citations
    if content.next_action is not None:
        yield from content.next_action.citations


def _all_citations(content: HandoffContent) -> Iterable[HandoffCitation]:
    yield from _direct_citations(content)
    for omission in content.omissions:
        if omission.citation is not None:
            yield omission.citation


def _source_lineage(content: HandoffContent) -> tuple[SourceRef, ...]:
    sources: list[SourceRef] = []
    for citation in _direct_citations(content):
        if isinstance(citation, HandoffSourceCitation) and citation.source_ref not in sources:
            sources.append(citation.source_ref)
    return tuple(sources)


def _artifact_lineage(content: HandoffContent) -> tuple[ArtifactRef, ...]:
    artifacts: list[ArtifactRef] = []
    for citation in _direct_citations(content):
        reference = (
            citation.artifact_ref
            if isinstance(citation, HandoffArtifactCitation)
            else citation.memory_citation.memory_ref
            if isinstance(citation, HandoffMemoryCitation)
            else None
        )
        if reference is not None and reference not in artifacts:
            artifacts.append(reference)
    return tuple(artifacts)
