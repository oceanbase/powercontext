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

"""Stable state and definition models for the personal Server service."""

from __future__ import annotations

import os
import stat
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

DEFINITION_VERSION = 2
OWNERSHIP_MARKER = "powercontext.personal-server"


class SupportState(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"


class RegistrationState(StrEnum):
    INSTALLED = "installed"
    NOT_INSTALLED = "not_installed"
    INVALID = "invalid"
    UNKNOWN = "unknown"


class DefinitionState(StrEnum):
    CURRENT = "current"
    STALE = "stale"
    MISSING_EXECUTABLE = "missing_executable"
    UNKNOWN = "unknown"


class ManagerState(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    FAILED = "failed"
    UNKNOWN = "unknown"


class ManagerOwnershipState(StrEnum):
    NOT_LOADED = "not_loaded"
    OWNED = "owned"
    FOREIGN = "foreign"
    UNKNOWN = "unknown"


class LivenessState(StrEnum):
    LIVE = "live"
    UNREACHABLE = "unreachable"
    UNKNOWN = "unknown"


class ProbeState(StrEnum):
    LIVE = "live"
    UNREACHABLE = "unreachable"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class EnvironmentFileIdentity:
    path: str
    device: int
    inode: int
    size: int
    modified_ns: int
    owner_uid: int
    mode: int

    @classmethod
    def from_stat(cls, path: Path, status: os.stat_result) -> EnvironmentFileIdentity:
        return cls(
            path=os.path.abspath(path),
            device=status.st_dev,
            inode=status.st_ino,
            size=status.st_size,
            modified_ns=status.st_mtime_ns,
            owner_uid=status.st_uid,
            mode=stat.S_IMODE(status.st_mode),
        )

    @classmethod
    def from_path(cls, path: Path) -> EnvironmentFileIdentity:
        return cls.from_stat(path, path.stat())


@dataclass(frozen=True)
class ServiceDefinition:
    ownership: str
    definition_version: int
    package_version: str
    python_executable: str
    endpoint: str
    data_dir: str
    env_file: EnvironmentFileIdentity | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: object) -> ServiceDefinition:
        if not isinstance(value, dict):
            raise TypeError("service definition must be an object")  # noqa: TRY003
        payload = cast(dict[str, object], value)
        expected = {
            "ownership",
            "definition_version",
            "package_version",
            "python_executable",
            "endpoint",
            "data_dir",
            "env_file",
        }
        if set(payload) != expected:
            raise ValueError("service definition fields do not match the supported contract")  # noqa: TRY003
        environment = payload["env_file"]
        env_file = None
        if environment is not None:
            if not isinstance(environment, dict):
                raise ValueError("service env-file identity must be an object")  # noqa: TRY003
            environment_payload = cast(dict[str, object], environment)
            env_file = EnvironmentFileIdentity(
                path=_required_string(environment_payload, "path"),
                device=_required_int(environment_payload, "device"),
                inode=_required_int(environment_payload, "inode"),
                size=_required_int(environment_payload, "size"),
                modified_ns=_required_int(environment_payload, "modified_ns"),
                owner_uid=_optional_int(environment_payload, "owner_uid", default=-1),
                mode=_optional_int(environment_payload, "mode", default=-1),
            )
        return cls(
            ownership=_required_string(payload, "ownership"),
            definition_version=_required_int(payload, "definition_version"),
            package_version=_required_string(payload, "package_version"),
            python_executable=_required_string(payload, "python_executable"),
            endpoint=_required_string(payload, "endpoint"),
            data_dir=_required_string(payload, "data_dir"),
            env_file=env_file,
        )

    def launcher_arguments(
        self,
        *,
        module: str = "powercontext.service.launcher",
        include_env_identity: bool = True,
    ) -> list[str]:
        arguments = [
            self.python_executable,
            "-m",
            module,
            "--endpoint",
            self.endpoint,
            "--data-dir",
            self.data_dir,
        ]
        if self.env_file is not None:
            arguments.extend(("--env-file", self.env_file.path))
            if include_env_identity:
                arguments.extend((
                    "--env-file-device",
                    str(self.env_file.device),
                    "--env-file-inode",
                    str(self.env_file.inode),
                    "--env-file-size",
                    str(self.env_file.size),
                    "--env-file-modified-ns",
                    str(self.env_file.modified_ns),
                    "--env-file-owner-uid",
                    str(self.env_file.owner_uid),
                    "--env-file-mode",
                    str(self.env_file.mode),
                ))
        return arguments


@dataclass(frozen=True)
class NativeRegistration:
    state: RegistrationState
    definition: ServiceDefinition | None = None
    content: bytes | None = None
    detail: str | None = None


@dataclass(frozen=True)
class ManagerRegistration:
    state: ManagerOwnershipState
    definition: ServiceDefinition | None = None
    detail: str | None = None


@dataclass(frozen=True)
class ProbeResult:
    state: ProbeState
    detail: str


@dataclass(frozen=True)
class ServiceStatus:
    support: SupportState
    registration: RegistrationState
    definition: DefinitionState
    manager: ManagerState
    server_liveness: LivenessState
    endpoint: str | None
    log_location: str | None
    recovery_action: str | None = None
    detail: str | None = None
    manager_ownership: ManagerOwnershipState = ManagerOwnershipState.UNKNOWN

    @property
    def ok(self) -> bool:
        return (
            self.support is SupportState.SUPPORTED
            and self.registration is RegistrationState.INSTALLED
            and self.definition is DefinitionState.CURRENT
            and self.manager_ownership is ManagerOwnershipState.OWNED
            and self.manager is ManagerState.ACTIVE
            and self.server_liveness is LivenessState.LIVE
        )

    def as_json(self) -> dict[str, str | None]:
        return {
            "support": self.support.value,
            "registration": self.registration.value,
            "definition": self.definition.value,
            "manager_ownership": self.manager_ownership.value,
            "manager": self.manager.value,
            "server_liveness": self.server_liveness.value,
            "endpoint": self.endpoint,
            "log_location": self.log_location,
            "recovery_action": self.recovery_action,
            "detail": self.detail,
        }


class ServiceError(RuntimeError):
    """Report a safe, actionable personal-service operation failure."""

    def __init__(
        self,
        message: str,
        *,
        exit_code: int = 1,
        status: ServiceStatus | None = None,
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.status = status


def _required_string(value: dict[str, object], name: str) -> str:
    field = value.get(name)
    if not isinstance(field, str) or not field:
        raise ValueError(  # noqa: TRY003
            f"service definition field {name!r} must be a non-empty string"
        )
    return field


def _required_int(value: dict[str, object], name: str) -> int:
    field = value.get(name)
    if not isinstance(field, int) or isinstance(field, bool):
        raise TypeError(f"service definition field {name!r} must be an integer")  # noqa: TRY003
    return field


def _optional_int(value: dict[str, object], name: str, *, default: int) -> int:
    if name not in value:
        return default
    return _required_int(value, name)


__all__ = [
    "DEFINITION_VERSION",
    "OWNERSHIP_MARKER",
    "DefinitionState",
    "EnvironmentFileIdentity",
    "LivenessState",
    "ManagerOwnershipState",
    "ManagerRegistration",
    "ManagerState",
    "NativeRegistration",
    "ProbeResult",
    "ProbeState",
    "RegistrationState",
    "ServiceDefinition",
    "ServiceError",
    "ServiceStatus",
    "SupportState",
]
