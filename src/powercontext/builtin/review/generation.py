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

"""Generate typed reviewed content from exact bounded evidence."""

from __future__ import annotations

from enum import StrEnum
from typing import TypeAlias

from pydantic import BaseModel

from powercontext.artifacts import Artifact, ArtifactRef
from powercontext.builtin.artifacts.experience import Experience, ExperienceContent, ExperienceGenerator
from powercontext.builtin.artifacts.generation import (
    MAX_GENERATION_EVIDENCE_CHARS,
    ArtifactGenerationInput,
    GenerationEvidence,
    GenerationEvidenceKind,
)
from powercontext.builtin.artifacts.skill import Skill, SkillContent, SkillGenerator
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.review.errors import InvalidCandidateError
from powercontext.builtin.review.models import ArtifactCandidate
from powercontext.builtin.review.service import ReviewService
from powercontext.errors import PowerContextError
from powercontext.sources import Source, SourceRef


class SkillGenerationOrigin(StrEnum):
    """Lineage shape selected explicitly by the owning integration."""

    EXPERIENCE = "experience"
    SOURCE = "source"
    USAGE = "usage"


class GenerationCapabilityUnavailableError(PowerContextError, RuntimeError):
    """Raised before persistence when a Family generator is not configured."""

    def __init__(self, family: str) -> None:
        self.family = family
        super().__init__(f"{family} generation is not configured")


GeneratedCandidate: TypeAlias = ArtifactCandidate[ExperienceContent] | ArtifactCandidate[SkillContent]


class GeneratedCandidateResult(BaseModel):
    """A pending Candidate or an explicit semantic no-op."""

    candidate: GeneratedCandidate | None = None

    @property
    def generated(self) -> bool:
        return self.candidate is not None


class ReviewedGenerationService:
    """Resolve evidence, call the model outside a transaction, then enter Review."""

    def __init__(
        self,
        *,
        database: AsyncDatabase,
        scope_id: str,
        sources: SourceRepository,
        artifacts: ArtifactRepository,
        review: ReviewService,
        experience_generator: ExperienceGenerator | None,
        skill_generator: SkillGenerator | None,
    ) -> None:
        self._database = database
        self._scope_id = scope_id
        self._sources = sources
        self._artifacts = artifacts
        self._review = review
        self._experience_generator = experience_generator
        self._skill_generator = skill_generator

    async def experience(
        self,
        *,
        sources: tuple[SourceRef, ...],
        artifacts: tuple[ArtifactRef, ...],
        target: ArtifactRef | None,
        reason: str | None,
    ) -> GeneratedCandidateResult:
        if self._experience_generator is None:
            raise GenerationCapabilityUnavailableError(Experience.family)
        if target is not None and target.family != Experience.family:
            raise InvalidCandidateError("target", "must identify an Experience")
        evidence = await self._evidence(sources, artifacts)
        proposal = await self._experience_generator.generate(_generation_input(evidence, target))
        if proposal is None:
            return GeneratedCandidateResult()
        candidate = await self._review.propose_experience(
            proposal,
            sources=sources,
            artifacts=artifacts,
            target=target,
            reason=reason,
        )
        return GeneratedCandidateResult(candidate=candidate)

    async def skill(
        self,
        *,
        origin: SkillGenerationOrigin,
        sources: tuple[SourceRef, ...],
        artifacts: tuple[ArtifactRef, ...],
        target: ArtifactRef | None,
        reason: str | None,
    ) -> GeneratedCandidateResult:
        if self._skill_generator is None:
            raise GenerationCapabilityUnavailableError(Skill.family)
        _validate_skill_lineage(origin, sources, artifacts, target)
        evidence = await self._evidence(sources, artifacts)
        proposal = await self._skill_generator.generate(_generation_input(evidence, target))
        if proposal is None:
            return GeneratedCandidateResult()
        candidate = await self._review.propose_skill(
            proposal,
            sources=sources,
            artifacts=artifacts,
            target=target,
            reason=reason,
        )
        return GeneratedCandidateResult(candidate=candidate)

    async def _evidence(
        self,
        sources: tuple[SourceRef, ...],
        artifacts: tuple[ArtifactRef, ...],
    ) -> tuple[GenerationEvidence, ...]:
        evidence: list[GenerationEvidence] = []
        try:
            async with self._database.transaction() as connection:
                for ref in sources:
                    row = await self._sources.get(connection, self._scope_id, ref)
                    evidence.append(_source_evidence(ref, row.value))
                for ref in artifacts:
                    artifact = await self._artifacts.get(connection, self._scope_id, ref)
                    evidence.append(_artifact_evidence(ref, artifact))
        except RepositoryNotFoundError as error:
            raise InvalidCandidateError("evidence", "reference is not available in this scope") from error
        if not evidence:
            raise InvalidCandidateError("evidence", "at least one exact reference is required")
        return tuple(evidence)


def _source_evidence(ref: SourceRef, source: Source) -> GenerationEvidence:
    return _bounded_evidence(
        evidence_id=f"source:{ref.source_type}/{ref.source_id}",
        kind=GenerationEvidenceKind.SOURCE,
        content=source.model_dump_json(),
    )


def _artifact_evidence(ref: ArtifactRef, artifact: Artifact[BaseModel]) -> GenerationEvidence:
    return _bounded_evidence(
        evidence_id=f"artifact:{ref.family}/{ref.artifact_id}@{ref.revision}",
        kind=GenerationEvidenceKind.ARTIFACT,
        content=artifact.model_dump_json(),
    )


def _bounded_evidence(*, evidence_id: str, kind: GenerationEvidenceKind, content: str) -> GenerationEvidence:
    return GenerationEvidence(
        evidence_id=evidence_id,
        kind=kind,
        content=content[:MAX_GENERATION_EVIDENCE_CHARS],
        truncated=len(content) > MAX_GENERATION_EVIDENCE_CHARS,
    )


def _generation_input(
    evidence: tuple[GenerationEvidence, ...],
    target: ArtifactRef | None,
) -> ArtifactGenerationInput:
    target_id = None if target is None else f"artifact:{target.family}/{target.artifact_id}@{target.revision}"
    if target_id is not None and target_id not in {value.evidence_id for value in evidence}:
        raise InvalidCandidateError("artifacts", "must include the exact target Artifact")
    return ArtifactGenerationInput(evidence=evidence, target_evidence_id=target_id)


def _validate_skill_lineage(
    origin: SkillGenerationOrigin,
    sources: tuple[SourceRef, ...],
    artifacts: tuple[ArtifactRef, ...],
    target: ArtifactRef | None,
) -> None:
    if origin is SkillGenerationOrigin.EXPERIENCE:
        if target is not None or not artifacts or any(ref.family != Experience.family for ref in artifacts):
            raise InvalidCandidateError(
                "origin",
                "Experience generation requires approved Experience references and no target",
            )
        return
    if origin is SkillGenerationOrigin.SOURCE:
        if target is not None or not sources or artifacts:
            raise InvalidCandidateError("origin", "Source generation requires only exact Source references")
        return
    if target is None or target.family != Skill.family or target not in artifacts or not sources:
        raise InvalidCandidateError(
            "origin",
            "usage evolution requires the exact target Skill and bounded Source evidence",
        )


__all__ = [
    "GeneratedCandidateResult",
    "GenerationCapabilityUnavailableError",
    "ReviewedGenerationService",
    "SkillGenerationOrigin",
]
