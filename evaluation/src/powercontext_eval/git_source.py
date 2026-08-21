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

"""Immutable Git source resolution and materialization."""

from __future__ import annotations

import ctypes
import errno
import fcntl
import hashlib
import math
import os
import platform
import re
import shutil
import stat
import sys
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit, urlunsplit

from powercontext_eval.errors import CommandError, GitSourceError
from powercontext_eval.models import PowerContextRef
from powercontext_eval.process import CommandResult, ProcessRunner

_FULL_LOWERCASE_SHA = re.compile(r"[0-9a-f]{40}")
_SUPPORTED_URL_SCHEMES = frozenset({"http", "https", "ssh"})
_SCP_STYLE_URL = re.compile(r"^(?:(?P<user>[^/@:\s]+)@)?(?P<host>[^/:\s]+):(?P<path>.+)$")
_GIT_ENVIRONMENT = {
    "GCM_INTERACTIVE": "never",
    "GIT_TERMINAL_PROMPT": "0",
}
_LINUX_RENAMEAT2_SYSCALLS = {
    "aarch64": 276,
    "amd64": 316,
    "arm64": 276,
    "x86_64": 316,
}
_MIRROR_LOCK_DIRECTORY = ".powercontext-eval-locks"
_RESOLVE_LOCKS_GUARD = threading.Lock()
_RESOLVE_LOCKS: dict[Path, threading.Lock] = {}


def _resolve_lock(cache_path: Path) -> threading.Lock:
    """Return the process-wide lock for one mutable bare mirror."""

    with _RESOLVE_LOCKS_GUARD:
        return _RESOLVE_LOCKS.setdefault(cache_path, threading.Lock())


def _mirror_lock_path(cache_root: Path, cache_path: Path) -> Path:
    """Return one canonical lock file for a physical mirror bucket."""

    canonical_root = cache_root.resolve(strict=True)
    if cache_path.parent.resolve(strict=True) != canonical_root:
        raise GitSourceError("Git cache bucket must be a direct child of cache root")
    if re.fullmatch(r"[0-9a-f]{64}", cache_path.name) is None:
        raise GitSourceError("Git cache bucket must use its canonical SHA-256 name")
    return canonical_root / _MIRROR_LOCK_DIRECTORY / f"{cache_path.name}.lock"


@contextmanager
def _mirror_write_lock(cache_root: Path, cache_path: Path) -> Iterator[None]:
    """Serialize mirror mutation across GitSource instances and service processes."""

    lock_path = _mirror_lock_path(cache_root, cache_path)
    with _resolve_lock(lock_path):
        lock_directory = lock_path.parent
        try:
            lock_directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
        lock_status = os.lstat(lock_directory)
        if stat.S_ISLNK(lock_status.st_mode) or not stat.S_ISDIR(lock_status.st_mode):
            raise GitSourceError("Git mirror lock directory was unsafe")
        if lock_directory.resolve(strict=True).parent != cache_root.resolve(strict=True):
            raise GitSourceError("Git mirror lock directory escaped cache root")

        flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(lock_path, flags, 0o600)
        except OSError:
            raise GitSourceError("Git mirror lock was unsafe or unavailable") from None
        try:
            descriptor_status = os.fstat(descriptor)
            if not stat.S_ISREG(descriptor_status.st_mode) or descriptor_status.st_nlink != 1:
                raise GitSourceError("Git mirror lock was unsafe or unavailable")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)


@dataclass(frozen=True)
class ResolvedGitSource:
    """A source reference resolved once to an immutable Git commit."""

    source: str
    requested: PowerContextRef
    sha: str
    cache_root: Path
    cache_path: Path

    def __post_init__(self) -> None:
        if not self.source:
            raise ValueError("Resolved source must not be empty")
        if not isinstance(self.requested, PowerContextRef):
            raise TypeError("requested must be a PowerContextRef")
        if _FULL_LOWERCASE_SHA.fullmatch(self.sha) is None:
            raise ValueError("Resolved SHA must contain exactly 40 lowercase hexadecimal characters")
        if not isinstance(self.cache_root, Path) or not isinstance(self.cache_path, Path):
            raise TypeError("cache_root and cache_path must be Paths")
        if not self.cache_root.is_absolute() or not self.cache_path.is_absolute():
            raise ValueError("Resolved cache provenance must use absolute paths")
        if self.cache_path.parent != self.cache_root:
            raise ValueError("Resolved cache bucket must be a direct child of cache root")
        if re.fullmatch(r"[0-9a-f]{64}", self.cache_path.name) is None:
            raise ValueError("Resolved cache bucket must use its canonical SHA-256 name")


