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

"""Typed captured text-file snapshots for the OpenDAL Connector."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import PurePosixPath
from typing import Literal, cast

from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    Source,
    SourceMaterialization,
    SourceProjection,
    TextEvidence,
)
from pydantic import BaseModel, JsonValue, field_validator

TEXT_FILE_SNAPSHOT_SOURCE_NAME = "text-file-snapshot"


class TextFileSnapshotCapture(BaseModel):
    """One UTF-8 file value captured with non-authoritative provider annotations."""

    namespace: str
    path: str
    content: str
    media_type: str = "text/plain"
    encoding: Literal["utf-8"] = "utf-8"
    etag: str | None = None
    provider_version: str | None = None
    modified_at: datetime | None = None

    @field_validator("namespace", "media_type")
    @classmethod
    def validate_trimmed_text(cls, value: str) -> str:
        if not value or value.strip() != value:
            raise ValueError("value must be a non-empty trimmed string")  # noqa: TRY003
        return value

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if not value or value.strip() != value or "\\" in value:
            raise ValueError("path must be a non-empty normalized POSIX path")  # noqa: TRY003
        path = PurePosixPath(value)
        if path.is_absolute() or value != path.as_posix() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("path must be a relative normalized POSIX path")  # noqa: TRY003
        return value

    @field_validator("etag", "provider_version")
    @classmethod
    def validate_optional_annotation(cls, value: str | None) -> str | None:
        if value is not None and (not value or value.strip() != value):
            raise ValueError("annotation must be a non-empty trimmed string")  # noqa: TRY003
        return value


class TextFileSnapshotSource(Source):
    """Captured text-file bytes with explicit filesystem provenance."""

    namespace: str
    path: str
    content: str
    media_type: str
    encoding: Literal["utf-8"]
    content_digest: str
    size: int
    etag: str | None = None
    provider_version: str | None = None
    modified_at: datetime | None = None


class TextFileSnapshotSourceDefinition:
    """Define canonical captured text-file snapshots and their projections."""

    input_class = TextFileSnapshotCapture
    name = TEXT_FILE_SNAPSHOT_SOURCE_NAME
    version = "1"
    source_class = TextFileSnapshotSource

    def __init__(self) -> None:
        projection = cast(SourceProjection[TextFileSnapshotSource], TextFileEvidenceProjection())
        self.projections: tuple[SourceProjection[TextFileSnapshotSource], ...] = (projection,)

    async def resolve(self, value: TextFileSnapshotCapture, /) -> TextFileSnapshotSource:
        content_bytes = value.content.encode(value.encoding)
        content_digest = f"sha256:{hashlib.sha256(content_bytes).hexdigest()}"
        source_id = _snapshot_id(value.namespace, value.path, content_digest)
        return TextFileSnapshotSource(
            name=source_id,
            materialization=SourceMaterialization.CAPTURED,
            description=f"Captured text file {value.path}",
            namespace=value.namespace,
            path=value.path,
            content=value.content,
            media_type=value.media_type,
            encoding=value.encoding,
            content_digest=content_digest,
            size=len(content_bytes),
            etag=value.etag,
            provider_version=value.provider_version,
            modified_at=value.modified_at,
        )

    async def read(self, source: TextFileSnapshotSource, /) -> TextFileSnapshotCapture:
        return TextFileSnapshotCapture(
            namespace=source.namespace,
            path=source.path,
            content=source.content,
            media_type=source.media_type,
            encoding=source.encoding,
            etag=source.etag,
            provider_version=source.provider_version,
            modified_at=source.modified_at,
        )


class TextFileEvidenceProjection:
    """Expose one file snapshot through the shared text-evidence capability."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = TextFileSnapshotSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: TextFileSnapshotSource, /) -> TextEvidence:
        metadata: dict[str, JsonValue] = {
            "namespace": source.namespace,
            "path": source.path,
            "media_type": source.media_type,
            "encoding": source.encoding,
            "content_digest": source.content_digest,
            "size": source.size,
        }
        if source.etag is not None:
            metadata["etag"] = source.etag
        if source.provider_version is not None:
            metadata["provider_version"] = source.provider_version
        if source.modified_at is not None:
            metadata["modified_at"] = source.modified_at.isoformat()
        return TextEvidence(
            source_type=TEXT_FILE_SNAPSHOT_SOURCE_NAME,
            source_id=source.name,
            content=source.content,
            metadata=metadata,
        )


def _snapshot_id(namespace: str, path: str, content_digest: str) -> str:
    identity = f"{namespace}\0{path}\0{content_digest}"
    return f"text_file_{hashlib.sha256(identity.encode()).hexdigest()}"


TEXT_FILE_SNAPSHOT_SOURCE_DEFINITION = TextFileSnapshotSourceDefinition()

__all__ = [
    "TEXT_FILE_SNAPSHOT_SOURCE_DEFINITION",
    "TEXT_FILE_SNAPSHOT_SOURCE_NAME",
    "TextFileEvidenceProjection",
    "TextFileSnapshotCapture",
    "TextFileSnapshotSource",
    "TextFileSnapshotSourceDefinition",
]
