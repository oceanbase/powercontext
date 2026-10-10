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

"""Family-owned writers used by the foundational Artifact management API."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any, Protocol, cast, runtime_checkable

from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError
from sqlalchemy.ext.asyncio import AsyncConnection
from typing_extensions import override

from powercontext.artifacts import Artifact
from powercontext.builtin.artifacts.experience import Experience, ExperienceContent
from powercontext.builtin.artifacts.handoff import Handoff, HandoffContent, HandoffService, PreparedHandoff
from powercontext.builtin.artifacts.prompt import Prompt, PromptContent, PromptError, PromptRegistry
from powercontext.builtin.artifacts.skill import (
    Skill,
    SkillContent,
    SkillPackageError,
    build_instruction_skill_package,
)
from powercontext.builtin.persistence.artifacts import ArtifactRepository, RepositoryArtifactDraft
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.experience_index import ExperienceIndex
from powercontext.builtin.persistence.generation_sources import GenerationSourceAccess
from powercontext.builtin.persistence.handoff import RelationalHandoffBackend, RelationalHandoffEvidenceResolver
from powercontext.builtin.persistence.skill_packages import SkillPackageRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.records import (
    ArtifactAlreadyExistsError,
    InvalidBaseAccessRequestError,
)
from powercontext.sources import SourceRef

IdFactory = Callable[[str], str]


class _ManagementValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class FamilyManagementWriter(Protocol):
    """Validate and atomically maintain one Family's authoritative and derived state."""

    family: str

    def artifact_id_for_create(self, generated: str, /) -> str: ...

    def validate_create(self, content: Mapping[str, JsonValue]) -> BaseModel: ...

    def validate_replace(self, content: Mapping[str, JsonValue]) -> BaseModel: ...

    async def create(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact_id: str,
        content: BaseModel,
        direct_source: SourceRef,
        /,
    ) -> Artifact[Any]: ...

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        current: Artifact[Any],
        content: BaseModel,
        direct_source: SourceRef,
        /,
    ) -> Artifact[Any]: ...


@runtime_checkable
class PreparingFamilyManagementWriter(Protocol):
    """Prepare request-local state before opening the write transaction."""

    async def prepare(self, content: BaseModel, /, *, usage_scope_id: str | None = None) -> BaseModel: ...


class FamilyManagementWriterRegistry:
    """Select the owning writer instead of duplicating Family behavior in the REST layer."""

    def __init__(self, writers: tuple[FamilyManagementWriter, ...], /) -> None:
        self._writers = {writer.family: writer for writer in writers}
        if len(self._writers) != len(writers):
            raise ValueError("Family management writers must have unique families")  # noqa: TRY003

    def get(self, family: str, /) -> FamilyManagementWriter:
        try:
            return self._writers[family]
        except KeyError:
            raise InvalidBaseAccessRequestError("family", f"has no management writer: {family}") from None


class _RepositoryFamilyWriter:
    family: str
    content_type: type[BaseModel]

    def __init__(self, artifacts: ArtifactRepository, /) -> None:
        self._artifacts = artifacts

    def artifact_id_for_create(self, generated: str, /) -> str:
        return generated

    def validate_create(self, content: Mapping[str, JsonValue]) -> BaseModel:
        return self._validate(content)

    def validate_replace(self, content: Mapping[str, JsonValue]) -> BaseModel:
        return self._validate(content)

    def _validate(self, content: Mapping[str, JsonValue]) -> BaseModel:
        try:
            return self.content_type.model_validate_json(json.dumps(content), strict=True)
        except ValidationError as error:
            raise InvalidBaseAccessRequestError("content", f"does not match the {self.family} model") from error

    async def _create_artifact(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact_id: str,
        content: BaseModel,
        direct_source: SourceRef,
    ) -> Artifact[Any]:
        return await self._artifacts.create(
            connection,
            scope_id,
            artifact_id,
            RepositoryArtifactDraft(family=self.family, content=content, sources=(direct_source,)),
        )

    async def _revise_artifact(
        self,
        connection: AsyncConnection,
        scope_id: str,
        current: Artifact[Any],
        content: BaseModel,
        direct_source: SourceRef,
    ) -> Artifact[Any]:
        return await self._artifacts.revise(
            connection,
            scope_id,
            current,
            RepositoryArtifactDraft(
                family=self.family,
                content=content,
                sources=(direct_source,),
                artifacts=current.lineage.artifacts,
            ),
        )