@dataclass(frozen=True)
class _SourceDetails:
    normalized: str
    transport: str
    local_path: Path | None
    secrets: tuple[str, ...]
    has_embedded_credentials: bool


class GitSource:
    """Resolve explicit Git refs into cached immutable commits."""

    def __init__(
        self,
        *,
        cache_root: str | Path,
        runner: ProcessRunner | None = None,
        command_timeout: float = 300.0,
    ) -> None:
        if (
            isinstance(command_timeout, bool)
            or not isinstance(command_timeout, (int, float))
            or not math.isfinite(command_timeout)
            or command_timeout <= 0
        ):
            raise ValueError("Git command timeout must be finite and greater than zero")
        self._cache_root = Path(os.path.abspath(Path(cache_root).expanduser()))
        self._runner = runner or ProcessRunner()
        self._command_timeout = float(command_timeout)

    def normalize_source(self, source: str | Path) -> str:
        """Return the credential-free source used for provenance and cache identity."""

        return _source_details(source).normalized

    def cache_path_for(self, source: str | Path) -> Path:
        """Return the deterministic credential-free mirror location for a source."""

        normalized = self.normalize_source(source)
        return self._cache_path_for_normalized(normalized)

    def resolve(self, source: str | Path, requested: PowerContextRef) -> ResolvedGitSource:
        """Resolve one explicit ref and retain its commit rather than the moving ref."""

        if not isinstance(requested, PowerContextRef):
            raise TypeError("requested must be a PowerContextRef")
        details = _source_details(source)
        if details.has_embedded_credentials:
            raise GitSourceError("Embedded Git credentials are not allowed; configure a credential helper")
        cache_path = self._cache_path_for_normalized(details.normalized)
        self._validate_cache_location(cache_path)
        with _mirror_write_lock(self._cache_root, cache_path):
            self._validate_cache_location(cache_path)

            local_head: str | None = None
            if requested.kind == "latest" and details.local_path is not None:
                local_head = self._clean_local_head(details)

            self._ensure_mirror(details, cache_path)
            ref = self._ref_to_resolve(details, requested, cache_path, local_head)
            if requested.kind in {"branch", "tag"}:
                self._verify_exact_ref(cache_path, ref)
            sha = self._resolve_commit(cache_path, ref)
            self._pin_commit(cache_path, sha)
            canonical_cache_root = self._cache_root.resolve(strict=True)
            canonical_cache_path = cache_path.resolve(strict=True)
        return ResolvedGitSource(
            source=details.normalized,
            requested=requested,
            sha=sha,
            cache_root=canonical_cache_root,
            cache_path=canonical_cache_path,
        )

    def _cache_path_for_normalized(self, normalized: str) -> Path:
        bucket = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        return self._cache_root / bucket

    def materialize(self, resolved: ResolvedGitSource, target: str | Path) -> Path:
        """Clone and detach at the already-resolved SHA without consulting a moving ref."""

        if not isinstance(resolved, ResolvedGitSource):
            raise TypeError("resolved must be a ResolvedGitSource")
        self._validate_resolved_cache_provenance(resolved)
        self._verify_resolved_pin(resolved)
        target_path = Path(os.path.abspath(Path(target).expanduser()))
        if os.path.lexists(target_path):
            raise GitSourceError(f"Materialization target must not exist: {target_path}")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = Path(
            tempfile.mkdtemp(
                prefix=f".{target_path.name}.powercontext-eval-",
                dir=target_path.parent,
            )
        )
        published = False
        try:
            self._git(
                ["git", "clone", "--no-checkout", str(resolved.cache_path), str(temporary_path)],
                cwd=target_path.parent,
                action="could not clone resolved Git source",
            )
            self._git(
                ["git", "checkout", "--detach", resolved.sha],
                cwd=temporary_path,
                action="could not check out resolved Git commit",
            )
            actual = self._resolve_commit(temporary_path, "HEAD")
            if actual != resolved.sha:
                raise GitSourceError(
                    f"Materialized HEAD did not match resolved commit: expected {resolved.sha}, got {actual}"
                )
            try:
                _atomic_publish_directory(temporary_path, target_path)
            except FileExistsError as error:
                raise GitSourceError(f"Materialization target already exists: {target_path}") from error
            except OSError as error:
                raise GitSourceError("Could not atomically publish materialized Git source") from error
            published = True
            return target_path
        finally:
            if not published:
                _remove_owned_temporary_directory(temporary_path)

    def _validate_resolved_cache_provenance(self, resolved: ResolvedGitSource) -> None:
        configured_root = self._cache_root.resolve(strict=False)
        if configured_root != resolved.cache_root:
            raise GitSourceError("Resolved Git source belongs to a different cache root")
        if resolved.cache_path.parent != resolved.cache_root:
            raise GitSourceError("Resolved Git cache bucket is not a direct child of cache root")
        expected_bucket_name = hashlib.sha256(resolved.source.encode("utf-8")).hexdigest()
        if resolved.cache_path.name != expected_bucket_name:
            raise GitSourceError("Resolved Git cache bucket does not match its source identity")

        if not os.path.lexists(self._cache_root):
            raise GitSourceError("Resolved Git cache root no longer exists")
        root_status = os.lstat(self._cache_root)
        if stat.S_ISLNK(root_status.st_mode):
            raise GitSourceError("Resolved Git cache root must not be a symlink")
        if not stat.S_ISDIR(root_status.st_mode):
            raise GitSourceError("Resolved Git cache root must be a directory")
        if self._cache_root.resolve(strict=True) != resolved.cache_root:
            raise GitSourceError("Resolved Git cache root provenance changed")

        if not os.path.lexists(resolved.cache_path):
            raise GitSourceError("Resolved Git cache bucket no longer exists")
        cache_status = os.lstat(resolved.cache_path)
        if stat.S_ISLNK(cache_status.st_mode):
            raise GitSourceError("Resolved Git cache bucket must not be a symlink")
        if not stat.S_ISDIR(cache_status.st_mode):
            raise GitSourceError("Resolved Git cache bucket must be a directory")
        if resolved.cache_path.resolve(strict=True).parent != resolved.cache_root:
            raise GitSourceError("Resolved Git cache bucket escaped cache root")

    def _clean_local_head(self, details: _SourceDetails) -> str:
        assert details.local_path is not None
        if not details.local_path.is_dir():
            raise GitSourceError(f"Local Git source does not exist: {details.normalized}")
        bare = self._git(
            ["git", "rev-parse", "--is-bare-repository"],
            cwd=details.local_path,
            action="could not inspect local Git source",
            secrets=details.secrets,
        )
        if bare.stdout.strip() not in {"true", "false"}:
            raise GitSourceError("Local Git source returned an invalid bare-repository status")
        if bare.stdout.strip() == "false":
            status = self._git(
                ["git", "status", "--porcelain"],
                cwd=details.local_path,
                action="could not inspect local Git source",
                secrets=details.secrets,
            )
            if status.stdout:
                raise GitSourceError("Local latest requires a clean Git working tree")
        return self._resolve_commit(details.local_path, "HEAD", secrets=details.secrets)

    def _ensure_mirror(self, details: _SourceDetails, cache_path: Path) -> None:
        self._validate_cache_location(cache_path)
        if not os.path.lexists(cache_path):
            self._git(
                self._transport_command(
                    details,
                    ["clone", "--mirror", details.normalized, str(cache_path)],
                ),
                cwd=self._cache_root,
                action="could not clone Git mirror",
                secrets=details.secrets,
            )
            return

        if not cache_path.is_dir():
            raise GitSourceError(f"Git cache path is not a directory: {cache_path}")
        bare = self._git(
            ["git", "rev-parse", "--is-bare-repository"],
            cwd=cache_path,
            action="could not validate Git mirror",
        )
        if bare.stdout.strip() != "true":
            raise GitSourceError(f"Git cache path is not a bare mirror: {cache_path}")

        self._validate_mirror_origin(details, cache_path)

        self._git(
            self._transport_command(
                details,
                [
                    "fetch",
                    "--prune",
                    "origin",
                    "+refs/heads/*:refs/heads/*",
                    "+refs/tags/*:refs/tags/*",
                ],
            ),
            cwd=cache_path,
            action="could not refresh Git mirror",
            secrets=details.secrets,
        )

    def _validate_mirror_origin(self, details: _SourceDetails, cache_path: Path) -> None:
        try:
            result = self._runner.run(
                ["git", "config", "--get-all", "remote.origin.url"],
                cwd=cache_path,
                timeout=self._command_timeout,
                env=_GIT_ENVIRONMENT,
                secrets=details.secrets,
            )
        except CommandError:
            raise GitSourceError("Existing Git mirror origin was missing or invalid") from None

        origin_lines = result.stdout.splitlines()
        if len(origin_lines) != 1 or not origin_lines[0]:
            raise GitSourceError("Existing Git mirror origin was missing or invalid")
        try:
            origin_details = _source_details(origin_lines[0])
        except GitSourceError:
            raise GitSourceError("Existing Git mirror origin was missing or invalid") from None
        if (
            origin_details.transport != origin_details.normalized
            or origin_details.has_embedded_credentials
            or origin_details.normalized != details.normalized
        ):
            raise GitSourceError("Existing Git mirror origin did not match normalized source")

    def _validate_cache_location(self, cache_path: Path) -> None:
        if cache_path.parent != self._cache_root:
            raise GitSourceError("Git cache bucket must be a direct child of cache root")

        if os.path.lexists(self._cache_root):
            root_status = os.lstat(self._cache_root)
            if stat.S_ISLNK(root_status.st_mode):
                raise GitSourceError("Git cache root must not be a symlink")
            if not stat.S_ISDIR(root_status.st_mode):
                raise GitSourceError("Git cache root must be a directory")
        else:
            self._cache_root.mkdir(parents=True)

        resolved_root = self._cache_root.resolve(strict=True)
        if os.path.lexists(cache_path):
            cache_status = os.lstat(cache_path)
            if stat.S_ISLNK(cache_status.st_mode):
                raise GitSourceError("Git cache bucket must not be a symlink")
            if cache_path.resolve(strict=True).parent != resolved_root:
                raise GitSourceError("Git cache bucket escaped cache root")
        elif cache_path.parent.resolve(strict=True) != resolved_root:
            raise GitSourceError("Git cache bucket escaped cache root")

    def _ref_to_resolve(
        self,
        details: _SourceDetails,
        requested: PowerContextRef,
        cache_path: Path,
        local_head: str | None,
    ) -> str:
        if requested.kind == "branch":
            return f"refs/heads/{requested.value}"
        if requested.kind == "tag":
            return f"refs/tags/{requested.value}"
        if requested.kind == "commit":
            assert requested.value is not None
            return requested.value.lower()
        if local_head is not None:
            return local_head

        remote_head = self._git(
            self._transport_command(details, ["ls-remote", "origin", "HEAD"]),
            cwd=cache_path,
            action="could not resolve remote HEAD",
            secrets=details.secrets,
        )
        matches = [
            line.split()[0].lower()
            for line in remote_head.stdout.splitlines()
            if len(line.split()) == 2 and line.split()[1] == "HEAD"
        ]
        if len(matches) != 1 or _FULL_LOWERCASE_SHA.fullmatch(matches[0]) is None:
            raise GitSourceError("Remote HEAD was missing or ambiguous")
        return matches[0]

    def _resolve_commit(self, cwd: Path, ref: str, *, secrets: tuple[str, ...] = ()) -> str:
        try:
            result = self._runner.run(
                ["git", "rev-parse", "--verify", f"{ref}^{{commit}}"],
                cwd=cwd,
                timeout=self._command_timeout,
                env=_GIT_ENVIRONMENT,
                secrets=secrets,
            )
        except CommandError as error:
            raise GitSourceError("Git reference could not resolve to a commit") from error
        candidates = result.stdout.splitlines()
        if len(candidates) != 1:
            raise GitSourceError("Git reference could not resolve unambiguously to a commit")
        sha = candidates[0].strip().lower()
        if _FULL_LOWERCASE_SHA.fullmatch(sha) is None:
            raise GitSourceError("Git returned a non-canonical commit SHA")
        return sha

    def _verify_exact_ref(self, cache_path: Path, ref: str) -> None:
        self._git(
            ["git", "show-ref", "--verify", "--quiet", ref],
            cwd=cache_path,
            action="Git reference could not resolve exactly",
        )

    def _pin_commit(self, cache_path: Path, sha: str) -> None:
        self._git(
            ["git", "update-ref", f"refs/powercontext-eval/pins/{sha}", sha],
            cwd=cache_path,
            action="could not pin resolved Git commit",
        )

    def _verify_resolved_pin(self, resolved: ResolvedGitSource) -> None:
        pin_ref = f"refs/powercontext-eval/pins/{resolved.sha}"
        try:
            actual = self._resolve_commit(resolved.cache_path, pin_ref)
        except GitSourceError as error:
            raise GitSourceError("Resolved Git pin is missing or invalid") from error
        if actual != resolved.sha:
            raise GitSourceError("Resolved Git pin did not match the resolved commit")

    def _transport_command(self, details: _SourceDetails, arguments: list[str]) -> list[str]:
        command = ["git"]
        if details.transport != details.normalized:
            command.extend(["-c", f"url.{details.transport}.insteadOf={details.normalized}"])
        command.extend(arguments)
        return command

    def _git(
        self,
        argv: list[str],
        *,
        cwd: Path,
        action: str,
        secrets: tuple[str, ...] = (),
    ) -> CommandResult:
        try:
            return self._runner.run(
                argv,
                cwd=cwd,
                timeout=self._command_timeout,
                env=_GIT_ENVIRONMENT,
                secrets=secrets,
            )
        except CommandError as error:
            raise GitSourceError(action) from error


