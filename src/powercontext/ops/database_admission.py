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

"""Bounded private snapshot transport to the verified target Server's admission entry."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from powercontext.ops.local_policy import ConfigurationSummary, DatabaseClassification, classify_database
from powercontext.ops.protocol import OpsError, canonical, decode, digest
from powercontext.service.environment import protect_directory

# Admission deliberately performs a complete copy and Server validation. Large
# or busy databases need explicit operator maintenance, not a partial certificate.
MAX_SNAPSHOT_BYTES = 8 * 1024**3
ADMISSION_TIMEOUT_SECONDS = 120
_COPY_BLOCK_BYTES = 1024 * 1024


def _identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _source_files(database: Path) -> dict[str, tuple[int, ...] | None]:
    result = {}
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            observed = Path(f"{database}{suffix}").lstat()
        except FileNotFoundError:
            result[suffix] = None
            continue
        if (
            not stat.S_ISREG(observed.st_mode)
            or observed.st_nlink != 1
            or getattr(observed, "st_file_attributes", 0) & 0x400
        ):
            raise OpsError("database_location_unverified")
        result[suffix] = _identity(observed)
    return result


def _copy(source: Path, destination: Path, expected: tuple[int, ...], deadline: float) -> str:
    checksum = hashlib.sha256()
    try:
        descriptor = os.open(source, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    except FileNotFoundError as error:
        raise OpsError("database_changed_during_admission") from error
    with os.fdopen(descriptor, "rb") as input_file, destination.open("xb") as output:
        if _identity(os.fstat(input_file.fileno())) != expected:
            raise OpsError("database_changed_during_admission")
        remaining = expected[4]
        while remaining:
            if time.monotonic() >= deadline:
                raise OpsError("database_admission_timeout")
            block = input_file.read(min(_COPY_BLOCK_BYTES, remaining))
            if not block:
                raise OpsError("database_changed_during_admission")
            checksum.update(block)
            output.write(block)
            remaining -= len(block)
        if input_file.read(1) or _identity(os.fstat(input_file.fileno())) != expected:
            raise OpsError("database_changed_during_admission")
    return checksum.hexdigest()


def _windows_pipe_bytes(descriptor: int) -> int:
    # Windows 3.11 does not implement os.set_blocking for pipes. Peek only;
    # os.read below consumes the known available bytes without waiting for EOF.
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)  # noqa: B009
    peek = kernel.PeekNamedPipe
    peek.argtypes = [
        wintypes.HANDLE,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.LPDWORD,
        wintypes.LPVOID,
    ]
    peek.restype = wintypes.BOOL
    available = wintypes.DWORD()
    if not peek(getattr(msvcrt, "get_osfhandle")(descriptor), None, 0, None, ctypes.byref(available), None):  # noqa: B009
        if getattr(ctypes, "get_last_error")() == 109:  # noqa: B009 -- ERROR_BROKEN_PIPE
            return 0
        raise OSError("admission_pipe_unavailable")
    return available.value


def _read_child(child: subprocess.Popen[bytes], deadline: float) -> bytes:
    if child.stdout is None:
        raise OpsError("database_admission_unavailable")
    descriptor = child.stdout.fileno()
    if os.name != "nt":
        os.set_blocking(descriptor, False)
    content = bytearray()
    exited = False
    while time.monotonic() < deadline:
        available = _windows_pipe_bytes(descriptor) if os.name == "nt" else 8193
        try:
            block = os.read(descriptor, min(available, 8193 - len(content))) if available else None
        except BlockingIOError:
            block = None
        if block:
            content.extend(block)
            if len(content) > 8192:
                raise OpsError("database_admission_unavailable")
            continue
        if block == b"" or exited:
            return bytes(content)
        if child.poll() is not None:
            # Drain once more to include bytes written between read and poll,
            # then stop even if a descendant still holds the output pipe open.
            exited = True
            continue
        time.sleep(0.01)
    raise OpsError("database_admission_timeout")


def _target_admission(
    configuration: ConfigurationSummary, version: str, executable: Path, snapshot: Path, deadline: float
) -> dict[str, Any]:
    if configuration.identity is None:
        raise OpsError("database_admission_unavailable")
    request = {
        "protocol_version": 1,
        "target_version": version,
        "configuration": asdict(configuration.identity),
        "data_dir": str(configuration.data_dir),
        "database": str(configuration.database_path),
        "snapshot": str(snapshot),
    }
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise OpsError("database_admission_timeout")
    # Never inherit Python injection or ambient Server settings. The protected
    # configuration and registered data directory are the target's authority.
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("PYTHON", "POWERCONTEXT_SERVER_")) and key.upper() != "POWERCONTEXT_HOME"
    }
    with tempfile.TemporaryFile(dir=snapshot.parent) as request_file:
        request_file.write(canonical(request))
        request_file.seek(0)
        with subprocess.Popen(  # noqa: S603 -- Verified managed-resource console interpreter.
            [str(executable), "-I", "-B", "-m", "powercontext.server.database_admission"],
            stdin=request_file,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            cwd=snapshot.parent,
            env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ) as child:
            try:
                content = _read_child(child, deadline)
                child.wait(timeout=max(0, deadline - time.monotonic()))
            except BaseException:
                child.kill()
                child.wait()
                raise
    if child.returncode != 0:
        raise OpsError("database_admission_unavailable")
    decoded = decode(content)
    if not isinstance(decoded, dict):
        raise OpsError("database_admission_unavailable")
    result = cast(dict[str, Any], decoded)
    if (
        type(result.get("protocol_version")) is not int
        or result["protocol_version"] != 1
        or not isinstance(result.get("state"), str)
        or result.get("state") not in {"ready", "rejected"}
        or not isinstance(result.get("reason"), str)
        or (
            result["state"] == "ready"
            and (result.get("reason") != "server_ready" or not isinstance(result.get("fingerprint"), str))
        )
    ):
        raise OpsError("database_admission_unavailable")
    return result


def _snapshot_admission(
    configuration: ConfigurationSummary,
    target_version: str,
    python_executable: Path,
    observed: DatabaseClassification,
    evidence: dict[str, object],
) -> dict[str, Any]:
    database = configuration.database_path
    source = _source_files(database)
    evidence["source"] = source
    if source[""] is None:
        raise OpsError("database_changed_during_admission")
    if source["-journal"] is not None:
        raise OpsError("database_recovery_required")
    copied = {
        suffix: identity for suffix, identity in source.items() if suffix in {"", "-wal"} and identity is not None
    }
    required = sum(identity[4] for identity in copied.values())
    if required > MAX_SNAPSHOT_BYTES:
        raise OpsError("database_snapshot_too_large")
    deadline = time.monotonic() + ADMISSION_TIMEOUT_SECONDS
    with tempfile.TemporaryDirectory(prefix="powercontext-admission-") as temporary:
        root = protect_directory(Path(temporary).resolve())
        if shutil.disk_usage(root).free < required + 32 * 1024**2:
            raise OpsError("database_snapshot_space_insufficient")
        snapshot = root / "database.db"
        evidence["content"] = {
            suffix: _copy(Path(f"{database}{suffix}"), Path(f"{snapshot}{suffix}"), identity, deadline)
            for suffix, identity in copied.items()
        }
        if _source_files(database) != source:
            raise OpsError("database_changed_during_admission")
        result = _target_admission(configuration, target_version, python_executable, snapshot, deadline)
        if _source_files(database) != source or classify_database(configuration, target_version) != observed:
            raise OpsError("database_changed_during_admission")
    return result


def admit_database(  # noqa: C901 -- Preserve ordered fail-closed result classification.
    configuration: ConfigurationSummary, target_version: str, *, python_executable: Path
) -> DatabaseClassification:
    """Admit only using a verified target; never connect SQLite to the source files.

    The installer must verify the complete target resource before calling this
    function. Passive inspect and program repair use ``classify_database`` only.
    """
    observed = classify_database(configuration, target_version)
    database = configuration.database_path
    if observed.classification != "existing_unknown":
        if observed.classification == "new" and any(
            path.exists() or path.is_symlink()
            for path in (Path(f"{database}.pc-migration-state"), Path(f"{database}.pc-migration.lock"))
        ):
            return DatabaseClassification("unknown", False, observed.fingerprint, "database_recovery_required")
        return observed
    evidence: dict[str, object] = {"inventory": observed.fingerprint}

    def rejected(reason: str, *, incompatible: bool = False) -> DatabaseClassification:
        return DatabaseClassification(
            "incompatible" if incompatible else "existing_unknown", False, digest(evidence), reason
        )

    try:
        result = _snapshot_admission(configuration, target_version, python_executable, observed, evidence)
        if result["state"] == "ready":
            evidence["target"] = result["fingerprint"]
            return DatabaseClassification("existing_ready", True, digest(evidence), "server_ready")
        reason = result["reason"]
        if reason in {
            "recovery_required",
            "migration_locked",
            "migration_lock_unsupported",
            "checksum_conflict",
            "archive_recovery_required",
        }:
            return rejected("database_recovery_required")
        if reason in {"migration_required", "server_migration_unavailable"}:
            return rejected("database_migration_required")
        if reason in {
            "embedding_profile_mismatch",
            "unsupported_upgrade_configuration",
            "configuration_invalid",
            "configuration_changed",
        }:
            return rejected("database_configuration_mismatch")
        if reason in {"incompatible_schema", "invalid_database", "verification_failed", "unknown_revision"}:
            return rejected("database_incompatible", incompatible=True)
        return rejected("database_admission_unavailable")
    except OpsError as error:
        return rejected(error.code)
    except (subprocess.TimeoutExpired, TimeoutError):
        return rejected("database_admission_timeout")
    except (OSError, ValueError):
        return rejected("database_admission_unavailable")


def require_admitted(database: DatabaseClassification) -> None:
    if database.allowed_to_start:
        return
    actionable = {
        "database_recovery_required",
        "database_migration_required",
        "database_configuration_mismatch",
        "database_snapshot_too_large",
        "database_snapshot_space_insufficient",
        "database_admission_timeout",
        "database_changed_during_admission",
    }
    raise OpsError(
        database.reason
        if database.reason in actionable
        else "database_incompatible"
        if database.classification == "incompatible"
        else "database_compatibility_unknown"
    )
