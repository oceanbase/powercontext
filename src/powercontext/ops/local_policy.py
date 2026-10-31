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

"""Personal configuration policy and conservative, non-mutating database inventory."""

from __future__ import annotations

import os
import shlex
import stat
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from powercontext.cli.env_file import environment_context
from powercontext.ops.protocol import OpsError, digest
from powercontext.paths import POWERCONTEXT_HOME_ENV, powercontext_data_dir, sqlite_url
from powercontext.server.configuration import server_settings_context
from powercontext.service.environment import load_protected_environment_file
from powercontext.service.model import EnvironmentFileIdentity
from powercontext.transport import is_loopback_host


@dataclass(frozen=True)
class ConfigurationSummary:
    """Safe local plan fields; never retain environment contents or credentials."""

    identity: EnvironmentFileIdentity | None
    data_dir: Path
    database_path: Path
    endpoint: str
    local_mode: Literal["tokenless", "authenticated"]

    def as_dict(self) -> dict[str, object]:
        return {
            "identity": asdict(self.identity) if self.identity is not None else None,
            "data_dir": str(self.data_dir),
            "database_path": str(self.database_path),
            "endpoint": self.endpoint,
            "local_mode": self.local_mode,
        }


@dataclass(frozen=True)
class DatabaseClassification:
    """A preflight observation, not a whole-database compatibility certificate."""

    classification: Literal["new", "existing_unknown", "incompatible", "unknown"]
    allowed_to_start: bool
    fingerprint: str
    reason: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _ambient_configuration_names() -> set[str]:
    return {
        name
        for name in os.environ
        if name.upper() in {POWERCONTEXT_HOME_ENV, "PYTHONPATH", "PYTHONHOME"}
        or name.upper().startswith("POWERCONTEXT_SERVER_")
    }


def default_data_dir() -> Path:
    """Resolve the platform user-data location without an inherited Server override."""
    with environment_context({}, clear=_ambient_configuration_names()):
        return powercontext_data_dir().absolute()


def _absolute_path(path: Path) -> Path:
    if not path.is_absolute() or "\x00" in str(path):
        raise OpsError("configuration_path_unsupported")
    return Path(os.path.abspath(path))


def _new_values(data_dir: Path, port: int) -> dict[str, str]:
    root = _absolute_path(data_dir)
    if type(port) is not int or not 1 <= port <= 65535:
        raise OpsError("invalid_request")
    return {
        POWERCONTEXT_HOME_ENV: str(root),
        "POWERCONTEXT_SERVER_HTTP_HOST": "127.0.0.1",
        "POWERCONTEXT_SERVER_HTTP_PORT": str(port),
        "POWERCONTEXT_SERVER_DATABASE_KIND": "sqlite",
        "POWERCONTEXT_SERVER_DATABASE_URL": sqlite_url(root / "powercontext.db"),
        "POWERCONTEXT_SERVER_DASHBOARD_ENABLED": "true",
        "POWERCONTEXT_SERVER_ACCESS_MODE": "disabled",
        "POWERCONTEXT_SERVER_AUTH_ENABLED": "false",
    }


def new_configuration(data_dir: Path, port: int = 8000) -> bytes:
    """Render validated tokenless local defaults without creating files or directories."""
    values = _new_values(data_dir, port)
    _configuration_summary(values, data_dir=data_dir)
    return "".join(f"{key}={shlex.quote(value)}\n" for key, value in sorted(values.items())).encode("utf-8")


def preview_new_configuration(data_dir: Path, port: int = 8000) -> ConfigurationSummary:
    """Preview the same effective settings that ``new_configuration`` will publish."""
    return _configuration_summary(_new_values(data_dir, port), data_dir=data_dir)


def inspect_existing_configuration(env_file: Path, *, data_dir: Path | None = None) -> ConfigurationSummary:
    """Inspect a protected configuration, preserving the registered data-directory override."""
    try:
        loaded = load_protected_environment_file(env_file)
        return _configuration_summary(loaded.values, data_dir=data_dir, identity=loaded.identity)
    except (OSError, ValueError) as error:
        if isinstance(error, OpsError):
            raise
        raise OpsError("configuration_invalid") from error