def _source_details(source: str | Path) -> _SourceDetails:
    raw = str(source)
    if not raw or "\0" in raw:
        raise GitSourceError("Git source must be a non-empty path or URL without NUL")

    scp_match = _SCP_STYLE_URL.fullmatch(raw) if "://" not in raw else None
    if scp_match is not None:
        normalized = f"{scp_match.group('host')}:{scp_match.group('path')}"
        user = scp_match.group("user")
        secrets = tuple(secret for secret in (raw, user, unquote(user) if user else None) if secret)
        return _SourceDetails(
            normalized=normalized,
            transport=raw,
            local_path=None,
            secrets=secrets,
            has_embedded_credentials=False,
        )

    parsed = urlsplit(raw)
    if parsed.scheme:
        if parsed.scheme not in _SUPPORTED_URL_SCHEMES:
            raise GitSourceError(f"Unsupported Git URL scheme: {parsed.scheme}")
        if not parsed.hostname:
            raise GitSourceError("Git URL must contain a host")
        try:
            port = parsed.port
        except ValueError as error:
            raise GitSourceError("Git URL contains an invalid port") from error
        host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        netloc = f"{host}:{port}" if port is not None else host
        normalized = urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
        secrets = _url_secrets(raw, parsed.username, parsed.password, parsed.query, parsed.fragment)
        return _SourceDetails(
            normalized=normalized,
            transport=raw,
            local_path=None,
            secrets=secrets,
            has_embedded_credentials=parsed.password is not None or "?" in raw or "#" in raw,
        )

    local_path = Path(source).expanduser().resolve()
    normalized = str(local_path)
    return _SourceDetails(
        normalized=normalized,
        transport=normalized,
        local_path=local_path,
        secrets=(),
        has_embedded_credentials=False,
    )


