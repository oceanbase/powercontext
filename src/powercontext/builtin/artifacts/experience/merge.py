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

"""Experience content and current-index adapter for the shared merge service."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactLineage, ArtifactRef
from powercontext.builtin.artifacts.experience.models import Experience, ExperienceContent, ExperienceDraft
from powercontext.builtin.artifacts.experience.search import experience_searchable_text
from powercontext.builtin.artifacts.merge_models import ArtifactMergeRecord, ArtifactMergeRelationError
from powercontext.builtin.persistence.experience_index import ExperienceIndex


class ExperienceMergeAdapter:
    """Accept direct Sources and exact in-Scope Artifacts as explicit merge evidence.

    Legacy Memory citations and publication lineage remain supported by their
    existing writers and exact reads; this adapter rejects them on new writes.
    Stored historical content is restored through precise Artifact references.
    """

    family = Experience.family

    def __init__(self, index: ExperienceIndex) -> None:
        self.index = index

    def draft(
        self,
        content: Any,
        lineage: ArtifactLineage,
        *,
        merge_inputs: tuple[ArtifactRef, ...] = (),
        historical: bool = False,
    ) -> ExperienceDraft:
        if lineage.publication_source is not None:
            raise ArtifactMergeRelationError("Experience accepts direct Sources and exact in-Scope Artifacts")  # noqa: TRY003
        return ExperienceDraft(
            content=ExperienceContent.model_validate(content), sources=lineage.sources, artifacts=lineage.artifacts
        )

    async def prepare(self, content: Any) -> str:
        return experience_searchable_text(ExperienceContent.model_validate(content))

    def validate_prepared(self, prepared: Any) -> None:
        if not isinstance(prepared, str) or not prepared:
            raise ArtifactMergeRelationError("Experience publication requires prepared searchable text")  # noqa: TRY003

    async def publish(
        self,
        connection: AsyncConnection,
        scope_id: str,
        record: ArtifactMergeRecord,
        prepared: Any,
        execution_context: Any,
    ) -> None:
        experience = cast(Experience, record.artifact)
        if type(experience) is not Experience or prepared != experience_searchable_text(experience.content):
            raise ArtifactMergeRelationError("Experience projection does not match its exact content")  # noqa: TRY003
        await self.index.replace(connection, scope_id, experience)

    async def remove(self, connection: AsyncConnection, scope_id: str, artifact_id: str) -> None:
        await self.index.remove(connection, scope_id, artifact_id)