def _configuration_summary(
    values: Mapping[str, str], *, data_dir: Path | None, identity: EnvironmentFileIdentity | None = None
) -> ConfigurationSummary:
    configured_home = next((value for key, value in values.items() if key.upper() == POWERCONTEXT_HOME_ENV), None)
    root = _absolute_path(
        data_dir if data_dir is not None else Path(configured_home) if configured_home else default_data_dir()
    )
    try:
        with (
            environment_context({}, clear=_ambient_configuration_names()),
            server_settings_context(environment=values, data_dir=root) as settings,
        ):
            if settings.database.kind != "sqlite":
                raise OpsError("database_backend_unsupported")
            url = make_url(settings.database.url)
            if url.query or not url.database or url.database == ":memory:" or url.database.startswith("file:"):
                raise OpsError("database_location_unsupported")
            database = _absolute_path(Path(url.database))
            if not is_loopback_host(settings.http.host):
                raise OpsError("configuration_not_local")
            host = f"[{settings.http.host}]" if ":" in settings.http.host else settings.http.host
            return ConfigurationSummary(
                identity=identity,
                data_dir=root,
                database_path=database,
                endpoint=f"http://{host}:{settings.http.port}",
                local_mode="tokenless"
                if settings.access.mode == "disabled" and not settings.auth.enabled
                else "authenticated",
            )
    except (OSError, ValueError, ArgumentError) as error:
        if isinstance(error, OpsError):
            raise
        raise OpsError("configuration_invalid") from error


def _stat_identity(value: os.stat_result, *, for_repair: bool = False) -> dict[str, int]:
    identity = {
        "device": value.st_dev,
        "inode": value.st_ino,
        "mode": value.st_mode,
    }
    if not for_repair:
        identity.update(size=value.st_size, modified_ns=value.st_mtime_ns)
    return identity


def _linked(value: os.stat_result) -> bool:
    return stat.S_ISLNK(value.st_mode) or bool(getattr(value, "st_file_attributes", 0) & 0x400)


def _file_evidence(path: Path, *, for_repair: bool = False) -> dict[str, object]:
    try:
        observed = path.lstat()
    except FileNotFoundError:
        return {"state": "absent"}
    return {
        "state": "linked" if _linked(observed) else "file" if stat.S_ISREG(observed.st_mode) else "not_file",
        "identity": _stat_identity(observed, for_repair=for_repair),
    }


def classify_database(  # noqa: C901
    configuration: ConfigurationSummary, target_version: str, *, for_repair: bool = False
) -> DatabaseClassification:
    """Inventory without SQLite connection, initialization, migration, or WAL side effects.

    Existing data remains unknown even when the Ops version matches the target.
    The current public maintenance checks cover individual domains, not the
    complete Server schema, indexes, task formats, and configured capabilities.
    Program-only repair binds an existing database's stable file identity,
    allowing normal writes and checkpointing before the service is stopped.
    Startup observations retain file sizes, times, and all recovery sidecars.
    """
    if not isinstance(target_version, str) or not target_version:
        raise OpsError("target_invalid")
    database = _absolute_path(configuration.database_path)
    evidence: dict[str, object] = {"database_path": str(database), "target_version": target_version}

    def result(
        classification: Literal["new", "existing_unknown", "incompatible", "unknown"], reason: str
    ) -> DatabaseClassification:
        return DatabaseClassification(classification, classification == "new", digest(evidence), reason)

    try:
        # Do not treat an absent leaf through a link or reparse point as a fresh
        # owned database. Inspecting parents never creates the missing tree.
        for parent in database.parents:
            try:
                observed = parent.lstat()
            except FileNotFoundError:
                continue
            if _linked(observed) or not stat.S_ISDIR(observed.st_mode):
                evidence["parent"] = _stat_identity(observed)
                return result("unknown", "database_location_unverified")
        files = {"": _file_evidence(database, for_repair=for_repair)}
        if not for_repair or files[""]["state"] != "file":
            files.update({
                suffix: _file_evidence(Path(f"{database}{suffix}")) for suffix in ("-wal", "-shm", "-journal")
            })
        evidence["files"] = files
        main = files[""]
        if main["state"] == "absent":
            if any(item["state"] != "absent" for item in files.values()):
                return result("unknown", "database_sidecars_without_main")
            return result("new", "database_absent")
        if main["state"] != "file":
            return result("unknown", "database_location_unverified")
        descriptor = os.open(database, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
        try:
            before = _stat_identity(os.fstat(descriptor), for_repair=for_repair)
            header = os.read(descriptor, 16)
            if before != main["identity"] or before != _stat_identity(os.fstat(descriptor), for_repair=for_repair):
                return result("unknown", "database_changed_during_inspection")
        finally:
            os.close(descriptor)
        if files != {
            suffix: _file_evidence(Path(f"{database}{suffix}"), for_repair=for_repair and suffix == "")
            for suffix in files
        }:
            return result("unknown", "database_changed_during_inspection")
        if header and header != b"SQLite format 3\x00":
            return result("incompatible", "database_format_unsupported")
        return result("existing_unknown", "whole_database_compatibility_unavailable")
    except OSError:
        return result("unknown", "database_inspection_failed")
