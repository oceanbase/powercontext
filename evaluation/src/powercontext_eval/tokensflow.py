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

"""Private TokensFlow profile snapshots and non-secret lifecycle helpers."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import stat
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, quote_plus

from powercontext_eval.errors import PowerContextEvalError
from powercontext_eval.models import Arm


class UnsafeTokensFlowConfiguration(PowerContextEvalError):
    """A TokensFlow profile or destination is missing or unsafe."""


class TokensFlowInfrastructureError(PowerContextEvalError):
    """TokensFlow could not prove a safe pre-inference runtime."""


@dataclass(frozen=True)
class DrainDeadline:
    """One fixed monotonic budget shared by every TokensFlow drain step."""

    timeout_seconds: float = 60.0
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)
    _deadline: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise TokensFlowInfrastructureError("TokensFlow drain timed out")
        object.__setattr__(self, "_deadline", self.clock() + self.timeout_seconds)

    def remaining(self) -> float:
        remaining = self._deadline - self.clock()
        if remaining <= 0:
            raise TokensFlowInfrastructureError("TokensFlow drain timed out")
        return remaining


@dataclass(frozen=True)
class TokensFlowSnapshot:
    """Private, arm-owned TokensFlow user home."""

    user_home: Path
    credentials: Path


@dataclass(frozen=True)
class TokensFlowEvidence:
    """Non-secret evidence for one arm's pre-inference TokensFlow gate."""

    host_version: str
    container_version: str
    host_identity_sha256: str
    container_identity_sha256: str
    identity_bytes: int
    identity_match: bool
    identity_checked_at: str
    daemon_started: bool
    daemon_started_at: str
    daemon_stopped: bool = False
    upload_all_succeeded: bool = False
    queue_caught_up: bool = False
    doctor_rc: int | None = None
    negative_detected: bool = False
    drain_duration_seconds: float = 0.0

    def as_dict(self) -> dict[str, str | int | float | bool | None]:
        return {
            "host_version": self.host_version,
            "container_version": self.container_version,
            "host_identity_sha256": self.host_identity_sha256,
            "container_identity_sha256": self.container_identity_sha256,
            "identity_bytes": self.identity_bytes,
            "identity_match": self.identity_match,
            "identity_checked_at": self.identity_checked_at,
            "daemon_started": self.daemon_started,
            "daemon_started_at": self.daemon_started_at,
            "daemon_stopped": self.daemon_stopped,
            "upload_all_succeeded": self.upload_all_succeeded,
            "queue_caught_up": self.queue_caught_up,
            "doctor_rc": self.doctor_rc,
            "negative_detected": self.negative_detected,
            "drain_duration_seconds": self.drain_duration_seconds,
        }


@dataclass(frozen=True)
class TokensFlowDaemonHandle:
    """Private lifecycle paths reserved for the later bounded drain."""

    pid_file: Path
    log_file: Path
    container_pid_file: str
    container_log_file: str


@dataclass(frozen=True)
class TokensFlowFinalizationDescriptor:
    """Credential-free resources durably transferred after arm artifacts are complete."""

    arm: Arm
    run_id: str
    container_name: str
    runtime: Path
    wrapper: Path
    egress_network: str
    daemon_pid_file: str
    evidence_sha256: str
    evidence_bytes: int


TokensFlowFinalizationRegistrar = Callable[[TokensFlowFinalizationDescriptor], None]


_TOKENSFLOW_VERSION = re.compile(
    rb"(?i:tokensflow) ([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3})"
    rb"(?: \([A-Za-z0-9][A-Za-z0-9 .,_:+/@=-]{0,63}\))?"
)
_TOKENSFLOW_CAUGHT_UP = b"[PASS] queue: caught up (0 pending files)"
_TOKENSFLOW_NEGATIVE_WORDS = (b"pending", b"rejected", b"failed", b"blocked")
_TOKENSFLOW_QUEUE_SCOPE = re.compile(rb"\b(?:queue|accounting)\b")
_TOKENSFLOW_ENVIRONMENT_NAME = re.compile(r"TOKENSFLOW_[A-Z0-9_]+")
_TOKENSFLOW_ENVIRONMENT_CREDENTIAL_MARKERS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "CREDENTIAL",
    "AUTH",
    "KEY",
)


