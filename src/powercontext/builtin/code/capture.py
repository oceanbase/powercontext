# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Bounded Git inventories and no-follow capture of actual worktree bytes."""

from __future__ import annotations

import errno
import fnmatch
import hashlib
import json
import os
import selectors
import stat
import subprocess
import time
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from powercontext.builtin.code.errors import CodeError
from powercontext.builtin.code.languages import LANGUAGES, language_for_path
from powercontext.builtin.code.models import CodeLimits, CodeRepositoryConfig, relative_path

_EXCLUDED_DIRECTORIES = frozenset({
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".powercontext",
    ".codegraph",
    "dist",
    "build",
    ".next",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
})
_SECRET_PATTERNS = (".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", ".netrc", ".npmrc")
_TEXT_SUFFIXES = frozenset(
    extension for language in LANGUAGES.values() for extension in language.extensions
) | frozenset({
    ".md",
    ".rst",
    ".txt",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".ini",
    ".cfg",
    ".mod",
    ".rs",
    ".c",
    ".h",
    ".cpp",
    ".java",
    ".sh",
    ".sql",
})


def source_lines(content: str) -> list[str]:
    """Split physical LF/CRLF source lines without treating Unicode separators as newlines."""
    parts = content.split("\n")
    return [part + "\n" for part in parts[:-1]] + ([parts[-1]] if parts[-1] else [])


def digest_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise CodeError("code_timeout")


