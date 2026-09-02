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

"""Canonical capture and validation for standard Agent Skill packages."""

# Package validation deliberately reports precise bounded failures at the
# trust boundary instead of hiding them behind one generic message.
# ruff: noqa: TRY003

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import stat
import unicodedata
import zipfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml
from pydantic import BaseModel, ConfigDict, Field

from powercontext.builtin.artifacts.skill.models import SkillContent, SkillPackageRef

MAX_SKILL_PACKAGE_FILES = 256
MAX_SKILL_PACKAGE_BYTES = 4 * 1024 * 1024
MAX_SKILL_ARCHIVE_BYTES = 5 * 1024 * 1024
MAX_SKILL_MANIFEST_BYTES = 128 * 1024
MAX_SKILL_PATH_BYTES = 512
MAX_SKILL_ENTRYPOINT_BYTES = 128 * 1024
SKILL_ENTRYPOINT = "SKILL.md"
_CANONICAL_TREE_DOMAIN = b"powercontext.skill-package-tree.v1\0"
_FORBIDDEN_COMPONENTS = frozenset({".env", ".git", "node_modules"})
_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
# This mapping is part of the stored manifest contract. Keep it host-independent and append-only.
_MEDIA_TYPES_BY_SUFFIX = {
    ".css": "text/css",
    ".csv": "text/csv",
    ".gif": "image/gif",
    ".htm": "text/html",
    ".html": "text/html",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".js": "text/javascript",
    ".json": "application/json",
    ".md": "text/markdown",
    ".mjs": "text/javascript",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".py": "text/x-python",
    ".sh": "application/x-sh",
    ".svg": "image/svg+xml",
    ".txt": "text/plain",
    ".wasm": "application/wasm",
    ".xml": "text/xml",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
}


class SkillPackageError(ValueError):
    """Raised when a package cannot be captured without changing its meaning."""


class SkillPackageEntry(BaseModel):
    """One canonical regular file in a package manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(min_length=1, max_length=MAX_SKILL_PATH_BYTES)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0, le=MAX_SKILL_PACKAGE_BYTES)
    media_type: str = Field(min_length=1, max_length=255)
    mode: int


class SkillPackageMetadata(BaseModel):
    """Validated standard metadata derived from the exact entrypoint."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=64)
    description: str = Field(min_length=1, max_length=1_024)
    license: str | None = Field(default=None, min_length=1, max_length=512)
    compatibility: str | None = Field(default=None, min_length=1, max_length=500)
    metadata: dict[str, str] = Field(default_factory=dict)
    allowed_tools: str | None = Field(default=None, min_length=1, max_length=2_000)


