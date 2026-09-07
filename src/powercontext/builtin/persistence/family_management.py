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
import unicodedata
from collections.abc import Callable, Mapping
from typing import Annotated, Any, Protocol, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, field_validator
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import Artifact, ArtifactLineage
from powercontext.builtin.artifacts.experience import Experience, ExperienceContent
from powercontext.builtin.artifacts.handoff import Handoff, HandoffContent, HandoffService, PreparedHandoff
from powercontext.builtin.artifacts.memory import (
    Memory,
    MemoryEntryInput,
    MemoryEntryNotFoundError,
    MemoryLayerError,
    MemoryService,
)
from powercontext.builtin.artifacts.skill import (
    Skill,
    SkillContent,
    SkillPackageError,
    build_instruction_skill_package,
)
from powercontext.builtin.inference import EmbeddingModel
from powercontext.builtin.persistence.artifacts import ArtifactRepository, RepositoryArtifactDraft
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.experience_index import ExperienceIndex
from powercontext.builtin.persistence.handoff import RelationalHandoffBackend, RelationalHandoffEvidenceResolver
from powercontext.builtin.persistence.memory import RelationalMemoryBackend
from powercontext.builtin.persistence.memory_index import MemoryIndex
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


class MemoryCreateEntry(_ManagementValue):
    """One explicit entry supplied by a direct Memory Create command."""

    kind: Annotated[str, Field(min_length=1, max_length=128)]
    text: Annotated[str, Field(min_length=1, max_length=8192)]

    @field_validator("kind")
    @classmethod
    def require_canonical_kind(cls, value: str) -> str:
        canonical = unicodedata.normalize("NFC", value).strip()
        if value != canonical:
            raise ValueError("kind must be a non-empty NFC-normalized trimmed string")  # noqa: TRY003
        return value

    @field_validator("text")
    @classmethod
    def require_nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must contain at least one non-whitespace character")  # noqa: TRY003
        return value


class MemoryCreateContent(_ManagementValue):
    """Command content that creates non-empty canonical Memory state."""

    entries: Annotated[tuple[MemoryCreateEntry, ...], Field(min_length=1, max_length=100)]


class MemoryReplaceEntry(MemoryCreateEntry):
    """Append a new entry or revise one existing logical entry."""

    entry_id: Annotated[str | None, Field(min_length=1, max_length=128)] = None


class MemoryReplaceContent(_ManagementValue):
    """Command content applied by Memory Replace through MemoryService."""

    entries: Annotated[tuple[MemoryReplaceEntry, ...], Field(min_length=1, max_length=100)]


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


class MemoryManagementWriter:
    family = Memory.family

    def __init__(
        self,
        *,
        database: AsyncDatabase,
        artifacts: ArtifactRepository,
        index: MemoryIndex,
        embedding_model: EmbeddingModel | None,
        id_factory: IdFactory,
    ) -> None:
        self._database = database
        self._artifacts = artifacts
        self._index = index
        self._embedding_model = embedding_model
        self._id_factory = id_factory

    def artifact_id_for_create(self, generated: str, /) -> str:
        return generated

    def validate_create(self, content: Mapping[str, JsonValue]) -> BaseModel:
        return _validate(MemoryCreateContent, content, self.family)

    def validate_replace(self, content: Mapping[str, JsonValue]) -> BaseModel:
        return _validate(MemoryReplaceContent, content, self.family)

    def _service(self, scope_id: str, artifact_id: str, connection: AsyncConnection) -> MemoryService:
        def id_factory(kind: str) -> str:
            return artifact_id if kind == "memory" else self._id_factory(kind)

        return MemoryService(
            backend=RelationalMemoryBackend(
                database=self._database,
                scope_id=scope_id,
                artifacts=self._artifacts,
                index=self._index,
                connection=connection,
            ),
            embedding_model=self._embedding_model,
            id_factory=id_factory,
        )

    async def create(
        self,
        connection: AsyncConnection,
        scope_id: str,
        artifact_id: str,
        content: BaseModel,
        direct_source: SourceRef,
        /,
    ) -> Memory:
        command = cast(MemoryCreateContent, content)
        service = self._service(scope_id, artifact_id, connection)
        plan = await service.plan_remember(
            memory=None,
            entries=tuple(MemoryEntryInput(kind=item.kind, text=item.text) for item in command.entries),
            mode="append",
        )
        return await _apply_memory_plan(service, plan, direct_source)

    async def replace(
        self,
        connection: AsyncConnection,
        scope_id: str,
        current: Artifact[Any],
        content: BaseModel,
        direct_source: SourceRef,
        /,
    ) -> Memory:
        if type(current) is not Memory:
            raise InvalidBaseAccessRequestError("family", "does not identify a Memory Artifact")
        memory = current
        command = cast(MemoryReplaceContent, content)
        service = self._service(scope_id, memory.artifact_id, connection)
        current_entries = {entry.entry_id: entry for entry in await service.entries(memory)}
        inputs: list[MemoryEntryInput] = []
        for item in command.entries:
            existing = None if item.entry_id is None else current_entries.get(item.entry_id)
            if item.entry_id is not None and existing is None:
                raise InvalidBaseAccessRequestError("content.entries.entry_id", "does not identify a current entry")
            inputs.append(MemoryEntryInput(kind=item.kind, text=item.text, entry=existing))
        try:
            plan = await service.plan_remember(memory=memory, entries=tuple(inputs), mode="append")
        except (MemoryEntryNotFoundError, MemoryLayerError) as error:
            raise InvalidBaseAccessRequestError("content.entries", "cannot be applied to the current Memory") from error
        return await _apply_memory_plan(service, plan, direct_source)


