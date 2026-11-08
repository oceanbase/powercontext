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

"""Bounded UTF-8 file acquisition without following links out of a selected tree."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, field_validator

from powercontext.client.capture import known_secret_values, redact_known_secrets

from .definition import MemoryCapture

SUPPORTED_SUFFIXES = frozenset({".md", ".markdown", ".txt"})


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codex_home: Path
    namespace: str = Field(min_length=1, max_length=128)
    poll_interval: float = Field(default=30, gt=0)
    max_file_bytes: int = Field(default=262_144, gt=0, le=1_048_576)
    max_total_bytes: int = Field(default=16_777_216, gt=0, le=67_108_864)
    max_files: int = Field(default=1000, gt=0, le=10_000)
    max_entries: int = Field(default=10_000, gt=0, le=100_000)

    @field_validator("codex_home")
    @classmethod
    def absolute_home(cls, value: Path) -> Path:
        value = value.expanduser()
        if not value.is_absolute():
            raise ValueError("codex_home must be an absolute path on the execution machine")  # noqa: TRY003
        return value

    @field_validator("namespace")
    @classmethod
    def stable_namespace(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("namespace must be nonblank and trimmed")  # noqa: TRY003
        return value


class MemoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(default=".", min_length=1, max_length=2048)

    @field_validator("path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or "\\" in value or ":" in value or "\0" in value:
            raise ValueError("path must stay inside the configured memories directory")  # noqa: TRY003
        return path.as_posix()


class MemoryFileError(ValueError):
    """A bounded reason; never carries file contents or external error text."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class MemoryScan:
    captures: tuple[MemoryCapture, ...]
    failures: tuple[tuple[str, str], ...]
    skipped: tuple[str, ...]


def selection(config: Config, value: MemoryInput) -> tuple[Path, Path]:
    configured_root = config.codex_home / "memories"
    if configured_root.is_symlink():
        raise MemoryFileError("symlink_unsupported")
    root = configured_root.resolve()
    path = root
    for part in PurePosixPath(value.path).parts:
        path = path / part
        if path.is_symlink():
            raise MemoryFileError("symlink_unsupported")
    if not path.resolve().is_relative_to(root):
        raise MemoryFileError("path_outside_memories")
    return root, path


def _paths(config: Config, root: Path, selected: Path) -> tuple[Path, ...]:  # noqa: C901 - bounded link-safe traversal.
    if selected.is_file():
        if selected.suffix.lower() not in SUPPORTED_SUFFIXES:
            raise MemoryFileError("unsupported_format")
        return (selected,)
    if not selected.is_dir():
        raise MemoryFileError("missing_target")
    pending = [selected]
    files: list[Path] = []
    entries = 0
    while pending:
        directory = pending.pop()
        descriptor = _open_directory(root, directory)
        try:
            with os.scandir(descriptor) as stream:
                for entry in stream:
                    entries += 1
                    if entries > config.max_entries:
                        raise MemoryFileError("too_many_entries")
                    if entry.is_symlink():
                        raise MemoryFileError("symlink_unsupported")
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(directory / entry.name)
                    elif entry.is_file(follow_symlinks=False) and Path(entry.name).suffix.lower() in SUPPORTED_SUFFIXES:
                        files.append(directory / entry.name)
                        if len(files) > config.max_files:
                            raise MemoryFileError("too_many_files")
        finally:
            os.close(descriptor)
    return tuple(sorted(files, key=lambda path: path.relative_to(root).as_posix()))


def _open_directory(root: Path, path: Path) -> int:
    # openat with O_NOFOLLOW on every component prevents a link swap between
    # discovery and reading. Collection is supported on Unix execution machines.
    if not hasattr(os, "O_NOFOLLOW"):
        raise MemoryFileError("platform_unsupported")
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.relative_to(root).parts:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
    except BaseException:
        os.close(directory)
        raise
    return directory


def _read(root: Path, path: Path, limit: int) -> bytes:
    directory = _open_directory(root, path.parent)
    try:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, "rb") as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise MemoryFileError("not_regular_file")
            if before.st_size > limit:
                raise MemoryFileError("file_too_large")
            raw = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
            if len(raw) > limit:
                raise MemoryFileError("file_too_large")
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise MemoryFileError("file_changed_during_read")
            return raw
    finally:
        os.close(directory)


def scan(config: Config, value: MemoryInput) -> MemoryScan:
    failures: list[tuple[str, str]] = []
    skipped: list[str] = []
    captures: list[MemoryCapture] = []
    try:
        root, selected = selection(config, value)
        paths = _paths(config, root, selected)
    except MemoryFileError as error:
        return MemoryScan((), ((value.path, error.reason),), ())
    except OSError:
        return MemoryScan((), ((value.path, "unreadable_target"),), ())
    secrets = known_secret_values(codex_home=config.codex_home)
    total = 0
    for path in paths:
        relative = path.relative_to(root).as_posix()
        read_limit = min(config.max_file_bytes, config.max_total_bytes - total)
        try:
            raw = _read(root, path, read_limit)
        except MemoryFileError as error:
            total += read_limit
            failures.append((relative, error.reason))
            continue
        except OSError:
            total += read_limit
            failures.append((relative, "unreadable_file"))
            continue
        total += len(raw)
        try:
            text = raw.decode("utf-8-sig")
            if "\0" in text:
                failures.append((relative, "binary_content"))
                continue
            content = redact_known_secrets(text, secrets=secrets)
            if not content.strip():
                skipped.append(relative)
                continue
            captures.append(
                MemoryCapture(
                    namespace=config.namespace,
                    relative_path=relative,
                    original_sha256=sha256(raw).hexdigest(),
                    content=content,
                    redaction_applied=content != text,
                )
            )
        except UnicodeError:
            failures.append((relative, "invalid_utf8"))
        except OSError:
            failures.append((relative, "unreadable_file"))
    return MemoryScan(tuple(captures), tuple(failures), tuple(skipped))