class PromptManagementWriter(_RepositoryFamilyWriter):
    """Validate a registered operation and reuse the existing atomic Artifact writer."""

    family = Prompt.family
    content_type = PromptContent

    def __init__(self, artifacts: ArtifactRepository, registry: PromptRegistry, /) -> None:
        super().__init__(artifacts)
        self._registry = registry

    @override
    def artifact_id_for_create(self, generated: str, /) -> str:
        return self._registry.get(generated).key

    @override
    def _validate(self, content: Mapping[str, JsonValue]) -> PromptContent:
        try:
            return PromptContent.model_validate_json(json.dumps(content), strict=True)
        except ValidationError:
            raise PromptError("invalid_prompt_content") from None

    async def create(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact_id: str,
        content: BaseModel,
        direct_source: SourceRef,
        /,
    ) -> Prompt:
        validated = cast(PromptContent, content)
        self._registry.validate(artifact_id, validated)
        return cast(Prompt, await self._create_artifact(connection, scope_id, artifact_id, validated, direct_source))

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        current: Artifact[Any],
        content: BaseModel,
        direct_source: SourceRef,
        /,
    ) -> Prompt:
        validated = cast(PromptContent, content)
        self._registry.validate(current.artifact_id, validated)
        return cast(Prompt, await self._revise_artifact(connection, scope_id, current, validated, direct_source))


class ExperienceManagementWriter(_RepositoryFamilyWriter):
    family = Experience.family
    content_type = ExperienceContent

    def __init__(self, artifacts: ArtifactRepository, index: ExperienceIndex, /) -> None:
        super().__init__(artifacts)
        self._index = index

    async def create(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact_id: str,
        content: BaseModel,
        direct_source: SourceRef,
        /,
    ) -> Experience:
        artifact = cast(
            Experience, await self._create_artifact(connection, scope_id, artifact_id, content, direct_source)
        )
        await self._index.replace(connection, scope_id, artifact)
        return artifact

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        current: Artifact[Any],
        content: BaseModel,
        direct_source: SourceRef,
        /,
    ) -> Experience:
        artifact = cast(Experience, await self._revise_artifact(connection, scope_id, current, content, direct_source))
        await self._index.replace(connection, scope_id, artifact)
        return artifact


class SkillManagementWriter(_RepositoryFamilyWriter):
    family = Skill.family
    content_type = SkillContent

    def __init__(
        self,
        artifacts: ArtifactRepository,
        index: ExperienceIndex,
        packages: SkillPackageRepository,
        /,
    ) -> None:
        super().__init__(artifacts)
        self._index = index
        self._packages = packages

    async def _canonical_content(
        self,
        connection: AsyncConnection,
        scope_id: str,
        content: BaseModel,
    ) -> tuple[SkillContent, Any]:
        proposal = cast(SkillContent, content)
        try:
            if proposal.package is None:
                package = build_instruction_skill_package(proposal)
                await self._packages.add(connection, scope_id, package)
            else:
                package = await self._packages.get(connection, scope_id, proposal.package)
        except (RepositoryNotFoundError, SkillPackageError) as error:
            raise InvalidBaseAccessRequestError("content.package", "is unavailable or invalid") from error
        canonical = package.as_skill_content()
        if proposal.package is not None and canonical != proposal:
            raise InvalidBaseAccessRequestError(
                "content.package",
                "cached Skill fields do not match the exact package",
            )
        return canonical, package

    async def create(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact_id: str,
        content: BaseModel,
        direct_source: SourceRef,
        /,
    ) -> Skill:
        canonical, package = await self._canonical_content(connection, scope_id, content)
        artifact = cast(
            Skill,
            await self._create_artifact(connection, scope_id, artifact_id, canonical, direct_source),
        )
        await self._index.replace_skill(connection, scope_id, artifact, package)
        return artifact

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        current: Artifact[Any],
        content: BaseModel,
        direct_source: SourceRef,
        /,
    ) -> Skill:
        canonical, package = await self._canonical_content(connection, scope_id, content)
        artifact = cast(
            Skill,
            await self._revise_artifact(connection, scope_id, current, canonical, direct_source),
        )
        await self._index.replace_skill(connection, scope_id, artifact, package)
        return artifact


