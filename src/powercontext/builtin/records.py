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
from datetime import datetime
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, JsonValue

from powercontext.artifacts import ArtifactRef
from powercontext.sources import SourceRef

TextSearchMode = Literal["auto", "keyword"]


class _RecordModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SourceRecord(_RecordModel):
    """One durable Source and its journal position."""

    scope_id: str
    source_type: str
    source_id: str
    content: JsonValue
    metadata: dict[str, JsonValue]
    created_at: datetime | None
    position: int
    content_digest: str


class SourceCollectionItem(_RecordModel):
    """One Source collection projection without its potentially large content."""

    scope_id: str
    source_type: str
    source_id: str
    metadata: dict[str, JsonValue]
    created_at: datetime | None
    position: int
    content_digest: str
    score: float | None = None
    snippets: tuple[str, ...] = ()


class SourceRecordPage(_RecordModel):
    """One stable page for either Source listing or keyword search."""

    query: str | None
    mode: Literal["keyword"] | None
    items: tuple[SourceCollectionItem, ...]
    next_cursor: str | None


class ArtifactWrite(_RecordModel):
    """Complete content and direct lineage for one Artifact revision."""

    content: dict[str, JsonValue]
    source_refs: tuple[SourceRef, ...] = ()
    artifact_refs: tuple[ArtifactRef, ...] = ()


class ArtifactRecord(_RecordModel):
    """One immutable Artifact revision with direct lineage."""

    scope_id: str
    artifact_ref: ArtifactRef
    content: dict[str, JsonValue]
    source_refs: tuple[SourceRef, ...]
    artifact_refs: tuple[ArtifactRef, ...]
    created_at: datetime | None
    content_digest: str


class ArtifactCollectionItem(_RecordModel):
    """One active Artifact head without content or lineage."""

    scope_id: str
    artifact_ref: ArtifactRef
    created_at: datetime | None
    content_digest: str
    score: float | None = None
    snippets: tuple[str, ...] = ()


class ArtifactRecordPage(_RecordModel):
    """One stable page for Artifact listing or keyword search."""

    query: str | None
    mode: Literal["keyword"] | None
    items: tuple[ArtifactCollectionItem, ...]
    next_cursor: str | None


class ArtifactDeletion(_RecordModel):
    """The durable deletion state for one Artifact lifecycle."""

    artifact_ref: ArtifactRef
    deleted_at: datetime


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

    def __init__(self, provided_revision: int, current_revision: int) -> None:
        self.provided_revision = provided_revision
        self.current_revision = current_revision
        super().__init__("Artifact revision precondition failed")


class RecordService(Protocol):
    """Persistence-backed base Source, Artifact, and Scope operations."""

    async def create_source(
        self,
        scope_id: str,
        source_type: str,
        content: JsonValue,
        metadata: Mapping[str, JsonValue],
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

    async def query_sources(
        self,
        scope_id: str,
        source_type: str,
        /,
        *,
        query: str | None,
        mode: TextSearchMode | None,
        limit: int,
        cursor: str | None,
    ) -> SourceRecordPage: ...

    async def create_artifact(
        self,
        scope_id: str,
        family: str,
        write: ArtifactWrite,
        /,
    ) -> ArtifactRecord: ...

    async def get_artifact(self, scope_id: str, family: str, artifact_id: str, /) -> ArtifactRecord: ...

    async def get_artifact_revision(
        self,
        scope_id: str,
        family: str,
        artifact_id: str,
        revision: int,
        /,
    ) -> ArtifactRecord: ...

    async def query_artifacts(
        self,
        scope_id: str,
        family: str,
        /,
        *,
        query: str | None,
        mode: TextSearchMode | None,
        limit: int,
        cursor: str | None,
    ) -> ArtifactRecordPage: ...

    async def replace_artifact(
        self,
        scope_id: str,
        family: str,
        artifact_id: str,
        expected_revision: int,
        write: ArtifactWrite,
        /,
    ) -> ArtifactRecord: ...

    async def delete_artifact(
        self,
        scope_id: str,
        family: str,
        artifact_id: str,
        expected_revision: int,
        /,
    ) -> ArtifactDeletion: ...

    async def list_scopes(self, *, limit: int, cursor: str | None) -> ScopeSummaryPage: ...