_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
_READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
_WRITE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
_SENSITIVE_CREDENTIAL_FIELDS = frozenset(
    {
        "access",
        "access_token",
        "api_key",
        "api_token",
        "client_secret",
        "password",
        "refresh_token",
        "secret",
        "token",
    }
)


def tokensflow_runtime_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Snapshot safe dynamic TokensFlow configuration without credentials or host-global settings."""

    environment = os.environ if source is None else source
    selected: dict[str, str] = {}
    for name, value in sorted(environment.items()):
        if _TOKENSFLOW_ENVIRONMENT_NAME.fullmatch(name) is None:
            continue
        configuration_name = name.removeprefix("TOKENSFLOW_")
        if any(marker in configuration_name for marker in _TOKENSFLOW_ENVIRONMENT_CREDENTIAL_MARKERS):
            continue
        selected[name] = value
    return selected


def normalize_tokensflow_identity(raw: bytes) -> bytes:
    """Normalize only CRLF versus LF and one terminal line ending."""

    normalized = raw.replace(b"\r\n", b"\n")
    return normalized[:-1] if normalized.endswith(b"\n") else normalized


def tokensflow_identity_sha256(raw: bytes) -> str:
    """Hash normalized identity bytes without retaining their content."""

    return hashlib.sha256(normalize_tokensflow_identity(raw)).hexdigest()


def parse_tokensflow_version(raw: bytes) -> str:
    """Return only the semantic version from a TokensFlow version response."""

    normalized = normalize_tokensflow_identity(raw)
    match = _TOKENSFLOW_VERSION.fullmatch(normalized)
    if match is None:
        raise TokensFlowInfrastructureError("TokensFlow version check failed")
    return match.group(1).decode("ascii")


def tokensflow_queue_caught_up(raw: bytes) -> bool:
    """Require one exact, complete TokensFlow queue PASS line."""

    normalized = raw.replace(b"\r\n", b"\n")
    queue_lines = [line for line in normalized.splitlines() if re.search(rb"\bqueue\b", line.lower())]
    return queue_lines == [_TOKENSFLOW_CAUGHT_UP]


def tokensflow_queue_negative_detected(raw: bytes) -> bool:
    """Detect explicit nonzero/failed/blocked/open queue evidence without retaining it."""

    normalized = raw.lower().replace(b"\r\n", b"\n")
    for line in normalized.splitlines():
        if _TOKENSFLOW_QUEUE_SCOPE.search(line) is None:
            continue
        if re.search(
            rb"\[(?:fail|failed)\]|\b(?:collector\s+)?circuit\s*[:=-]?\s*open\b|\bcircuit[- ]open\b",
            line,
        ):
            return True
        for word in _TOKENSFLOW_NEGATIVE_WORDS:
            if word not in line:
                continue
            safe = (
                re.search(rb"\bno\s+" + word + rb"\b", line)
                or re.search(rb"\b0\s+" + word + rb"\b", line)
                or re.search(word + rb"[^:=\n]*[:=]\s*(?:0|none|false)\b", line)
            )
            if safe is None:
                return True
    return False


def matched_tokensflow_evidence(
    *,
    host_version: str,
    container_version: str,
    host_identity: bytes,
    container_identity: bytes,
) -> TokensFlowEvidence:
    """Build hash-only evidence or reject unequal normalized identities."""

    normalized_host = normalize_tokensflow_identity(host_identity)
    normalized_container = normalize_tokensflow_identity(container_identity)
    host_hash = hashlib.sha256(normalized_host).hexdigest()
    container_hash = hashlib.sha256(normalized_container).hexdigest()
    if normalized_host != normalized_container:
        raise TokensFlowInfrastructureError("TokensFlow identity did not match")
    if host_version != container_version:
        raise TokensFlowInfrastructureError("TokensFlow version did not match")
    return TokensFlowEvidence(
        host_version=host_version,
        container_version=container_version,
        host_identity_sha256=host_hash,
        container_identity_sha256=container_hash,
        identity_bytes=len(normalized_host),
        identity_match=True,
        identity_checked_at=datetime.now(UTC).isoformat(),
        daemon_started=False,
        daemon_started_at="",
    )


def _require_safe_absolute_path(path: Path) -> None:
    raw = os.fspath(path)
    if not path.is_absolute() or "\x00" in raw or ".." in path.parts:
        raise UnsafeTokensFlowConfiguration("Source is not a safe TokensFlow profile")


def _open_directory(path: Path) -> int:
    current_fd = os.open(path.anchor, _DIRECTORY_FLAGS)
    try:
        for component in path.parts[1:]:
            next_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _create_private_destination(destination: Path) -> int:
    anchor_fd = os.open(destination.anchor, _DIRECTORY_FLAGS)
    current_fd = anchor_fd
    try:
        parts = destination.parts[1:]
        for index, component in enumerate(parts):
            is_destination = index == len(parts) - 1
            if is_destination:
                os.mkdir(component, 0o700, dir_fd=current_fd)
            else:
                try:
                    os.mkdir(component, 0o700, dir_fd=current_fd)
                except FileExistsError:
                    pass
            next_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        os.fchmod(current_fd, 0o700)
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _copy_regular_file(source_fd: int, destination_fd: int, name: str, expected: os.stat_result) -> None:
    input_fd = os.open(name, _READ_FLAGS, dir_fd=source_fd)
    output_fd = -1
    try:
        opened = os.fstat(input_fd)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
            raise UnsafeTokensFlowConfiguration("Source is not a safe TokensFlow profile")
        output_fd = os.open(name, _WRITE_FLAGS, 0o600, dir_fd=destination_fd)
        while chunk := os.read(input_fd, 1024 * 1024):
            view = memoryview(chunk)
            written = 0
            while written < len(view):
                count = os.write(output_fd, view[written:])
                if count <= 0:
                    raise OSError("TokensFlow snapshot copy made no progress")
                written += count
        os.fchmod(output_fd, 0o600)
        os.fsync(output_fd)
    finally:
        if output_fd >= 0:
            os.close(output_fd)
        os.close(input_fd)


def _copy_directory(source_fd: int, destination_fd: int) -> None:
    for name in sorted(os.listdir(source_fd)):
        if name in {"", ".", ".."} or "/" in name or "\x00" in name:
            raise UnsafeTokensFlowConfiguration("Source is not a safe TokensFlow profile")
        metadata = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
        if stat.S_ISDIR(metadata.st_mode):
            os.mkdir(name, 0o700, dir_fd=destination_fd)
            child_source = os.open(name, _DIRECTORY_FLAGS, dir_fd=source_fd)
            child_destination = os.open(name, _DIRECTORY_FLAGS, dir_fd=destination_fd)
            try:
                opened = os.fstat(child_source)
                if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                    raise UnsafeTokensFlowConfiguration("Source is not a safe TokensFlow profile")
                _copy_directory(child_source, child_destination)
                os.fchmod(child_destination, 0o700)
                os.fsync(child_destination)
            finally:
                os.close(child_destination)
                os.close(child_source)
        elif stat.S_ISREG(metadata.st_mode):
            _copy_regular_file(source_fd, destination_fd, name, metadata)
        else:
            raise UnsafeTokensFlowConfiguration("Source is not a safe TokensFlow profile")


def _create_private_state_tree(destination_fd: int) -> None:
    current_fd = os.dup(destination_fd)
    try:
        for component in (".local", "share", "tokensflow"):
            os.mkdir(component, 0o700, dir_fd=current_fd)
            next_fd = os.open(component, _DIRECTORY_FLAGS, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
            os.fchmod(current_fd, 0o700)
        os.fsync(current_fd)
    finally:
        os.close(current_fd)


def snapshot_tokensflow_home(source_user_home: Path, destination_user_home: Path) -> TokensFlowSnapshot:
    """Copy the current TokensFlow profile to a fresh, private arm-owned user home."""

    _require_safe_absolute_path(source_user_home)
    _require_safe_absolute_path(destination_user_home)
    source_home_fd = source_config_fd = destination_fd = destination_config_fd = -1
    created_destination = False
    try:
        source_home_fd = _open_directory(source_user_home)
        source_config_fd = os.open(".tokensflow", _DIRECTORY_FLAGS, dir_fd=source_home_fd)
        credentials = os.stat("credentials.json", dir_fd=source_config_fd, follow_symlinks=False)
        if not stat.S_ISREG(credentials.st_mode):
            raise UnsafeTokensFlowConfiguration("Source is not a safe TokensFlow profile")

        destination_fd = _create_private_destination(destination_user_home)
        created_destination = True
        os.mkdir(".tokensflow", 0o700, dir_fd=destination_fd)
        destination_config_fd = os.open(".tokensflow", _DIRECTORY_FLAGS, dir_fd=destination_fd)
        _copy_directory(source_config_fd, destination_config_fd)
        os.fchmod(destination_config_fd, 0o700)
        os.fsync(destination_config_fd)
        _create_private_state_tree(destination_fd)
        os.fsync(destination_fd)
    except (OSError, UnsafeTokensFlowConfiguration) as error:
        if created_destination:
            shutil.rmtree(destination_user_home, ignore_errors=True)
        if isinstance(error, UnsafeTokensFlowConfiguration):
            raise
        raise UnsafeTokensFlowConfiguration("Source is not a safe TokensFlow profile") from error
    finally:
        for descriptor in (destination_config_fd, destination_fd, source_config_fd, source_home_fd):
            if descriptor >= 0:
                os.close(descriptor)

    return TokensFlowSnapshot(
        user_home=destination_user_home,
        credentials=destination_user_home / ".tokensflow" / "credentials.json",
    )


def tokensflow_secret_variants(credentials_json: Path) -> tuple[str, ...]:
    """Expand only non-empty string values stored under credential-bearing fields."""

    descriptor = -1
    try:
        descriptor = os.open(credentials_json, _READ_FLAGS)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise UnsafeTokensFlowConfiguration("Source is not a safe TokensFlow profile")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            value = json.load(stream)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
        raise UnsafeTokensFlowConfiguration("Source is not a safe TokensFlow profile") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    raw_values: set[str] = set()

    def visit(item: object) -> None:
        if isinstance(item, dict):
            for key, nested in item.items():
                if isinstance(key, str) and _is_sensitive_credential_field(key):
                    if isinstance(nested, str) and nested:
                        raw_values.add(nested)
                    continue
                visit(nested)
        elif isinstance(item, list):
            for nested in item:
                visit(nested)

    visit(value)
    variants: set[str] = set()
    for raw in raw_values:
        encoded = raw.encode("utf-8")
        variants.update(
            {
                raw,
                quote(raw, safe=""),
                quote_plus(raw, safe=""),
                base64.b64encode(encoded).decode("ascii"),
                base64.urlsafe_b64encode(encoded).decode("ascii"),
                encoded.hex(),
            }
        )
    return tuple(sorted(variants, key=lambda item: (-len(item), item)))


def _is_sensitive_credential_field(field: str) -> bool:
    normalized = field.casefold().replace("-", "_")
    return normalized in _SENSITIVE_CREDENTIAL_FIELDS or normalized.endswith(("_token", "_secret", "_password", "_key"))
