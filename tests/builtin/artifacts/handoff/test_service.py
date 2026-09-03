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

from powercontext.artifacts import ArtifactLineage, ArtifactRef
from powercontext.builtin.artifacts.handoff import (
    Handoff,
    HandoffArtifactDraft,
    HandoffBackend,
    HandoffDisposition,
    HandoffDraft,
    HandoffEvidenceUnavailableError,
    HandoffGenerationUnavailableError,
    HandoffOmission,
    HandoffService,
    HandoffSourceCitation,
    HandoffSourceEvidence,
    HandoffStatement,
    InvalidHandoffGenerationError,
    PreparedHandoff,
    PrepareHandoff,
)
from powercontext.errors import RevisionConflictError
from powercontext.sources import Source, SourceMaterialization, SourceRef

SOURCE_REF = SourceRef(source_type="content", source_id="turn-1")
MISSING_REF = SourceRef(source_type="content", source_id="missing")


class _EvidenceResolver:
    def __init__(self) -> None:
        self.unavailable: set[tuple[str, str]] = set()

    async def validate(self, citation, /) -> None:
        if isinstance(citation, HandoffSourceCitation):
            key = (citation.source_ref.source_type, citation.source_ref.source_id)
            if key in self.unavailable:
                raise HandoffEvidenceUnavailableError(citation)

    async def resolve(self, citation, /):
        await self.validate(citation)
        if isinstance(citation, HandoffSourceCitation):
            return HandoffSourceEvidence(
                citation=citation,
                source=Source(
                    name=citation.source_ref.source_id,
                    materialization=SourceMaterialization.CAPTURED,
                ),
            )
        raise TypeError(type(citation).__name__)


class _GenerationPipeline:
    def __init__(self, draft: object) -> None:
        self.draft = draft

    async def generate(self, request, /):
        return self.draft


class _HandoffBackend(HandoffBackend):
    def __init__(self) -> None:
        self.history: list[Handoff] = []
        self.published: dict[tuple[str, int], Handoff] = {}

    async def create(self, artifact_id: str, draft: HandoffArtifactDraft, /) -> Handoff:
        if self.history:
            raise RevisionConflictError(draft, self.history[-1])
        return self._append(artifact_id, draft)

    async def revise(self, base: Handoff, draft: HandoffArtifactDraft, /) -> Handoff:
        if not self.history or self.history[-1] != base:
            raise RevisionConflictError(base, None if not self.history else self.history[-1])
        return self._append(base.artifact_id, draft)

    async def get(self, reference: ArtifactRef, /) -> Handoff:
        if reference.artifact_id != "handoff":
            return self.published[(reference.artifact_id, reference.revision)]
        return self.history[reference.revision - 1]

    async def latest(self, artifact_id: str, /) -> Handoff | None:
        published = [handoff for (current_id, _), handoff in self.published.items() if current_id == artifact_id]
        if published:
            return max(published, key=lambda handoff: handoff.revision)
        if not self.history:
            return None
        assert self.history[-1].artifact_id == artifact_id
        return self.history[-1]

    async def revisions(self, artifact_id: str, /) -> tuple[Handoff, ...]:
        assert not self.history or self.history[-1].artifact_id == artifact_id
        return tuple(self.history)

    def _append(self, artifact_id: str, draft: HandoffArtifactDraft) -> Handoff:
        handoff = Handoff(
            artifact_id=artifact_id,
            revision=len(self.history) + 1,
            content=draft.content,
            lineage=ArtifactLineage(sources=draft.sources, artifacts=draft.artifacts),
        )
        self.history.append(handoff)
        return handoff


def _citation(source_ref: SourceRef = SOURCE_REF) -> HandoffSourceCitation:
    return HandoffSourceCitation(source_ref=source_ref)


def _draft(
    state: str,
    *,
    disposition: HandoffDisposition = "continuable",
    next_action: str | None = "Run tests.",
    omissions: tuple[HandoffOmission, ...] = (),
) -> HandoffDraft:
    return HandoffDraft(
        objective="Complete parser error handling.",
        state=(HandoffStatement(text=state, citations=(_citation(),)),),
        disposition=disposition,
        next_action=(None if next_action is None else HandoffStatement(text=next_action, citations=(_citation(),))),
        omissions=omissions,
    )