def write_private(path: Path, content: bytes) -> None:
    """Create cache-owned files without inheriting a permissive umask."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())


@dataclass(frozen=True)
class CapturedFile:
    path: str
    mode: str
    size: int = 0
    sha256: str | None = None
    language: str = "text"
    reason: str | None = None
    git_blob: str | None = None


@dataclass
class Capture:
    root: Path
    root_identity: tuple[int, int]
    commit: str | None
    object_format: str
    git_state: str
    dirty: bool
    branch: str | None = None
    files: list[CapturedFile] = field(default_factory=list)
    invalid_paths: int = 0

    def identity(self) -> dict[str, Any]:
        return {
            "root_identity": self.root_identity,
            "commit": self.commit,
            "branch": self.branch,
            "git_object_format": self.object_format,
            "git_state": self.git_state,
            "dirty": self.dirty,
            "files": [asdict(item) for item in self.files],
            "invalid_paths": self.invalid_paths,
        }

    @property
    def content_identity(self) -> str:
        return digest_bytes(json_bytes(self.identity()))


def _git(root: Path, arguments: list[str], deadline: float, *, allow_unborn: bool = False) -> bytes:
    check_deadline(deadline)
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    command = [
        "git",
        "--no-optional-locks",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.untrackedCache=false",
        "-c",
        f"core.hooksPath={os.devnull}",
        "-C",
        str(root),
        *arguments,
    ]
    try:
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=environment) as process:  # noqa: S603 - fixed Git subcommands without execution hooks.
            try:
                output = _git_output(process, deadline)
                returncode = process.wait(timeout=max(0.01, deadline - time.monotonic()))
            finally:
                if process.poll() is None:
                    process.kill()
                process.wait()
    except subprocess.TimeoutExpired as error:
        raise CodeError("code_timeout") from error
    except OSError as error:
        raise CodeError("git_unavailable") from error
    if returncode and not (allow_unborn and returncode == 1):
        raise CodeError("repository_unavailable")
    return output


def _git_output(process: subprocess.Popen[bytes], deadline: float) -> bytes:
    if process.stdout is None:
        raise CodeError("git_unavailable")
    result = bytearray()
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while True:
            check_deadline(deadline)
            if not selector.select(min(0.1, max(0, deadline - time.monotonic()))):
                continue
            chunk = os.read(process.stdout.fileno(), 128 * 1024)
            if not chunk:
                return bytes(result)
            result.extend(chunk)
            if len(result) > 64 * 1024 * 1024:
                raise CodeError("repository_limit_exceeded")


def _inventory(root: Path, config: CodeRepositoryConfig, deadline: float) -> tuple[dict[str, tuple[str, str]], int]:
    entries: dict[str, tuple[str, str]] = {}
    invalid = 0
    for record in _git(root, ["ls-files", "--stage", "-z"], deadline).split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, object_id, stage = metadata.split(b" ")
        if stage != b"0":
            raise CodeError("workspace_busy", status=409)
        try:
            path = relative_path(raw_path.decode("utf-8"))
        except (UnicodeError, ValueError):
            invalid += 1
            continue
        entries[path] = (mode.decode("ascii"), object_id.decode("ascii"))
    if config.include_untracked:
        for raw_path in _git(root, ["ls-files", "--others", "--exclude-standard", "-z"], deadline).split(b"\0"):
            if not raw_path:
                continue
            try:
                entries[relative_path(raw_path.decode("utf-8"))] = ("untracked", "")
            except (UnicodeError, ValueError):
                invalid += 1
    return entries, invalid


@lru_cache(maxsize=16)
def _exclusion_groups(patterns: tuple[str, ...]) -> tuple[frozenset[str], tuple[str, ...]]:
    exact = frozenset(pattern for pattern in patterns if not any(char in pattern for char in "*?["))
    return exact, tuple(pattern for pattern in patterns if pattern not in exact)


def _excluded(path: str, config: CodeRepositoryConfig) -> str | None:
    parts = path.split("/")
    if any(part in _EXCLUDED_DIRECTORIES for part in parts[:-1]):
        return "excluded_directory"
    if any(fnmatch.fnmatchcase(parts[-1].lower(), pattern) for pattern in _SECRET_PATTERNS):
        return "credential_file"
    exact, patterns = _exclusion_groups(config.exclude)
    if path in exact or any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns):
        return "excluded_pattern"
    if Path(path).suffix.lower() not in _TEXT_SUFFIXES and parts[-1] not in {"Makefile", "Dockerfile", "LICENSE"}:
        return "unsupported_content"
    return None


def _read_relative(root_fd: int, path: str, limit: int, deadline: float) -> tuple[bytes | None, str | None, int, str]:
    """Open each component relative to a trusted directory descriptor."""
    descriptors: list[int] = []
    current = root_fd
    try:
        parts = path.split("/")
        for part in parts[:-1]:
            current = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=current)
            descriptors.append(current)
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=current)
        descriptors.append(descriptor)
        return _read_descriptor(descriptor, limit, deadline)
    except OSError as error:
        if error.errno in {errno.ELOOP, errno.ENOTDIR}:
            return None, "symlink_or_non_directory", 0, "120000"
        if error.errno == errno.ENOENT:
            return None, "missing_worktree_file", 0, "missing"
        raise CodeError("repository_read_failed") from error
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _read_descriptor(descriptor: int, limit: int, deadline: float) -> tuple[bytes | None, str | None, int, str]:
    before = os.fstat(descriptor)
    mode = "100755" if before.st_mode & 0o111 else "100644"
    if not stat.S_ISREG(before.st_mode):
        return None, "non_regular_file", 0, mode
    if before.st_size > limit:
        return None, "file_too_large", before.st_size, mode
    content = bytearray()
    while len(content) <= limit:
        check_deadline(deadline)
        chunk = os.read(descriptor, min(128 * 1024, limit + 1 - len(content)))
        if not chunk:
            break
        content.extend(chunk)
    after = os.fstat(descriptor)
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise CodeError("workspace_busy", status=409)
    if len(content) > limit:
        return None, "file_too_large", len(content), mode
    return bytes(content), None, len(content), mode


def capture_repository(
    config: CodeRepositoryConfig,
    limits: CodeLimits,
    deadline: float,
    *,
    content_dir: Path | None = None,
) -> Capture:
    """Capture or verify an inventory, including actual unstaged worktree content."""
    try:
        root = config.root.resolve(strict=True)
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as error:
        raise CodeError("repository_unavailable") from error
    try:
        if _git(root, ["rev-parse", "--show-prefix"], deadline).strip():
            raise CodeError("repository_root_required", status=422)
        commit = (
            _git(root, ["rev-parse", "--verify", "--quiet", "HEAD"], deadline, allow_unborn=True).decode().strip()
            or None
        )
        object_format = _git(root, ["rev-parse", "--show-object-format"], deadline).decode().strip()
        if object_format not in {"sha1", "sha256"}:
            raise CodeError("unsupported_git_object_format")
        entries, invalid = _inventory(root, config, deadline)
        root_stat = os.fstat(root_fd)
        capture = Capture(
            root, (root_stat.st_dev, root_stat.st_ino), commit, object_format, digest_bytes(json_bytes(entries)), False
        )
        capture.branch = (
            _git(root, ["symbolic-ref", "--quiet", "HEAD"], deadline, allow_unborn=True).decode().strip() or None
        )
        capture.invalid_paths = invalid
        included_count = total_bytes = 0
        for path, (mode, _object_id) in sorted(entries.items()):
            check_deadline(deadline)
            capture.files.append(
                _capture_file(root_fd, path, mode, config, limits, deadline, content_dir, object_format)
            )
            included = capture.files[-1]
            if included.sha256 is not None:
                included_count += 1
                total_bytes += included.size
                capture.dirty |= mode == "untracked"
            if included_count > limits.max_files or total_bytes > limits.max_source_bytes:
                raise CodeError("repository_limit_exceeded")
        capture.dirty |= _dirty(capture, entries, config, deadline)
        return capture
    finally:
        os.close(root_fd)


def _content_reason(content: bytes | None) -> str | None:
    if content is None:
        return None
    if b"\0" in content:
        return "binary_file"
    if content.startswith(b"version https://git-lfs.github.com/spec/v1\n"):
        return "lfs_pointer"
    try:
        content.decode("utf-8")
    except UnicodeError:
        return "unsupported_encoding"
    return None


def _capture_file(
    root_fd: int,
    path: str,
    mode: str,
    config: CodeRepositoryConfig,
    limits: CodeLimits,
    deadline: float,
    content_dir: Path | None,
    object_format: str,
) -> CapturedFile:
    reason = _excluded(path, config)
    if mode in {"120000", "160000"}:
        reason = "symlink" if mode == "120000" else "submodule"
    if reason:
        return CapturedFile(path, mode, reason=reason)
    content, reason, size, actual_mode = _read_relative(root_fd, path, limits.max_file_bytes, deadline)
    language = language_for_path(path)
    reason = reason or _content_reason(content)
    sha256 = digest_bytes(content) if content is not None and reason is None else None
    if content_dir is not None and content is not None and sha256 is not None:
        destination = content_dir / sha256
        if not destination.exists():
            write_private(destination, content)
    blob = (
        hashlib.new(object_format, b"blob " + str(size).encode() + b"\0" + content).hexdigest()
        if content is not None and reason is None
        else None
    )
    return CapturedFile(path, actual_mode, size, sha256, language, reason, blob)


def _dirty(
    capture: Capture, entries: dict[str, tuple[str, str]], config: CodeRepositoryConfig, deadline: float
) -> bool:
    # git status/diff may invoke worktree clean filters. Compare raw captured blobs
    # with the index and committed tree instead; no repository command executes.
    tracked = {
        item.path: item
        for item in capture.files
        if _excluded(item.path, config) is None and item.mode not in {"120000", "160000"}
    }
    for path, item in tracked.items():
        if item.reason == "missing_worktree_file" or (
            item.git_blob is not None and (item.mode, item.git_blob) != entries[path]
        ):
            return True
    if capture.commit is None:
        return bool(tracked)
    committed = {}
    for record in _git(capture.root, ["ls-tree", "-r", "-z", capture.commit], deadline).split(b"\0"):
        if not record:
            continue
        metadata, raw_path = record.split(b"\t", 1)
        mode, _kind, object_id = metadata.split(b" ")
        path = raw_path.decode("utf-8", errors="surrogateescape")
        if _excluded(path, config) is None and mode not in {b"120000", b"160000"}:
            committed[path] = (mode.decode(), object_id.decode())
    eligible_index = {
        path: value
        for path, value in entries.items()
        if _excluded(path, config) is None and value[0] not in {"120000", "160000"}
    }
    return eligible_index != committed


def compatible_baseline(root: Path, before: Capture | dict[str, Any], after: dict[str, Any], deadline: float) -> bool:
    old = before.identity() if isinstance(before, Capture) else before
    if old.get("branch") != after.get("branch"):
        return False
    left, right = old.get("commit"), after.get("commit")
    if left == right:
        return True
    if left is None or right is None:
        return False
    # merge-base prints a commit only when the old head belongs to current ancestry.
    common = _git(root, ["merge-base", left, right], deadline, allow_unborn=True).decode().strip()
    return common == left
