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

"""Read eligibility and exact evidence through the authenticated PowerContext Client SDK."""

from __future__ import annotations

import base64
import json

from pydantic import BaseModel, ConfigDict, Field

from powercontext.artifacts import ArtifactAddress, ArtifactRef
from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.artifacts.experience.search import render_experience
from powercontext.builtin.artifacts.skill import SkillContent, capture_skill_archive, package_file
from powercontext.builtin.artifacts.skill.compatibility import SkillCompatibilityState, assess_skill_compatibility
from powercontext.builtin.artifacts.skill.external import AgentSkillTarget
from powercontext.client import PowerContextClient
from powercontext.http import (
    ArtifactReference,
    GetExperienceRequest,
    GetSkillPackageRequest,
    GetSkillRequest,
    ListManagedSkillsRequest,
    PrepareContextRequest,
    SkillLifecycleState,
)

from .applicability import ApplicabilityCandidate


class CandidateOmission(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    address: ArtifactAddress
    reason: str


class CandidatePool(BaseModel):
    """A bounded snapshot; omissions never become candidates by receiving a model score."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    candidates: tuple[ApplicabilityCandidate, ...] = Field(default=(), max_length=16)
    omissions: tuple[CandidateOmission, ...] = ()


class StaleRecommendationError(ValueError):
    """An exact recommendation is no longer in the current eligible retrieval snapshot."""


class ServerCandidateCatalog:
    """Use the caller's existing Server identity, Review and package-validation boundaries.

    Experience comes from ordinary active-head retrieval (currently at most two entries).
    Skill metadata comes from the active managed library; only a bounded shortlist's exact
    standard packages is downloaded. Static compatibility is delegated to the existing Skill
    assessor. No database, candidate inbox, installation directory, or write API is accessed.
    """

    def __init__(self, client: PowerContextClient, target: AgentSkillTarget, *, skill_limit: int = 8) -> None:
        if isinstance(skill_limit, bool) or not 1 <= skill_limit <= 14:
            raise ValueError("Skill shortlist limit must be between one and fourteen")  # noqa: TRY003
        self._client = client
        self._target = target
        self._skill_limit = skill_limit

    async def retrieve(self, scope_id: str, query: str, /) -> CandidatePool:
        candidates: list[ApplicabilityCandidate] = []
        omissions: list[CandidateOmission] = []
        prepared = await self._client.prepare_context(
            PrepareContextRequest(
                scope_id=scope_id,
                query=query,
                max_bytes=32768,
            )
        )
        if prepared.content is not None:
            # Delimiters occupy whole lines; quoted markers inside JSON evidence remain data.
            lines = prepared.content.splitlines()
            begin = lines.index("BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1") + 1
            end = lines.index("END_POWERCONTEXT_PREPARED_CONTEXT_V1", begin)
            text = "\n".join(lines[begin:end])
            for item in json.loads(text)["items"]:
                if item["kind"] != "experience":
                    continue
                citation = item["citation"]
                address = (
                    ArtifactAddress.model_validate(citation["artifact"])
                    if "artifact" in citation
                    else ArtifactAddress(
                        scope_id=scope_id,
                        artifact=ArtifactRef.model_validate(citation["artifact_ref"]),
                    )
                )
                exact = await self._client.get_experience(
                    GetExperienceRequest(
                        scope_id=address.scope_id,
                        artifact=ArtifactReference.model_validate(address.artifact.model_dump()),
                    )
                )
                head = await self._client.get_artifact(
                    address.scope_id,
                    "experience",
                    address.artifact.artifact_id,
                )
                if head is None or head.revision != address.artifact.revision:
                    omissions.append(
                        CandidateOmission(address=address, reason="Experience head changed during retrieval")
                    )
                    continue
                content = ExperienceContent.model_validate(exact.content.model_dump())
                candidates.append(
                    ApplicabilityCandidate(
                        address=address,
                        content=render_experience(content),
                        content_digest=head.content_digest,
                    )
                )
        library = await self._client.list_managed_skills(
            ListManagedSkillsRequest(
                scope_id=scope_id,
                query=query,
                include_deprecated=False,
                limit=self._skill_limit,
            )
        )
        for entry in library.skills:
            address = ArtifactAddress(
                scope_id=scope_id, artifact=ArtifactRef.model_validate(entry.artifact.model_dump())
            )
            if entry.governance.lifecycle_state is not SkillLifecycleState.ACTIVE:
                omissions.append(CandidateOmission(address=address, reason="Skill is not active"))
                continue
            candidate, reason = await self._skill_candidate(address)
            if candidate is None:
                omissions.append(CandidateOmission(address=address, reason=reason))
            else:
                candidates.append(candidate)
        return CandidatePool(candidates=tuple(candidates), omissions=tuple(omissions))

    async def revalidate(
        self,
        scope_id: str,
        query: str,
        candidates: tuple[ApplicabilityCandidate, ...],
        /,
    ) -> None:
        """Recheck reads, active eligibility and exact identities before a host loads a result.

        A changed snapshot is rejected, never silently upgraded to a newer revision. This is a
        read-time check, not an execution lease; a host must retain its normal approval checks.
        """

        current = await self.retrieve(scope_id, query)
        by_address = {item.address.model_dump_json(): item for item in current.candidates}
        for candidate in candidates:
            fresh = by_address.get(candidate.address.model_dump_json())
            if fresh != candidate:
                raise StaleRecommendationError("recommendation changed or is no longer eligible")  # noqa: TRY003

    async def _skill_candidate(self, address: ArtifactAddress) -> tuple[ApplicabilityCandidate | None, str]:
        reference = ArtifactReference.model_validate(address.artifact.model_dump())
        exact = await self._client.get_skill(GetSkillRequest(scope_id=address.scope_id, artifact=reference))
        content = SkillContent.model_validate(exact.content.model_dump())
        if content.package is None:
            return None, "legacy Skill has no standard package for compatibility validation"
        downloaded = await self._client.download_skill_package(
            GetSkillPackageRequest(
                scope_id=address.scope_id,
                artifact=reference,
            )
        )
        snapshot = capture_skill_archive(base64.b64decode(downloaded.archive_base64, validate=True))
        if snapshot.reference != content.package or snapshot.reference.model_dump() != downloaded.package.model_dump():
            raise ValueError("exact Skill package does not match its approved reference")  # noqa: TRY003
        compatibility = assess_skill_compatibility(content, snapshot, self._target)
        if compatibility.state is not SkillCompatibilityState.COMPATIBLE:
            return None, f"Skill compatibility is {compatibility.state.value}"
        head = await self._client.get_artifact(address.scope_id, "skill", address.artifact.artifact_id)
        if head is None or head.revision != address.artifact.revision:
            return None, "Skill head changed during retrieval"
        # Complete entrypoint and textual supporting references. Programs are never interpreted
        # or executed; the owned compatibility assessor handles declared runtime requirements.
        try:
            files = {
                entry.path: package_file(snapshot, entry.path).decode("utf-8")
                for entry in snapshot.entries
                if (
                    entry.media_type.startswith("text/") or entry.media_type in {"application/json", "application/yaml"}
                )
                and not entry.path.startswith("scripts/")
            }
        except UnicodeDecodeError:
            return None, "complete Skill text evidence is not UTF-8"
        evidence = json.dumps(
            {
                "name": snapshot.metadata.name,
                "description": snapshot.metadata.description,
                "compatibility": snapshot.metadata.compatibility,
                "files": files,
            },
            ensure_ascii=False,
        )
        return ApplicabilityCandidate(
            address=address,
            content=evidence,
            content_digest=head.content_digest,
            package_digest="sha256:" + snapshot.reference.tree_digest,
        ), ""