@dataclass(frozen=True)
class SkillPackageSnapshot:
    """Canonical archive plus deterministic metadata needed by storage and Review."""

    reference: SkillPackageRef
    entries: tuple[SkillPackageEntry, ...]
    metadata: SkillPackageMetadata
    instructions: str
    archive_bytes: bytes

    @property
    def manifest_bytes(self) -> bytes:
        """Return deterministic JSON for the canonical file manifest."""

        values = [entry.model_dump(mode="json") for entry in self.entries]
        return json.dumps(values, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")

    def as_skill_content(self) -> SkillContent:
        """Return the package-backed managed Artifact content cache."""

        return SkillContent(
            name=self.metadata.name,
            description=self.metadata.description,
            instructions=self.instructions,
            package=self.reference,
            license=self.metadata.license,
            compatibility=self.metadata.compatibility,
            metadata=self.metadata.metadata,
            allowed_tools=self.metadata.allowed_tools,
        )


@dataclass(frozen=True)
class _PackageFile:
    path: str
    content: bytes
    mode: int


def capture_skill_directory(
    package: Path,
    /,
    *,
    expected_name: str | None = None,
) -> SkillPackageSnapshot:
    """Capture a stable local package directory using its owned logical name by default."""

    root = package.expanduser().resolve(strict=True)
    if not root.is_dir() or package.is_symlink():
        raise SkillPackageError("Agent Skill package must be a regular directory")
    return _canonical_snapshot(
        _directory_files(root), expected_name=root.name if expected_name is None else expected_name
    )


def capture_skill_archive(archive_bytes: bytes, /) -> SkillPackageSnapshot:
    """Validate an untrusted ZIP and rewrite it into canonical package bytes."""

    if not archive_bytes or len(archive_bytes) > MAX_SKILL_ARCHIVE_BYTES:
        raise SkillPackageError("Agent Skill archive exceeds the supported size")
    try:
        files = _archive_files(archive_bytes)
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise SkillPackageError("Agent Skill archive is invalid") from error
    return _canonical_snapshot(files)


def package_file(snapshot: SkillPackageSnapshot, path: str, /) -> bytes:
    """Read one exact regular file from a verified canonical snapshot."""

    canonical = _validate_relative_path(path)
    if canonical not in {entry.path for entry in snapshot.entries}:
        raise SkillPackageError(f"Agent Skill package file does not exist: {canonical}")
    try:
        with zipfile.ZipFile(io.BytesIO(snapshot.archive_bytes), "r") as archive:
            return archive.read(canonical)
    except (KeyError, OSError, RuntimeError, zipfile.BadZipFile) as error:
        raise SkillPackageError("stored Agent Skill archive is invalid") from error


def materialize_skill_package(snapshot: SkillPackageSnapshot, destination: Path, /) -> None:
    """Write exact package files into a new destination without following links."""

    if destination.exists() or destination.is_symlink():
        raise FileExistsError(destination)
    destination.mkdir(parents=True)
    try:
        with zipfile.ZipFile(io.BytesIO(snapshot.archive_bytes), "r") as archive:
            for entry in snapshot.entries:
                target = destination.joinpath(*PurePosixPath(entry.path).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                content = archive.read(entry.path)
                if hashlib.sha256(content).hexdigest() != entry.digest:
                    raise SkillPackageError(f"stored Agent Skill file digest does not match: {entry.path}")  # noqa: TRY301
                target.write_bytes(content)
                target.chmod(entry.mode)
    except BaseException:
        _remove_partial_tree(destination)
        raise


def build_instruction_skill_package(content: SkillContent, /) -> SkillPackageSnapshot:
    """Convert legacy or generated instruction content into a one-file standard package."""

    if content.package is not None:
        raise SkillPackageError("package-backed Skill content cannot be rebuilt from cached fields")
    frontmatter: dict[str, object] = {
        "name": content.name,
        "description": content.description,
    }
    if content.license is not None:
        frontmatter["license"] = content.license
    if content.compatibility is not None:
        frontmatter["compatibility"] = content.compatibility
    if content.metadata:
        frontmatter["metadata"] = content.metadata
    if content.allowed_tools is not None:
        frontmatter["allowed-tools"] = content.allowed_tools
    body = content.instructions.rstrip()
    if content.validation:
        validation = "\n".join(f"- {item}" for item in content.validation)
        body = f"{body}\n\n## Validation\n\n{validation}" if body else f"## Validation\n\n{validation}"
    manifest = yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False).rstrip()
    skill_markdown = f"---\n{manifest}\n---\n\n{body}\n".encode()
    return _canonical_snapshot((_PackageFile(SKILL_ENTRYPOINT, skill_markdown, 0o644),), expected_name=content.name)


def _directory_files(root: Path) -> tuple[_PackageFile, ...]:  # noqa: C901
    files: list[_PackageFile] = []
    seen_inodes: set[tuple[int, int]] = set()
    total_bytes = 0
    for path in sorted(root.rglob("*"), key=lambda value: value.relative_to(root).as_posix()):
        relative = _validate_relative_path(path.relative_to(root).as_posix())
        try:
            file_stat = path.lstat()
        except OSError as error:
            raise SkillPackageError(f"Agent Skill package file is unreadable: {relative}") from error
        if stat.S_ISLNK(file_stat.st_mode):
            raise SkillPackageError(f"Agent Skill package contains a symbolic link: {relative}")
        if stat.S_ISDIR(file_stat.st_mode):
            continue
        if not stat.S_ISREG(file_stat.st_mode):
            raise SkillPackageError(f"Agent Skill package contains a special file: {relative}")
        inode = (file_stat.st_dev, file_stat.st_ino)
        if file_stat.st_nlink > 1 or inode in seen_inodes:
            raise SkillPackageError(f"Agent Skill package contains a hard link: {relative}")
        if len(files) >= MAX_SKILL_PACKAGE_FILES:
            raise SkillPackageError("Agent Skill package has an unsupported file count")
        remaining = MAX_SKILL_PACKAGE_BYTES - total_bytes
        try:
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            with os.fdopen(descriptor, "rb") as stream:
                before = os.fstat(stream.fileno())
                if not _same_file(file_stat, before) or not stat.S_ISREG(before.st_mode) or before.st_nlink > 1:
                    raise SkillPackageError(f"Agent Skill package changed during capture: {relative}")
                content = stream.read(remaining + 1)
                after = os.fstat(stream.fileno())
        except OSError as error:
            raise SkillPackageError(f"Agent Skill package file is unreadable: {relative}") from error
        if _changed_during_read(before, after):
            raise SkillPackageError(f"Agent Skill package changed during capture: {relative}")
        if len(content) > remaining:
            raise SkillPackageError("Agent Skill package exceeds the supported uncompressed size")
        seen_inodes.add((before.st_dev, before.st_ino))
        total_bytes += len(content)
        files.append(_PackageFile(relative, content, _normalized_mode(before.st_mode)))
    return tuple(files)


def _archive_files(archive_bytes: bytes) -> tuple[_PackageFile, ...]:  # noqa: C901
    files: list[_PackageFile] = []
    seen_paths: set[str] = set()
    with zipfile.ZipFile(io.BytesIO(archive_bytes), "r") as archive:
        entries: list[tuple[zipfile.ZipInfo, str, int]] = []
        total_bytes = 0
        for info in archive.infolist():
            path = _validate_relative_path(info.filename.rstrip("/") if info.is_dir() else info.filename)
            collision_key = _path_collision_key(path)
            if collision_key in seen_paths:
                raise SkillPackageError(f"Agent Skill archive contains duplicate or colliding paths: {path}")
            seen_paths.add(collision_key)
            if info.flag_bits & 0x1:
                raise SkillPackageError(f"Agent Skill archive contains an encrypted entry: {path}")
            mode = info.external_attr >> 16
            if info.is_dir():
                continue
            file_type = stat.S_IFMT(mode)
            if file_type not in {0, stat.S_IFREG}:
                raise SkillPackageError(f"Agent Skill archive contains a non-regular entry: {path}")
            if info.file_size > MAX_SKILL_PACKAGE_BYTES:
                raise SkillPackageError(f"Agent Skill archive entry exceeds the supported size: {path}")
            if len(entries) >= MAX_SKILL_PACKAGE_FILES:
                raise SkillPackageError("Agent Skill package has an unsupported file count")
            total_bytes += info.file_size
            if total_bytes > MAX_SKILL_PACKAGE_BYTES:
                raise SkillPackageError("Agent Skill package exceeds the supported uncompressed size")
            entries.append((info, path, mode))
        for info, path, mode in entries:
            with archive.open(info, "r") as stream:
                content = stream.read(info.file_size + 1)
            if len(content) != info.file_size:
                raise SkillPackageError(f"Agent Skill archive entry size does not match: {path}")
            files.append(_PackageFile(path, content, _normalized_mode(mode)))
    return tuple(files)


def _canonical_snapshot(
    values: Iterable[_PackageFile],
    *,
    expected_name: str | None = None,
) -> SkillPackageSnapshot:
    files = tuple(sorted(values, key=lambda value: value.path))
    if not files or len(files) > MAX_SKILL_PACKAGE_FILES:
        raise SkillPackageError("Agent Skill package has an unsupported file count")
    paths: dict[str, str] = {}
    total_bytes = 0
    entries: list[SkillPackageEntry] = []
    for value in files:
        path = _validate_relative_path(value.path)
        collision_key = _path_collision_key(path)
        if collision_key in paths:
            raise SkillPackageError(f"Agent Skill package contains colliding paths: {paths[collision_key]} and {path}")
        paths[collision_key] = path
        total_bytes += len(value.content)
        if total_bytes > MAX_SKILL_PACKAGE_BYTES:
            raise SkillPackageError("Agent Skill package exceeds the supported uncompressed size")
        entries.append(
            SkillPackageEntry(
                path=path,
                digest=hashlib.sha256(value.content).hexdigest(),
                size=len(value.content),
                media_type=_media_type(path),
                mode=value.mode,
            )
        )
    try:
        entrypoint = files[[value.path for value in files].index(SKILL_ENTRYPOINT)].content
    except ValueError:
        raise SkillPackageError("Agent Skill package must contain SKILL.md at its root") from None
    metadata, instructions = _parse_skill_markdown(entrypoint, expected_name=expected_name)
    tree_digest = _tree_digest(tuple(entries))
    archive_bytes = _canonical_archive(files)
    if len(archive_bytes) > MAX_SKILL_ARCHIVE_BYTES:
        raise SkillPackageError("canonical Agent Skill archive exceeds the supported size")
    reference = SkillPackageRef(
        tree_digest=tree_digest,
        archive_digest=hashlib.sha256(archive_bytes).hexdigest(),
        file_count=len(files),
        uncompressed_size=total_bytes,
        archive_size=len(archive_bytes),
    )
    snapshot = SkillPackageSnapshot(
        reference=reference,
        entries=tuple(entries),
        metadata=metadata,
        instructions=instructions,
        archive_bytes=archive_bytes,
    )
    if len(snapshot.manifest_bytes) > MAX_SKILL_MANIFEST_BYTES:
        raise SkillPackageError("Agent Skill package manifest exceeds the supported size")
    return snapshot


def _parse_skill_markdown(  # noqa: C901
    content: bytes,
    *,
    expected_name: str | None,
) -> tuple[SkillPackageMetadata, str]:
    if not content or len(content) > MAX_SKILL_ENTRYPOINT_BYTES:
        raise SkillPackageError("Agent Skill SKILL.md exceeds the supported size")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SkillPackageError("Agent Skill SKILL.md must be UTF-8") from error
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise SkillPackageError("Agent Skill SKILL.md is missing YAML frontmatter")
    closing = next((index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
    if closing is None:
        raise SkillPackageError("Agent Skill SKILL.md frontmatter is not terminated")
    try:
        parsed = yaml.safe_load("".join(lines[1:closing]))
    except yaml.YAMLError as error:
        raise SkillPackageError("Agent Skill SKILL.md frontmatter is invalid YAML") from error
    if not isinstance(parsed, Mapping) or any(not isinstance(key, str) for key in parsed):
        raise SkillPackageError("Agent Skill SKILL.md frontmatter must be a string-keyed mapping")
    name = _required_string(parsed, "name", maximum=64)
    if _SKILL_NAME.fullmatch(name) is None:
        raise SkillPackageError("Agent Skill name must contain lowercase letters, digits, and single hyphens")
    if expected_name is not None and name != expected_name:
        raise SkillPackageError("Agent Skill name must match its package directory")
    description = _required_string(parsed, "description", maximum=1_024)
    license_name = _optional_string(parsed, "license", maximum=512)
    compatibility = _optional_string(parsed, "compatibility", maximum=500)
    allowed_tools = _optional_string(parsed, "allowed-tools", maximum=2_000)
    raw_metadata = parsed.get("metadata", {})
    if not isinstance(raw_metadata, Mapping) or any(
        not isinstance(key, str) or not isinstance(value, str) for key, value in raw_metadata.items()
    ):
        raise SkillPackageError("Agent Skill metadata must map strings to strings")
    metadata = dict(raw_metadata)
    if len(metadata) > 64:
        raise SkillPackageError("Agent Skill metadata must not exceed 64 entries")
    for key, value in metadata.items():
        if not key.strip() or key != key.strip() or len(key) > 128:
            raise SkillPackageError("Agent Skill metadata keys must be trimmed and at most 128 characters")
        if value != value.strip() or len(value) > 2_000:
            raise SkillPackageError("Agent Skill metadata values must be trimmed and at most 2000 characters")
    instructions = "".join(lines[closing + 1 :]).lstrip("\r\n").rstrip()
    return (
        SkillPackageMetadata(
            name=name,
            description=description,
            license=license_name,
            compatibility=compatibility,
            metadata=metadata,
            allowed_tools=allowed_tools,
        ),
        instructions,
    )


def _required_string(values: Mapping[str, object], field: str, *, maximum: int) -> str:
    value = values.get(field)
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > maximum:
        raise SkillPackageError(
            f"Agent Skill {field} must be a non-empty trimmed string of at most {maximum} characters"
        )
    return value


def _optional_string(values: Mapping[str, object], field: str, *, maximum: int) -> str | None:
    value = values.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or value != value.strip() or len(value) > maximum:
        raise SkillPackageError(
            f"Agent Skill {field} must be a non-empty trimmed string of at most {maximum} characters"
        )
    return value


def _validate_relative_path(value: str) -> str:
    if not value or "\x00" in value or "\\" in value:
        raise SkillPackageError("Agent Skill package contains an invalid path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SkillPackageError(f"Agent Skill package path is not relative: {value}")
    if any(part in _FORBIDDEN_COMPONENTS for part in path.parts):
        raise SkillPackageError(f"Agent Skill package contains a forbidden path: {value}")
    canonical = path.as_posix()
    if unicodedata.normalize("NFC", canonical) != canonical:
        raise SkillPackageError(f"Agent Skill package path must use NFC Unicode normalization: {value}")
    if len(canonical.encode("utf-8")) > MAX_SKILL_PATH_BYTES:
        raise SkillPackageError(f"Agent Skill package path exceeds the supported size: {value}")
    return canonical


def _path_collision_key(value: str) -> str:
    return unicodedata.normalize("NFC", value).casefold()


def _normalized_mode(value: int) -> int:
    executable = value & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return 0o755 if executable else 0o644


def _tree_digest(entries: tuple[SkillPackageEntry, ...]) -> str:
    digest = hashlib.sha256(_CANONICAL_TREE_DOMAIN)
    for entry in entries:
        path = entry.path.encode("utf-8")
        digest.update(len(path).to_bytes(4, "big"))
        digest.update(path)
        digest.update(entry.mode.to_bytes(4, "big"))
        digest.update(entry.size.to_bytes(8, "big"))
        digest.update(bytes.fromhex(entry.digest))
    return digest.hexdigest()


def _canonical_archive(files: tuple[_PackageFile, ...]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9, strict_timestamps=True
    ) as archive:
        for value in files:
            info = zipfile.ZipInfo(value.path, date_time=_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | value.mode) << 16
            info.flag_bits |= 0x800
            archive.writestr(info, value.content, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return output.getvalue()


def _changed_during_read(before: os.stat_result, after: os.stat_result) -> bool:
    return (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )


def _same_file(checked: os.stat_result, opened: os.stat_result) -> bool:
    return (checked.st_dev, checked.st_ino) == (opened.st_dev, opened.st_ino)


def _media_type(path: str) -> str:
    return _MEDIA_TYPES_BY_SUFFIX.get(PurePosixPath(path).suffix.casefold(), "application/octet-stream")


def _remove_partial_tree(path: Path) -> None:
    for child in sorted(path.rglob("*"), key=lambda value: len(value.parts), reverse=True):
        if child.is_dir() and not child.is_symlink():
            child.rmdir()
        else:
            child.unlink(missing_ok=True)
    path.rmdir()


__all__ = [
    "MAX_SKILL_ARCHIVE_BYTES",
    "MAX_SKILL_ENTRYPOINT_BYTES",
    "MAX_SKILL_MANIFEST_BYTES",
    "MAX_SKILL_PACKAGE_BYTES",
    "MAX_SKILL_PACKAGE_FILES",
    "MAX_SKILL_PATH_BYTES",
    "SKILL_ENTRYPOINT",
    "SkillPackageEntry",
    "SkillPackageError",
    "SkillPackageMetadata",
    "SkillPackageSnapshot",
    "build_instruction_skill_package",
    "capture_skill_archive",
    "capture_skill_directory",
    "materialize_skill_package",
    "package_file",
]
