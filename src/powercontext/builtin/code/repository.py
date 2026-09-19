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

"""Capture Git working tree bytes without following links or executing code."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from time import monotonic
from typing import Literal

from powercontext.builtin.code.config import CodeConfig
from powercontext.builtin.code.errors import (
    CodeUnavailableError,
    InvalidCodeRequestError,
    UnsupportedCodeCapabilityError,
)
from powercontext.builtin.code.models import CodeValue, relative_path
from powercontext.builtin.code.process import run_process

_EXCLUDED_DIRECTORIES = frozenset({
    ".git",
    ".powercontext",
    ".codegraph",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".ssh",
    ".aws",
    "dist",
    "build",
})
_CREDENTIAL_NAMES = frozenset({"credentials", "credentials.json", ".git-credentials", "id_rsa", "id_ed25519"})


class _NonRegularFileError(OSError):
    """A captured path is a link, directory, or other non-regular file."""


class CapturedFile(CodeValue):
    path: str
    git_mode: str
    index_oid: str
    sha256: str
    size: int


class RepositorySnapshot(CodeValue):
    commit: str
    git_object_format: Literal["sha1", "sha256"]
    dirty: bool
    change_digest: str
    files: tuple[CapturedFile, ...]
    omissions: dict[str, int]

    def content_digest(self) -> str:
        """Bind the exact captured bytes, Git identity, and included changes."""

        wire = json.dumps(self.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(wire.encode()).hexdigest()


@dataclass(frozen=True)
class _GitEntry:
    path: str
    mode: str
    oid: str


@dataclass(frozen=True)
class _GitState:
    commit: str
    object_format: Literal["sha1", "sha256"]
    entries: tuple[_GitEntry, ...]
    changes: dict[str, bytes]


async def capture_repository(
    root: Path,
    config: CodeConfig,
    *,
    deadline: float,
    destination: Path | None = None,
) -> RepositorySnapshot:
    """Capture or check the included working tree, retrying concurrent edits."""

    root = await asyncio.to_thread(root.absolute)
    if os.name != "posix" or os.open not in os.supports_dir_fd:
        raise UnsupportedCodeCapabilityError("unsupported_capability")
    try:
        top = await _git(root, ("rev-parse", "--show-toplevel"), config, deadline)
        if top.removesuffix(b"\n").decode("utf-8") != str(root):
            raise InvalidCodeRequestError("code_root_must_be_repository")
        for attempt in range(config.limits.capture_attempts):
            before = await _git_state(root, config, deadline)
            snapshot = await asyncio.to_thread(_capture_files, root, before, config, destination, deadline)
            after = await _git_state(root, config, deadline)
            if before == after:
                verified = await asyncio.to_thread(_capture_files, root, after, config, None, deadline)
                if snapshot == verified:
                    return snapshot
            if destination is not None:
                await asyncio.to_thread(_discard_capture, destination, snapshot.files, deadline)
            if attempt + 1 == config.limits.capture_attempts:
                raise CodeUnavailableError("code_capture_changed")
    except (OSError, UnicodeError):
        raise CodeUnavailableError("code_repository_unavailable") from None
    raise CodeUnavailableError("code_capture_changed")


async def _git(root: Path, arguments: tuple[str, ...], config: CodeConfig, deadline: float) -> bytes:
    return await run_process(
        ("git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", str(root), *arguments),
        cwd=root,
        deadline=deadline,
        max_output_bytes=config.limits.max_files * 4352,
    )


def _discard_capture(destination: Path, files: tuple[CapturedFile, ...], deadline: float) -> None:
    """Remove only bytes written by an inconsistent attempt before retrying."""

    for file in files:
        _check_deadline(deadline)
        (destination / file.path).unlink(missing_ok=True)


async def _git_state(root: Path, config: CodeConfig, deadline: float) -> _GitState:
    commit, object_format, tracked = await asyncio.gather(
        _git(root, ("rev-parse", "--verify", "HEAD"), config, deadline),
        _git(root, ("rev-parse", "--show-object-format"), config, deadline),
        _git(root, ("ls-files", "--stage", "--full-name", "-z"), config, deadline),
    )
    kind = object_format.strip().decode("ascii")
    if kind not in {"sha1", "sha256"}:
        raise UnsupportedCodeCapabilityError("unsupported_capability")
    entries: list[_GitEntry] = []
    for record in tracked.split(b"\0"):
        if not record:
            continue
        prefix, path = record.split(b"\t", 1)
        mode, oid, stage = prefix.split()
        if stage != b"0":
            raise CodeUnavailableError("code_repository_unmerged")
        entries.append(
            _GitEntry(path.decode("utf-8", errors="surrogateescape"), mode.decode("ascii"), oid.decode("ascii"))
        )
    # Read object/index metadata, never `status` or a worktree diff: those can
    # execute repository clean/process filters when comparing equal-size files.
    tree = await _git(root, ("ls-tree", "-r", "-z", "--full-tree", commit.strip().decode("ascii")), config, deadline)
    head_entries = {}
    for record in tree.split(b"\0"):
        if record:
            prefix, raw_path = record.split(b"\t", 1)
            mode, _kind, oid = prefix.split()
            path = raw_path.decode("utf-8", errors="surrogateescape")
            head_entries[path] = _GitEntry(path, mode.decode("ascii"), oid.decode("ascii"))
    indexed = {entry.path: entry for entry in entries}
    changes = {
        path: b"D " if path not in indexed else b"A " if path not in head_entries else b"M "
        for path in indexed.keys() | head_entries.keys()
        if indexed.get(path) != head_entries.get(path)
    }
    if config.include_untracked:
        untracked = await _git(root, ("ls-files", "--others", "--exclude-standard", "-z"), config, deadline)
        entries.extend(
            _GitEntry(path.decode("utf-8", errors="surrogateescape"), "100644", "")
            for path in untracked.split(b"\0")
            if path
        )
    return _GitState(
        commit=commit.strip().decode("ascii"),
        object_format="sha256" if kind == "sha256" else "sha1",
        entries=tuple(sorted(entries, key=lambda entry: entry.path)),
        changes=changes,
    )


def _excluded(path: str, config: CodeConfig) -> bool:
    parts = path.split("/")
    name = parts[-1]
    return (
        any(part in _EXCLUDED_DIRECTORIES for part in parts[:-1])
        or name in _CREDENTIAL_NAMES
        or name == ".env"
        or name.startswith(".env.")
        or name.endswith((".pem", ".key", ".p12", ".pfx"))
        or any(fnmatchcase(path, pattern) or fnmatchcase(name, pattern) for pattern in config.exclude)
    )


def _capture_files(
    root: Path,
    state: _GitState,
    config: CodeConfig,
    destination: Path | None,
    deadline: float,
) -> RepositorySnapshot:
    files: list[CapturedFile] = []
    omissions: Counter[str] = Counter()
    worktree_changes: dict[str, str] = {}
    total = 0
    with _directory_descriptor(root) as descriptor:
        for entry in state.entries:
            _check_deadline(deadline)
            reason = _omission_reason(entry, config)
            if reason is not None:
                omissions[reason] += 1
                continue
            try:
                captured = _read_file(descriptor, entry.path, config.limits.max_file_bytes)
            except _NonRegularFileError:
                omissions["symlink_or_submodule"] += 1
                continue
            except FileNotFoundError:
                omissions["deleted"] += 1
                worktree_changes[entry.path] = "D"
                continue
            if captured is None:
                omissions["file_size_or_encoding"] += 1
                continue
            content, git_mode = captured
            if content.startswith(b"version https://git-lfs.github.com/spec/v1\n"):
                omissions["lfs_pointer"] += 1
                continue
            if len(files) >= config.limits.max_files or total + len(content) > config.limits.max_content_bytes:
                raise CodeUnavailableError("code_input_limit")
            total += len(content)
            blob = hashlib.new(state.object_format, usedforsecurity=False)
            blob.update(f"blob {len(content)}\0".encode())
            blob.update(content)
            if entry.oid and (blob.hexdigest() != entry.oid or git_mode != entry.mode):
                worktree_changes[entry.path] = "M"
            file = CapturedFile(
                path=entry.path,
                git_mode=git_mode,
                index_oid=entry.oid,
                sha256=hashlib.sha256(content).hexdigest(),
                size=len(content),
            )
            if destination is not None:
                target = destination / entry.path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                target.chmod(0o600)
            files.append(file)
    changes = _included_changes(state, files, config, worktree_changes)
    _check_deadline(deadline)
    return RepositorySnapshot(
        commit=state.commit,
        git_object_format=state.object_format,
        dirty=bool(changes),
        change_digest=hashlib.sha256(json.dumps(changes, sort_keys=True).encode()).hexdigest(),
        files=tuple(files),
        omissions=dict(omissions),
    )


def _included_changes(
    state: _GitState,
    files: list[CapturedFile],
    config: CodeConfig,
    worktree_changes: dict[str, str],
) -> dict[str, str]:
    included = {file.path: file for file in files}
    changes = {
        path: state.changes.get(path, b"??" if not file.index_oid else b"").decode("ascii")
        for path, file in included.items()
        if path in state.changes or not file.index_oid
    }
    # Deletions have no bytes to index but still invalidate previous answers.
    for path, status_code in state.changes.items():
        if b"D" in status_code and _omission_reason(_GitEntry(path, "100644", ""), config) is None:
            changes[path] = status_code.decode("ascii")
    for path, status_code in worktree_changes.items():
        changes[path] = changes.get(path, "  ")[0] + status_code
    return changes


def _omission_reason(entry: _GitEntry, config: CodeConfig) -> str | None:
    if entry.mode not in {"100644", "100755"}:
        return "symlink_or_submodule"
    try:
        relative_path(entry.path)
    except InvalidCodeRequestError:
        return "unrepresentable_path"
    if _excluded(entry.path, config):
        return "excluded"
    if not entry.path.endswith(".py"):
        return "unsupported_language"
    return None


def _check_deadline(deadline: float) -> None:
    if monotonic() >= deadline:
        raise CodeUnavailableError("code_timeout")


@contextmanager
def _directory_descriptor(root: Path) -> Iterator[int]:
    descriptor = os.open(root.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in root.parts[1:]:
            next_descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        yield descriptor
    finally:
        os.close(descriptor)


def _read_file(root_descriptor: int, path: str, max_bytes: int) -> tuple[bytes, str] | None:
    descriptor = os.dup(root_descriptor)
    try:
        parts = relative_path(path).split("/")
        for part in parts[:-1]:
            next_descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        metadata = os.stat(parts[-1], dir_fd=descriptor, follow_symlinks=False)
        if not stat.S_ISREG(metadata.st_mode):
            raise _NonRegularFileError
        file_descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        with os.fdopen(file_descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                raise _NonRegularFileError
            if metadata.st_size > max_bytes:
                return None
            content = stream.read(max_bytes + 1)
            if len(content) > max_bytes:
                return None
            try:
                content.decode("utf-8")
            except UnicodeDecodeError:
                return None
            return content, "100755" if metadata.st_mode & stat.S_IXUSR else "100644"
    finally:
        os.close(descriptor)


def read_file_bytes(root: Path, path: str, *, max_bytes: int) -> bytes:
    """Read a bounded regular UTF-8 file through link-free directory descriptors."""

    try:
        with _directory_descriptor(root) as descriptor:
            captured = _read_file(descriptor, path, max_bytes)
    except OSError:
        raise CodeUnavailableError("code_cache_invalid") from None
    if captured is None:
        raise CodeUnavailableError("code_cache_invalid")
    return captured[0]