class HandoffManagementWriter:
    family = Handoff.family
    content_type = HandoffContent

    def __init__(
        self,
        *,
        database: AsyncDatabase,
        artifacts: ArtifactRepository,
        sources: SourceRepository,
        memory_index: MemoryIndex,
        id_factory: IdFactory,
        memory_artifact_id: str,
        handoff_artifact_id: str,
    ) -> None:
        self._database = database
        self._artifacts = artifacts
        self._sources = sources
        self._memory_index = memory_index
        self._id_factory = id_factory
        self._memory_artifact_id = memory_artifact_id
        self._handoff_artifact_id = handoff_artifact_id

    def artifact_id_for_create(self, _generated: str, /) -> str:
        return self._handoff_artifact_id

    def validate_create(self, content: Mapping[str, JsonValue]) -> BaseModel:
        return _validate(HandoffContent, content, self.family)

    def validate_replace(self, content: Mapping[str, JsonValue]) -> BaseModel:
        return _validate(HandoffContent, content, self.family)

    def _service(self, scope_id: str, connection: AsyncConnection) -> HandoffService:
        memory = MemoryService(
            backend=RelationalMemoryBackend(
                database=self._database,
                scope_id=scope_id,
                artifacts=self._artifacts,
                index=self._memory_index,
                connection=connection,
            ),
            id_factory=self._id_factory,
        )
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
                sources=self._sources,
                artifacts=self._artifacts,
                memory=memory,
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


async def _apply_memory_plan(service: MemoryService, plan: Any, direct_source: SourceRef) -> Memory:
    if plan.commit is None:
        raise InvalidBaseAccessRequestError("content.entries", "does not produce a new Memory Revision")
    inherited_artifacts = () if plan.commit.base is None else plan.commit.base.lineage.artifacts
    memory = plan.commit.memory.model_copy(
        update={"lineage": ArtifactLineage(sources=(direct_source,), artifacts=inherited_artifacts)}
    )
    commit = plan.commit.model_copy(update={"memory": memory})
    result = await service.apply(plan.model_copy(update={"result": memory, "commit": commit}))
    if type(result) is not Memory:
        raise RuntimeError("Memory writer did not commit a Memory Revision")  # noqa: TRY003
    return result


def _validate(model: type[BaseModel], content: Mapping[str, JsonValue], family: str) -> BaseModel:
    try:
        return model.model_validate_json(json.dumps(content), strict=True)
    except ValidationError as error:
        raise InvalidBaseAccessRequestError("content", f"does not match the {family} model") from error


__all__ = [
    "ExperienceManagementWriter",
    "FamilyManagementWriterRegistry",
    "HandoffManagementWriter",
    "MemoryManagementWriter",
    "SkillManagementWriter",
]
