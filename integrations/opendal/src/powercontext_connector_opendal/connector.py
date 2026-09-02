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

"""Capture UTF-8 text files through the OpenDAL fsspec implementation."""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import mimetypes
import posixpath
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from importlib import import_module
from typing import Any, Literal, Protocol

from powercontext.errors import InvalidConnectorRunError
from powercontext.sources import (
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
)
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    field_validator,
)

from powercontext_connector_opendal.source import (
    TEXT_FILE_SNAPSHOT_SOURCE_NAME,
    TextFileSnapshotCapture,
)

OPENDAL_TEXT_FILE_CONNECTOR_NAME = "opendal-text-files"
_DEFAULT_PATTERNS = ("**/*.md", "**/*.markdown", "**/*.txt", "**/*.rst", "**/*.adoc")
DEFAULT_MAX_FILE_SIZE = 256 * 1024


class _FsspecFileSystem(Protocol):
    def find(self, path: str, *, detail: bool) -> Mapping[str, Mapping[str, object]]: ...

    def cat_file(self, path: str) -> bytes: ...


class OpenDALTextFileCheckpoint(BaseModel):
    """Opaque content-digest checkpoint for one Connector binding."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal["1"] = "1"
    files: dict[str, str] = Field(default_factory=dict)

    @field_validator("files")
    @classmethod
    def validate_files(cls, value: dict[str, str]) -> dict[str, str]:
        for path, digest in value.items():
            _validate_relative_path(path)
            if not digest.startswith("sha256:") or len(digest) != 71:
                raise ValueError("checkpoint digests must use sha256:<hex>")  # noqa: TRY003
            try:
                int(digest.removeprefix("sha256:"), 16)
            except ValueError as error:
                raise ValueError("checkpoint digest must contain lowercase hexadecimal") from error  # noqa: TRY003
            if digest != digest.lower():
                raise ValueError("checkpoint digest must contain lowercase hexadecimal")  # noqa: TRY003
        return value


class OpenDALTextFileConnector:
    """Perform bounded full scans through an OpenDAL-backed fsspec filesystem."""

    name = OPENDAL_TEXT_FILE_CONNECTOR_NAME
    version = "1"
    source_definitions = frozenset({TEXT_FILE_SNAPSHOT_SOURCE_NAME})

    def __init__(
        self,
        filesystem: _FsspecFileSystem,
        *,
        source_namespace: str,
        root: str = "",
        patterns: Sequence[str] = _DEFAULT_PATTERNS,
        max_files: int = 10_000,
        max_file_size: int = DEFAULT_MAX_FILE_SIZE,
    ) -> None:
        if not source_namespace or source_namespace.strip() != source_namespace:
            raise ValueError("source_namespace must be a non-empty trimmed string")  # noqa: TRY003
        if max_files < 1:
            raise ValueError("max_files must be positive")  # noqa: TRY003
        if max_file_size < 1:
            raise ValueError("max_file_size must be positive")  # noqa: TRY003
        if isinstance(patterns, str):
            raise TypeError("patterns must be a sequence of glob patterns")  # noqa: TRY003
        normalized_patterns = tuple(patterns)
        if not normalized_patterns or any(not pattern or pattern.strip() != pattern for pattern in normalized_patterns):
            raise ValueError("patterns must contain non-empty trimmed values")  # noqa: TRY003
        self._filesystem = filesystem
        self._source_namespace = source_namespace
        self._root = _normalize_root(root)
        self._patterns = normalized_patterns
        self._max_files = max_files
        self._max_file_size = max_file_size

    @classmethod
    def from_service(
        cls,
        service: str,
        *,
        source_namespace: str,
        root: str = "",
        storage_options: Mapping[str, object] | None = None,
        patterns: Sequence[str] = _DEFAULT_PATTERNS,
        max_files: int = 10_000,
        max_file_size: int = DEFAULT_MAX_FILE_SIZE,
    ) -> OpenDALTextFileConnector:
        """Create a Connector from one OpenDAL service and its runtime-only options."""

        try:
            opendalfs = import_module("opendalfs")
        except ImportError as error:
            raise ImportError(  # noqa: TRY003
                "OpenDALTextFileConnector.from_service requires powercontext-connector-opendal on Python 3.12+"
            ) from error
        backend_options: dict[str, Any] = dict(storage_options or {})
        filesystem = opendalfs.OpendalFileSystem(
            scheme=service,
            asynchronous=False,
            skip_instance_cache=True,
            **backend_options,
        )
        return cls(
            filesystem,
            source_namespace=source_namespace,
            root=root,
            patterns=patterns,
            max_files=max_files,
            max_file_size=max_file_size,
        )

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        previous = _checkpoint(session.checkpoint)
        entries = await asyncio.to_thread(self._filesystem.find, self._root, detail=True)
        files = self._selected_files(entries)
        if len(files) > self._max_files:
            raise InvalidConnectorRunError(
                "file-limit",
                f"scan selected {len(files)} files, maximum is {self._max_files}",
            )

        current_files: dict[str, str] = {}
        for relative_path, storage_path, info in files:
            size = _non_negative_int(info.get("size"))
            if size is not None and size > self._max_file_size:
                session.reject(
                    relative_path,
                    TEXT_FILE_SNAPSHOT_SOURCE_NAME,
                    f"file exceeds {self._max_file_size} bytes",
                )
                continue
            try:
                content_bytes = await asyncio.to_thread(self._filesystem.cat_file, storage_path)
            except Exception as error:
                session.fail(relative_path, TEXT_FILE_SNAPSHOT_SOURCE_NAME, type(error).__name__)
                continue
            if not isinstance(content_bytes, bytes):
                session.fail(relative_path, TEXT_FILE_SNAPSHOT_SOURCE_NAME, "filesystem returned non-bytes content")
                continue
            if len(content_bytes) > self._max_file_size:
                session.reject(
                    relative_path,
                    TEXT_FILE_SNAPSHOT_SOURCE_NAME,
                    f"file exceeds {self._max_file_size} bytes",
                )
                continue

            content_digest = f"sha256:{hashlib.sha256(content_bytes).hexdigest()}"
            current_files[relative_path] = content_digest
            if previous.files.get(relative_path) == content_digest:
                continue
            try:
                content = content_bytes.decode("utf-8")
            except UnicodeDecodeError:
                session.reject(relative_path, TEXT_FILE_SNAPSHOT_SOURCE_NAME, "file is not valid UTF-8")
                continue
            capture = TextFileSnapshotCapture(
                namespace=self._source_namespace,
                path=relative_path,
                content=content,
                media_type=mimetypes.guess_type(relative_path)[0] or "text/plain",
                etag=_optional_string(info.get("etag")),
                provider_version=_optional_string(info.get("version")),
                modified_at=_optional_datetime(info.get("mtime")),
            )
            await session.submit(relative_path, TEXT_FILE_SNAPSHOT_SOURCE_NAME, capture)

        checkpoint = OpenDALTextFileCheckpoint(files=current_files)
        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint=checkpoint.model_dump(mode="json"),
        )

    def _selected_files(
        self,
        entries: Mapping[str, Mapping[str, object]],
    ) -> tuple[tuple[str, str, Mapping[str, object]], ...]:
        selected: list[tuple[str, str, Mapping[str, object]]] = []
        for storage_path, info in entries.items():
            if info.get("type") != "file":
                continue
            relative_path = _relative_path(storage_path, self._root)
            if not _matches(relative_path, self._patterns):
                continue
            selected.append((relative_path, storage_path, info))
        selected.sort(key=lambda item: item[0])
        return tuple(selected)


def _checkpoint(value: JsonValue | None) -> OpenDALTextFileCheckpoint:
    if value is None:
        return OpenDALTextFileCheckpoint()
    try:
        return OpenDALTextFileCheckpoint.model_validate(value)
    except ValidationError as error:
        raise InvalidConnectorRunError("checkpoint", "does not match OpenDALTextFileCheckpoint") from error


def _normalize_root(value: str) -> str:
    if value != value.strip() or "\\" in value:
        raise ValueError("root must be a normalized POSIX path")  # noqa: TRY003
    normalized = posixpath.normpath(value).strip("/")
    if normalized in {"", "."}:
        return ""
    _validate_relative_path(normalized)
    return normalized


def _relative_path(storage_path: str, root: str) -> str:
    normalized = posixpath.normpath(storage_path).strip("/")
    relative = posixpath.relpath(normalized, root) if root else normalized
    _validate_relative_path(relative)
    return relative


def _validate_relative_path(value: str) -> None:
    if not value or value.startswith("/") or "\\" in value:
        raise ValueError("file path must be a relative POSIX path")  # noqa: TRY003
    normalized = posixpath.normpath(value)
    if normalized != value or normalized == ".." or normalized.startswith("../"):
        raise ValueError("file path escapes the configured root")  # noqa: TRY003


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(
        fnmatch.fnmatchcase(path, pattern)
        or (pattern.startswith("**/") and fnmatch.fnmatchcase(path, pattern.removeprefix("**/")))
        for pattern in patterns
    )


def _non_negative_int(value: object) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None
    return value


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value and value.strip() == value else None


def _optional_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, int | float) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, tz=UTC)
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


__all__ = [
    "OPENDAL_TEXT_FILE_CONNECTOR_NAME",
    "OpenDALTextFileCheckpoint",
    "OpenDALTextFileConnector",
]