def _service(
    generation_pipeline=None,
) -> tuple[HandoffService, _HandoffBackend, _EvidenceResolver]:
    backend = _HandoffBackend()
    resolver = _EvidenceResolver()
    return (
        HandoffService(
            scope_id="project",
            artifact_id="handoff",
            backend=backend,
            evidence_resolver=resolver,
            generation_pipeline=generation_pipeline,
        ),
        backend,
        resolver,
    )


def test_prepare_generates_a_draft_from_exact_bounded_evidence() -> None:
    async def scenario() -> None:
        generated = _draft("Error mapping changed.")
        pipeline = _GenerationPipeline(generated)
        service, _, _ = _service(pipeline)
        action = PrepareHandoff(
            objective=generated.objective,
            evidence=(_citation(),),
            max_bytes=4096,
        )

        draft = await service.prepare(action)

        assert draft == generated
        assert await service.latest() is None

    asyncio.run(scenario())


def test_prepare_requires_a_configured_generation_pipeline() -> None:
    async def scenario() -> None:
        service, _, _ = _service()

        with pytest.raises(HandoffGenerationUnavailableError):
            await service.prepare(
                PrepareHandoff(
                    objective="Complete parser error handling.",
                    evidence=(_citation(),),
                )
            )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("draft", "max_bytes", "code"),
    [
        (
            HandoffDraft(
                objective="Changed objective.",
                state=(HandoffStatement(text="Current state.", citations=(_citation(),)),),
                disposition="continuable",
            ),
            4096,
            "objective",
        ),
        (
            HandoffDraft(
                objective="Complete parser error handling.",
                state=(HandoffStatement(text="Unsupported state.", citations=(_citation(MISSING_REF),)),),
                disposition="continuable",
            ),
            4096,
            "evidence",
        ),
        (_draft("x" * 1000), 512, "budget"),
    ],
)
def test_prepare_rejects_pipeline_contract_violations(
    draft: HandoffDraft,
    max_bytes: int,
    code: str,
) -> None:
    async def scenario() -> None:
        service, _, _ = _service(_GenerationPipeline(draft))

        with pytest.raises(InvalidHandoffGenerationError) as caught:
            await service.prepare(
                PrepareHandoff(
                    objective="Complete parser error handling.",
                    evidence=(_citation(),),
                    max_bytes=max_bytes,
                )
            )

        assert caught.value.code == code

    asyncio.run(scenario())


def test_draft_can_be_corrected_before_temporary_handoff_is_finalized() -> None:
    async def scenario() -> None:
        service, _, _ = _service()
        draft = _draft("Error mapping needs review.")
        corrected = draft.model_copy(
            update={
                "state": (
                    HandoffStatement(
                        text="Error mapping changed.",
                        citations=(_citation(),),
                    ),
                ),
            }
        )

        prepared = await service.finalize(corrected)

        assert prepared.scope_id == "project"
        assert prepared.base is None
        assert prepared.content.state[0].text == "Error mapping changed."
        assert await service.latest() is None
        assert service.render(corrected, audience="human") == service.render(prepared, audience="human")
        assert service.render(prepared, audience="human") == service.render(prepared, audience="agent")
        continued = await service.continue_from(prepared)
        assert continued.status == "resolved"
        assert continued.selection == "prepared"
        assert continued.content == prepared.content
        assert continued.selected_revision is None
        assert all(check.status == "available" for check in continued.evidence_checks)

    asyncio.run(scenario())


def test_commit_is_idempotent_and_omission_references_are_not_lineage() -> None:
    async def scenario() -> None:
        service, _, _ = _service()
        omission = HandoffOmission(
            text="Latest test output is unavailable.",
            citation=_citation(MISSING_REF),
        )
        prepared = await service.finalize(_draft("Error mapping changed.", omissions=(omission,)))

        committed = await service.commit(prepared)
        retried = await service.commit(prepared)

        assert committed == retried
        assert await service.revisions() == (committed,)
        assert committed.lineage.sources == (SOURCE_REF,)

    asyncio.run(scenario())