def _url_secrets(
    raw: str,
    username: str | None,
    password: str | None,
    query: str,
    fragment: str,
) -> tuple[str, ...]:
    values = [raw]
    for value in (username, password):
        if value:
            values.extend((value, unquote(value)))
    values.extend(unquote(value) for pair in parse_qsl(query, keep_blank_values=True) for value in pair if value)
    if fragment:
        values.append(unquote(fragment))
    return tuple(dict.fromkeys(value for value in values if value))


def _remove_owned_temporary_directory(path: Path) -> None:
    if not os.path.lexists(path):
        return
    status = os.lstat(path)
    if stat.S_ISDIR(status.st_mode) and not stat.S_ISLNK(status.st_mode):
        shutil.rmtree(path)
    else:
        path.unlink()


def _atomic_publish_directory(source: Path, target: Path) -> None:
    """Atomically publish ``source`` without ever replacing ``target``."""

    libc = ctypes.CDLL(None, use_errno=True)
    encoded_source = os.fsencode(source)
    encoded_target = os.fsencode(target)

    if sys.platform.startswith("linux"):
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is not None:
            renameat2.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            renameat2.restype = ctypes.c_int
            result = renameat2(-100, encoded_source, -100, encoded_target, 1)
        else:
            syscall_number = _LINUX_RENAMEAT2_SYSCALLS.get(platform.machine().lower())
            syscall = getattr(libc, "syscall", None)
            if syscall_number is None or syscall is None:
                raise OSError(errno.ENOTSUP, "renameat2 is unavailable; refusing a non-atomic publication")
            syscall.argtypes = [
                ctypes.c_long,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            syscall.restype = ctypes.c_long
            result = syscall(syscall_number, -100, encoded_source, -100, encoded_target, 1)
    elif sys.platform == "darwin":
        renamex_np = getattr(libc, "renamex_np", None)
        if renamex_np is not None:
            renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
            renamex_np.restype = ctypes.c_int
            result = renamex_np(encoded_source, encoded_target, 0x00000004)
        else:
            renameatx_np = getattr(libc, "renameatx_np", None)
            if renameatx_np is None:
                raise OSError(errno.ENOTSUP, "exclusive rename is unavailable; refusing a non-atomic publication")
            renameatx_np.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            renameatx_np.restype = ctypes.c_int
            result = renameatx_np(-2, encoded_source, -2, encoded_target, 0x00000004)
    else:
        raise OSError(errno.ENOTSUP, "exclusive rename is unsupported on this platform")

    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), target)


__all__ = ["GitSource", "ResolvedGitSource"]
