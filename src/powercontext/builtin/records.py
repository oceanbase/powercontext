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

"""Transport-neutral values for base Source and Artifact access."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, JsonValue

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryEntryVersion
from powercontext.sources import SourceRef

BaseArtifactFamily = Literal["memory", "experience", "skill", "handoff"]


class _RecordModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SourceRecord(_RecordModel):
    """One durable Source and its journal position."""

    scope_id: str
    source_type: Literal["content"]
    source_id: str
    content: JsonValue
    position: int
    content_digest: str


class ArtifactWrite(_RecordModel):
    """Complete family-specific content for one Artifact write."""

    content: dict[str, JsonValue]


class ArtifactCreated(_RecordModel):
    """Identity and server-owned lineage returned by Artifact Create."""

    scope_id: str
    family: BaseArtifactFamily
    artifact_id: str
    revision: int
    sources: tuple[SourceRef, ...]
    artifacts: tuple[ArtifactRef, ...]


class ArtifactRecord(_RecordModel):
    """One immutable Artifact revision with direct lineage."""

    scope_id: str
    family: BaseArtifactFamily
    artifact_id: str
    revision: int
    content: dict[str, JsonValue]
    sources: tuple[SourceRef, ...]
    artifacts: tuple[ArtifactRef, ...]
    content_digest: str


class ArtifactCollectionItem(_RecordModel):
    """One active Artifact head without content or lineage."""

    scope_id: str
    family: BaseArtifactFamily
    artifact_id: str
    revision: int
    sources: tuple[SourceRef, ...]
    artifacts: tuple[ArtifactRef, ...]
    content_digest: str


class LogicalArtifactRecord(_RecordModel):
    """A committed logical identity, without content or revision selection."""

    family: str
    artifact_id: str
    entry_id: str | None = None


class ArtifactRecordPage(_RecordModel):
    """One stable page of current Artifact heads."""

    items: tuple[ArtifactCollectionItem, ...]
    next_cursor: str | None


class ScopeSummary(_RecordModel):
    """Scope identity plus Source and Artifact activity summaries."""

    scope_id: str
    title: str | None = None
    summary: str | None = None
    parent_scope_id: str | None = None
    version: int | None = None
    source_types: tuple[str, ...] = ()
    artifact_families: tuple[str, ...] = ()
    source_count: int = 0
    artifact_count: int = 0


class ScopeSummaryPage(_RecordModel):
    """One stable page of observable Scopes."""

    items: tuple[ScopeSummary, ...]
    next_cursor: str | None


class BaseAccessError(Exception):
    """Base error for Source and Artifact access failures."""


class BaseValueNotFoundError(BaseAccessError):
    """Report an absent or non-visible Source or Artifact."""

    def __init__(self, kind: Literal["source", "artifact"], identity: object) -> None:
        self.kind = kind
        self.identity = identity
        super().__init__(f"{kind} was not found")


class BaseValueConflictError(BaseAccessError):
    """Report an identity that already names different durable state."""

    def __init__(self, kind: Literal["source", "artifact"], identity: object) -> None:
        self.kind = kind
        self.identity = identity
        super().__init__(f"{kind} identity conflicts with durable state")


class ArtifactAlreadyExistsError(BaseAccessError):
    """Report a singleton Artifact that must be updated through Replace."""

    def __init__(self, family: str, artifact_id: str, *, use_replace: bool = False) -> None:
        self.family = family
        self.artifact_id = artifact_id
        self.use_replace = use_replace
        super().__init__(f"{family} Artifact {artifact_id} already exists")


class BaseOperationNotSupportedError(BaseAccessError):
    """Report an operation disabled for one Source type or Artifact family."""

    def __init__(self, kind: Literal["source_type", "artifact_family"], name: str, operation: str) -> None:
        self.kind = kind
        self.name = name
        self.operation = operation
        super().__init__(f"{operation} is not supported for {kind} {name}")


class InvalidBaseAccessRequestError(BaseAccessError, ValueError):
    """Report a request combination that cannot be interpreted safely."""

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"{field} {reason}")


class InvalidCursorError(InvalidBaseAccessRequestError):
    """Report a malformed, tampered, or context-mismatched pagination cursor."""

    def __init__(self, reason: str = "is invalid") -> None:
        super().__init__("cursor", reason)


class CursorExpiredError(BaseAccessError):
    """Report a valid pagination cursor whose bounded lifetime elapsed."""


class ArtifactRevisionPreconditionError(BaseAccessError):
    """Report an Artifact ETag that no longer identifies the current head."""

    def __init__(self, provided_etag: str, current_etag: str) -> None:
        self.provided_etag = provided_etag
        self.current_etag = current_etag
        super().__init__("Artifact revision precondition failed")


class RecordService(Protocol):
    """Persistence-backed base Source, Artifact, and Scope operations."""

    async def create_source(
        self,
        scope_id: str,
        source_type: str,
        content: JsonValue,
        /,
    ) -> SourceRecord: ...

    async def capture_source(
        self,
        scope_id: str,
        source_type: str,
        source_id: str,
        content: JsonValue,
        metadata: Mapping[str, JsonValue],
        /,
    ) -> SourceRecord: ...

    async def get_source(self, scope_id: str, source_type: str, source_id: str, /) -> SourceRecord: ...

    async def create_artifact(
        self,
        scope_id: str,
        family: str,
        write: ArtifactWrite,
        /,
    ) -> ArtifactCreated: ...

    async def get_artifact(self, scope_id: str, family: str, artifact_id: str, /) -> ArtifactRecord: ...

    async def get_artifact_revision(
        self,
        scope_id: str,
        family: str,
        artifact_id: str,
        revision: int,
        /,
    ) -> ArtifactRecord: ...

    async def current_memory_entry(self, scope_id: str, artifact_id: str, entry_id: str, /) -> MemoryEntryVersion: ...

    async def logical_artifacts(self, scope_id: str, /) -> tuple[LogicalArtifactRecord, ...]: ...

    async def query_artifacts(
        self,
        scope_id: str,
        family: str,
        /,
        *,
        limit: int,
        cursor: str | None,
    ) -> ArtifactRecordPage: ...

    async def replace_artifact(
        self,
        scope_id: str,
        family: str,
        artifact_id: str,
        expected_etag: str,
        write: ArtifactWrite,
        /,
    ) -> ArtifactRecord: ...

    async def list_scopes(self, *, limit: int, cursor: str | None) -> ScopeSummaryPage: ...