def test_stale_prepared_handoff_cannot_replace_a_newer_milestone() -> None:
    async def scenario() -> None:
        service, _, _ = _service()
        first = await service.finalize(_draft("Initial state."))
        await service.commit(first)
        session_a = await service.finalize(_draft("Session A state."))
        session_b = await service.finalize(_draft("Session B state."))

        current = await service.commit(session_a)
        with pytest.raises(RevisionConflictError) as caught:
            await service.commit(session_b)

        assert caught.value.artifact == session_b.base
        assert caught.value.current == current

    asyncio.run(scenario())


def test_continue_reports_evidence_availability_per_statement() -> None:
    async def scenario() -> None:
        service, _, resolver = _service()
        draft = HandoffDraft(
            objective="Complete parser error handling.",
            state=(
                HandoffStatement(text="Verified state.", citations=(_citation(),)),
                HandoffStatement(text="Unverified state.", citations=(_citation(MISSING_REF),)),
            ),
            disposition="continuable",
            next_action=HandoffStatement(text="Run tests.", citations=(_citation(),)),
        )
        prepared = await service.finalize(draft)
        resolver.unavailable.add((MISSING_REF.source_type, MISSING_REF.source_id))

        continued = await service.continue_from(prepared)

        assert continued.status == "resolved"
        assert [check.status for check in continued.evidence_checks] == [
            "available",
            "unavailable",
            "available",
        ]
        assert continued.evidence_checks[1].unavailable_evidence == (_citation(MISSING_REF),)
        assert continued.trust == "untrusted_history"

    asyncio.run(scenario())


def test_commit_revalidates_evidence_after_preparation() -> None:
    async def scenario() -> None:
        service, _, resolver = _service()
        prepared = await service.finalize(_draft("Current state."))
        resolver.unavailable.add((SOURCE_REF.source_type, SOURCE_REF.source_id))

        with pytest.raises(HandoffEvidenceUnavailableError):
            await service.commit(prepared)

        assert await service.latest() is None

    asyncio.run(scenario())


def test_exact_old_revision_remains_historical_input() -> None:
    async def scenario() -> None:
        service, _, _ = _service()
        first = await service.commit(await service.finalize(_draft("First state.")))
        second = await service.commit(
            await service.finalize(
                _draft(
                    "Second state.",
                    disposition="complete",
                    next_action=None,
                )
            )
        )

        continued = await service.continue_from(first.as_ref())
        latest = await service.continue_latest()

        assert continued.status == "resolved"
        assert continued.selection == "exact"
        assert continued.selected_revision == first.as_ref()
        assert continued.current_revision == second.as_ref()
        assert latest.selection == "latest"
        assert latest.selected_revision == second.as_ref()
        assert latest.content is not None
        assert latest.content.disposition == "complete"
        assert await service.revisions() == (first, second)

    asyncio.run(scenario())


def test_exact_published_revision_is_continuation_input_without_becoming_the_local_head() -> None:
    async def scenario() -> None:
        service, backend, _ = _service()
        published = Handoff(
            artifact_id="pub_01kcontinuation",
            revision=1,
            content=_draft("Published state.").as_content(),
        )
        backend.published[(published.artifact_id, published.revision)] = published

        continued = await service.continue_from(published.as_ref())

        assert continued.status == "resolved"
        assert continued.selection == "exact"
        assert continued.selected_revision == published.as_ref()
        assert continued.current_revision == published.as_ref()
        assert continued.content == published.content
        assert await service.latest() is None

    asyncio.run(scenario())


def test_cross_scope_prepared_handoff_is_rejected() -> None:
    async def scenario() -> None:
        service, _, _ = _service()
        prepared = await service.finalize(_draft("Current state."))
        foreign = PreparedHandoff(
            scope_id="other",
            base=prepared.base,
            content=prepared.content,
        )

        with pytest.raises(ValueError, match="belongs to scope"):
            await service.continue_from(foreign)

    asyncio.run(scenario())


def test_continue_latest_reports_empty_without_a_committed_milestone() -> None:
    async def scenario() -> None:
        service, _, _ = _service()

        continued = await service.continue_latest()

        assert continued.status == "empty"
        assert continued.content is None
        assert continued.selection is None

    asyncio.run(scenario())