class HandoffManagementWriter:
    family = Handoff.family
    content_type = HandoffContent

    def __init__(
        self,
        *,
        database: AsyncDatabase,
        artifacts: ArtifactRepository,
        sources: SourceRepository,
        handoff_artifact_id: str,
    ) -> None:
        self._database = database
        self._artifacts = artifacts
        self._sources = sources
        self._handoff_artifact_id = handoff_artifact_id

    def artifact_id_for_create(self, _generated: str, /) -> str:
        return self._handoff_artifact_id

    def validate_create(self, content: Mapping[str, JsonValue]) -> BaseModel:
        return _validate(HandoffContent, content, self.family)

    def validate_replace(self, content: Mapping[str, JsonValue]) -> BaseModel:
        return _validate(HandoffContent, content, self.family)

    def _service(self, scope_id: str, connection: AsyncConnection) -> HandoffService:
        return HandoffService(
            scope_id=scope_id,
            artifact_id=self._handoff_artifact_id,
            backend=RelationalHandoffBackend(
                database=self._database,
                scope_id=scope_id,
                artifacts=self._artifacts,
                connection=connection,
            ),
            evidence_resolver=RelationalHandoffEvidenceResolver(
                database=self._database,
                scope_id=scope_id,
                sources=GenerationSourceAccess(self._sources),
                artifacts=self._artifacts,
                connection=connection,
            ),
        )

    async def create(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact_id: str,
        content: BaseModel,
        direct_source: SourceRef,
        /,
    ) -> Handoff:
        service = self._service(scope_id, connection)
        if artifact_id != self._handoff_artifact_id or await service.latest() is not None:
            raise ArtifactAlreadyExistsError(self.family, self._handoff_artifact_id, use_replace=True)
        prepared = PreparedHandoff(scope_id=scope_id, base=None, content=cast(HandoffContent, content))
        return await service.commit(
            prepared,
            additional_sources=(direct_source,),
            force_revision=True,
        )

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        current: Artifact[Any],
        content: BaseModel,
        direct_source: SourceRef,
        /,
    ) -> Handoff:
        if type(current) is not Handoff or current.artifact_id != self._handoff_artifact_id:
            raise InvalidBaseAccessRequestError(
                "artifact_id",
                "must identify the Scope's configured Handoff singleton",
            )
        prepared = PreparedHandoff(
            scope_id=scope_id,
            base=current.as_ref(),
            content=cast(HandoffContent, content),
        )
        return await self._service(scope_id, connection).commit(
            prepared,
            additional_sources=(direct_source,),
            force_revision=True,
        )


def _validate(model: type[BaseModel], content: Mapping[str, JsonValue], family: str) -> BaseModel:
    try:
        return model.model_validate_json(json.dumps(content), strict=True)
    except ValidationError as error:
        raise InvalidBaseAccessRequestError("content", f"does not match the {family} model") from error


__all__ = [
    "ExperienceManagementWriter",
    "FamilyManagementWriterRegistry",
    "HandoffManagementWriter",
    "SkillManagementWriter",
]


class AtomicMemoryManagementPrepared(BaseModel):
    """Prepared domain command with its server-owned execution identity."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)
    prepared: Any
    execution_context: Any


class AtomicMemoryManagementWriter:
    """Route foundational Create/Replace through the Atomic domain writer."""

    family = "atomic-memory"

    def __init__(self, application) -> None:
        self.application = application

    def artifact_id_for_create(self, generated: str, /) -> str:
        return generated

    def validate_create(self, content: Mapping[str, JsonValue]) -> BaseModel:
        return self._validate(content)

    def validate_replace(self, content: Mapping[str, JsonValue]) -> BaseModel:
        return self._validate(content)

    def _validate(self, content: Mapping[str, JsonValue]) -> BaseModel:
        from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemoryContent

        if "creation" in content:
            raise InvalidBaseAccessRequestError("content.creation", "is maintained by the Atomic Memory service")
        try:
            return AtomicMemoryContent.model_validate_json(json.dumps(content), strict=True)
        except ValidationError as error:
            raise InvalidBaseAccessRequestError("content", "does not match the atomic-memory model") from error

    async def prepare_command(
        self,
        scope_id: str,
        artifact_id: str,
        content: BaseModel,
        *,
        expected_revision: int | None = None,
        execution_context=None,
    ) -> AtomicMemoryManagementPrepared:
        from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemoryContent

        context = execution_context
        async with self.application.database.transaction() as connection:
            plan = await self.application.service.inspect_change(
                connection,
                scope_id,
                artifact_id,
                cast(AtomicMemoryContent, content),
                context,
                expected_revision=expected_revision,
            )
        prepared = await self.application.service.prepare_change(plan)
        return AtomicMemoryManagementPrepared(prepared=prepared, execution_context=context)

    async def create(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact_id: str,
        content: BaseModel,
        direct_source: SourceRef,
        /,
    ) -> Artifact[Any]:
        command = cast(AtomicMemoryManagementPrepared, content)
        result = await self.application.service.commit(
            connection, command.prepared, command.execution_context, direct_source=direct_source
        )
        return result.primary.artifact

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        current: Artifact[Any],
        content: BaseModel,
        direct_source: SourceRef,
        /,
    ) -> Artifact[Any]:
        command = cast(AtomicMemoryManagementPrepared, content)
        result = await self.application.service.commit(
            connection, command.prepared, command.execution_context, direct_source=direct_source
        )
        return result.primary.artifact
