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

"""Protocol and safe filesystem helpers shared by native service adapters."""

from __future__ import annotations

import base64
import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import Protocol

from powercontext.service.model import (
    DEFINITION_VERSION,
    DefinitionState,
    ManagerOwnershipState,
    ManagerRegistration,
    ManagerState,
    NativeRegistration,
    RegistrationState,
    ServiceDefinition,
    ServiceError,
    SupportState,
)


class NativeServiceAdapter(Protocol):
    identifier: str
    artifact_path: Path
    lock_path: Path

    def platform_support(self) -> tuple[SupportState, str]: ...

    def support(self) -> tuple[SupportState, str]: ...

    def inspect(self) -> NativeRegistration: ...

    def loaded_registration(self) -> ManagerRegistration: ...

    def render(self, definition: ServiceDefinition) -> bytes: ...

    def write(self, content: bytes) -> None: ...

    def restore(self, content: bytes | None) -> None: ...

    def reload(self) -> None: ...

    def enable(self) -> None: ...

    def start(self, *, reload_definition: bool) -> None: ...

    def stop(self) -> None: ...

    def disable(self) -> None: ...

    def remove(self) -> None: ...

    def manager_state(self) -> ManagerState: ...

    def log_location(self, definition: ServiceDefinition | None) -> str | None: ...

    def uninstall_recovery(self, stage: str) -> str: ...


class UnsupportedAdapter:
    identifier = "powercontext"
    artifact_path = Path("/unsupported/powercontext")
    lock_path = Path("/unsupported/powercontext.lock")

    def __init__(self, detail: str) -> None:
        self._detail = detail

    def support(self) -> tuple[SupportState, str]:
        return SupportState.UNSUPPORTED, self._detail

    def platform_support(self) -> tuple[SupportState, str]:
        return self.support()

    def inspect(self) -> NativeRegistration:
        return NativeRegistration(RegistrationState.UNKNOWN, detail=self._detail)

    def loaded_registration(self) -> ManagerRegistration:
        return ManagerRegistration(ManagerOwnershipState.UNKNOWN, detail=self._detail)

    def render(self, definition: ServiceDefinition) -> bytes:
        raise ServiceError(self._detail)

    def write(self, content: bytes) -> None:
        raise ServiceError(self._detail)

    def restore(self, content: bytes | None) -> None:
        raise ServiceError(self._detail)

    def reload(self) -> None:
        raise ServiceError(self._detail)

    def enable(self) -> None:
        raise ServiceError(self._detail)

    def start(self, *, reload_definition: bool) -> None:
        raise ServiceError(self._detail)

    def stop(self) -> None:
        raise ServiceError(self._detail)

    def disable(self) -> None:
        raise ServiceError(self._detail)

    def remove(self) -> None:
        raise ServiceError(self._detail)

    def manager_state(self) -> ManagerState:
        return ManagerState.UNKNOWN

    def log_location(self, definition: ServiceDefinition | None) -> str | None:
        return None

    def uninstall_recovery(self, stage: str) -> str:
        return self._detail


def encode_metadata(definition: ServiceDefinition) -> str:
    content = json.dumps(definition.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(content).decode("ascii")


def decode_metadata(value: str) -> ServiceDefinition:
    try:
        content = base64.urlsafe_b64decode(value.encode("ascii"))
        payload = json.loads(content)
        return ServiceDefinition.from_dict(payload)
    except (UnicodeError, ValueError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("invalid PowerContext service metadata") from error  # noqa: TRY003


def inspect_artifact(path: Path) -> tuple[RegistrationState, bytes | None, str | None]:
    try:
        path.lstat()
    except FileNotFoundError:
        return RegistrationState.NOT_INSTALLED, None, None
    except OSError as error:
        return RegistrationState.UNKNOWN, None, f"cannot inspect {path}: {error}"
    if path.is_symlink() or not path.is_file():
        return RegistrationState.INVALID, None, f"service artifact is not a regular file: {path}"
    try:
        return RegistrationState.INSTALLED, path.read_bytes(), None
    except OSError as error:
        return RegistrationState.UNKNOWN, None, f"cannot read {path}: {error}"


def atomic_write(path: Path, content: bytes, *, mode: int = 0o644) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except BaseException:
        with suppress(OSError):
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise


def definition_state(
    definition: ServiceDefinition,
    *,
    package_version: str,
    python_executable: str,
) -> DefinitionState:
    if not Path(definition.python_executable).is_file():
        return DefinitionState.MISSING_EXECUTABLE
    if (
        definition.definition_version != DEFINITION_VERSION
        or definition.package_version != package_version
        or os.path.abspath(definition.python_executable) != os.path.abspath(python_executable)
    ):
        return DefinitionState.STALE
    if definition.env_file is not None:
        from powercontext.service.environment import environment_identity_is_current

        if not environment_identity_is_current(definition.env_file):
            return DefinitionState.STALE
    return DefinitionState.CURRENT


__all__ = [
    "NativeServiceAdapter",
    "UnsupportedAdapter",
    "atomic_write",
    "decode_metadata",
    "definition_state",
    "encode_metadata",
    "inspect_artifact",
]
