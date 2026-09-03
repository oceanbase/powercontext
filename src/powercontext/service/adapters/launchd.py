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

"""macOS per-user LaunchAgent adapter for the personal PowerContext Server."""

from __future__ import annotations

import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from powercontext.service.adapters.base import atomic_write, decode_metadata, encode_metadata, inspect_artifact
from powercontext.service.model import (
    DEFINITION_VERSION,
    OWNERSHIP_MARKER,
    ManagerOwnershipState,
    ManagerRegistration,
    ManagerState,
    NativeRegistration,
    RegistrationState,
    ServiceDefinition,
    ServiceError,
    SupportState,
)

_FAILURE_LIMIT = 3
_FAILURE_WINDOW_SECONDS = 60
_BOOTOUT_TIMEOUT_SECONDS = 5.0
_LEGACY_DEFINITION_VERSION = 1


class LaunchdUserAdapter:
    identifier = "com.oceanbase.powercontext"

    def __init__(
        self,
        *,
        home: Path | None = None,
        uid: int | None = None,
        identifier: str | None = None,
    ) -> None:
        self.identifier = identifier or type(self).identifier
        user_home = home or Path.home()
        self.artifact_path = user_home / "Library" / "LaunchAgents" / f"{self.identifier}.plist"
        self.lock_path = self.artifact_path.with_name(f".{self.identifier}.lock")
        getuid = getattr(os, "getuid", None)
        self._uid = (getuid() if getuid is not None else 0) if uid is None else uid

    @property
    def _domain(self) -> str:
        return f"gui/{self._uid}"

    @property
    def _target(self) -> str:
        return f"{self._domain}/{self.identifier}"

    def platform_support(self) -> tuple[SupportState, str]:
        if sys.platform != "darwin":
            return SupportState.UNSUPPORTED, "LaunchAgents are available only on macOS"
        if shutil.which("launchctl") is None:
            return SupportState.UNSUPPORTED, "launchctl is not installed or is not on PATH"
        return SupportState.SUPPORTED, "launchd is available"

    def support(self) -> tuple[SupportState, str]:
        support, detail = self.platform_support()
        if support is SupportState.UNSUPPORTED:
            return support, detail
        result = self._run("print", self._domain, check=False)
        if result.returncode != 0:
            return SupportState.UNSUPPORTED, "the current launchd user domain is unavailable"
        return SupportState.SUPPORTED, "the launchd user domain is available"

    def inspect(self) -> NativeRegistration:
        state, content, detail = inspect_artifact(self.artifact_path)
        if state is not RegistrationState.INSTALLED or content is None:
            return NativeRegistration(state, content=content, detail=detail)
        try:
            payload = plistlib.loads(content)
            definition, metadata = _definition_and_metadata_from_payload(payload)
            if definition.definition_version == DEFINITION_VERSION:
                expected = plistlib.loads(self.render(definition))
            elif definition.definition_version == _LEGACY_DEFINITION_VERSION:
                expected = _legacy_payload(self.identifier, definition, metadata)
            else:
                expected = None
        except (TypeError, ValueError, plistlib.InvalidFileException) as error:
            return NativeRegistration(RegistrationState.INVALID, content=content, detail=str(error))
        if definition.ownership != OWNERSHIP_MARKER or expected is None or payload != expected:
            return NativeRegistration(
                RegistrationState.INVALID,
                content=content,
                detail="the installed LaunchAgent does not match its PowerContext metadata",
            )
        return NativeRegistration(RegistrationState.INSTALLED, definition=definition, content=content)

    def loaded_registration(self) -> ManagerRegistration:
        result = self._run("print", self._target, check=False)
        if result.returncode != 0:
            if _launchd_job_is_missing(result):
                return ManagerRegistration(ManagerOwnershipState.NOT_LOADED)
            return ManagerRegistration(
                ManagerOwnershipState.UNKNOWN,
                detail=f"cannot inspect loaded LaunchAgent{_command_detail(result.stderr)}",
            )
        metadata = _launchd_environment_value(result.stdout, "POWERCONTEXT_SERVICE_METADATA")
        owned = _launchd_environment_value(result.stdout, "POWERCONTEXT_SERVICE_OWNED")
        if owned != "true" or metadata is None:
            return ManagerRegistration(
                ManagerOwnershipState.FOREIGN,
                detail=f"loaded LaunchAgent {self.identifier} has no PowerContext ownership metadata",
            )
        try:
            definition = decode_metadata(metadata)
        except ValueError as error:
            return ManagerRegistration(ManagerOwnershipState.FOREIGN, detail=str(error))
        path = _launchd_scalar(result.stdout, "path")
        program = _launchd_scalar(result.stdout, "program")
        arguments = _launchd_block(result.stdout, "arguments")
        expected_arguments = (
            _launcher_arguments(definition)
            if definition.definition_version == DEFINITION_VERSION
            else _legacy_launcher_arguments(definition)
        )
        if (
            definition.ownership != OWNERSHIP_MARKER
            or path is None
            or os.path.abspath(path) != os.path.abspath(self.artifact_path)
            or program != expected_arguments[0]
            or arguments != expected_arguments
        ):
            return ManagerRegistration(
                ManagerOwnershipState.FOREIGN,
                definition=definition,
                detail=f"loaded LaunchAgent {self.identifier} does not match the PowerContext definition",
            )
        return ManagerRegistration(ManagerOwnershipState.OWNED, definition=definition)

    def render(self, definition: ServiceDefinition) -> bytes:
        log_dir = Path(definition.data_dir) / "logs"
        retry_token = _retry_token_path(definition)
        payload: dict[str, Any] = {
            "Label": self.identifier,
            "ProgramArguments": _launcher_arguments(definition),
            "RunAtLoad": True,
            "KeepAlive": {"PathState": {str(retry_token): True}},
            "ThrottleInterval": 5,
            "ProcessType": "Background",
            "StandardOutPath": str(log_dir / "server.stdout.log"),
            "StandardErrorPath": str(log_dir / "server.stderr.log"),
            "EnvironmentVariables": {
                "POWERCONTEXT_SERVICE_OWNED": "true",
                "POWERCONTEXT_SERVICE_METADATA": encode_metadata(definition),
            },
        }
        return plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True)

    def write(self, content: bytes) -> None:
        definition, _metadata = _definition_and_metadata_from_payload(plistlib.loads(content))
        (Path(definition.data_dir) / "logs").mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic_write(self.artifact_path, content)

    def restore(self, content: bytes | None) -> None:
        if content is None:
            self.artifact_path.unlink(missing_ok=True)
        else:
            atomic_write(self.artifact_path, content)

    def reload(self) -> None:
        return None

    def enable(self) -> None:
        registration = self.inspect()
        _require_owned_or_not_loaded(self.loaded_registration())
        self._run("enable", self._target)
        if registration.definition is not None:
            log_dir = Path(registration.definition.data_dir) / "logs"
            log_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
            _failure_state_path(registration.definition).unlink(missing_ok=True)
            atomic_write(_retry_token_path(registration.definition), b"enabled\n", mode=0o600)

    def start(self, *, reload_definition: bool) -> None:
        loaded = self.loaded_registration()
        _require_owned_or_not_loaded(loaded)
        is_loaded = loaded.state is ManagerOwnershipState.OWNED
        if is_loaded and reload_definition:
            self._bootout_and_wait()
            is_loaded = False
        if not is_loaded:
            self._run("bootstrap", self._domain, str(self.artifact_path))
        elif self.manager_state() is not ManagerState.ACTIVE:
            self._run("kickstart", "-k", self._target)

    def stop(self) -> None:
        loaded = self.loaded_registration()
        _require_owned_or_not_loaded(loaded)
        if loaded.state is ManagerOwnershipState.OWNED:
            self._bootout_and_wait()

    def disable(self) -> None:
        _require_owned_or_not_loaded(self.loaded_registration())
        self._run("disable", self._target)
        registration = self.inspect()
        if registration.definition is not None:
            _clear_retry_files(registration.definition)

    def remove(self) -> None:
        self.artifact_path.unlink(missing_ok=True)

    def manager_state(self) -> ManagerState:
        result = self._run("print", self._target, check=False)
        if result.returncode != 0:
            return ManagerState.INACTIVE if _launchd_job_is_missing(result) else ManagerState.UNKNOWN
        return _manager_state_from_output(result.stdout)

    def log_location(self, definition: ServiceDefinition | None) -> str | None:
        if definition is None:
            return None
        return str(Path(definition.data_dir) / "logs")

    def uninstall_recovery(self, stage: str) -> str:
        commands = {
            "stop": f"launchctl bootout {self._target}",
            "disable": f"launchctl disable {self._target}",
            "remove": f"rm -- {shlex.quote(str(self.artifact_path))}",
            "reload": f"launchctl print {self._target}",
        }
        return commands.get(stage, f"inspect `launchctl print {self._target}`")

    def _bootout_and_wait(self) -> None:
        self._run("bootout", self._target)
        deadline = time.monotonic() + _BOOTOUT_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            loaded = self.loaded_registration()
            _require_owned_or_not_loaded(loaded)
            if loaded.state is ManagerOwnershipState.NOT_LOADED:
                return
            time.sleep(0.05)
        raise ServiceError(f"launchd did not unload {self.identifier} after bootout")  # noqa: TRY003

    def _run(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        command = ["launchctl", *arguments]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)  # noqa: S603
        except (OSError, subprocess.SubprocessError) as error:
            raise ServiceError(f"failed to execute launchctl: {error}") from error  # noqa: TRY003
        if check and result.returncode != 0:
            detail = _command_detail(result.stderr)
            raise ServiceError(f"launchctl {arguments[0]} failed{detail}")  # noqa: TRY003
        return result


def _definition_and_metadata_from_payload(payload: dict[str, Any]) -> tuple[ServiceDefinition, str]:
    environment = payload.get("EnvironmentVariables")
    if not isinstance(environment, dict) or environment.get("POWERCONTEXT_SERVICE_OWNED") != "true":
        raise ValueError("LaunchAgent is missing the PowerContext ownership marker")  # noqa: TRY003
    metadata = environment.get("POWERCONTEXT_SERVICE_METADATA")
    if not isinstance(metadata, str):
        raise TypeError("LaunchAgent is missing PowerContext service metadata")  # noqa: TRY003
    return decode_metadata(metadata), metadata


def _legacy_payload(identifier: str, definition: ServiceDefinition, metadata: str) -> dict[str, Any]:
    log_dir = Path(definition.data_dir) / "logs"
    return {
        "Label": identifier,
        "ProgramArguments": _legacy_launcher_arguments(definition),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 5,
        "ProcessType": "Background",
        "StandardOutPath": str(log_dir / "server.stdout.log"),
        "StandardErrorPath": str(log_dir / "server.stderr.log"),
        "EnvironmentVariables": {
            "POWERCONTEXT_SERVICE_OWNED": "true",
            "POWERCONTEXT_SERVICE_METADATA": metadata,
        },
    }


def _failure_state_path(definition: ServiceDefinition) -> Path:
    return Path(definition.data_dir) / "logs" / "launchd-retry-state.json"


def _retry_token_path(definition: ServiceDefinition) -> Path:
    return Path(definition.data_dir) / "logs" / "launchd-retry.enabled"


def _clear_retry_files(definition: ServiceDefinition) -> None:
    _failure_state_path(definition).unlink(missing_ok=True)
    _retry_token_path(definition).unlink(missing_ok=True)


def _launcher_arguments(definition: ServiceDefinition) -> list[str]:
    return [
        definition.python_executable,
        "-m",
        "powercontext_service_bootstrap",
        "--retry-state",
        str(_failure_state_path(definition)),
        "--retry-token",
        str(_retry_token_path(definition)),
        "--retry-limit",
        str(_FAILURE_LIMIT),
        "--retry-window-seconds",
        str(_FAILURE_WINDOW_SECONDS),
        "--",
        *definition.launcher_arguments()[3:],
    ]


def _legacy_launcher_arguments(definition: ServiceDefinition) -> list[str]:
    return [
        *definition.launcher_arguments(include_env_identity=False),
        "--failure-state",
        str(_failure_state_path(definition)),
        "--failure-limit",
        str(_FAILURE_LIMIT),
        "--failure-window-seconds",
        str(_FAILURE_WINDOW_SECONDS),
    ]


def _manager_state_from_output(output: str) -> ManagerState:
    if re.search(r"^\s*state = running\s*$", output, re.MULTILINE):
        return ManagerState.ACTIVE
    if re.search(r"^\s*state = (?:exited|not running)\s*$", output, re.MULTILINE):
        exit_code = re.search(r"^\s*last exit code = (-?\d+)\s*$", output, re.MULTILINE)
        signal = re.search(r"^\s*last terminating signal = .+$", output, re.MULTILINE)
        if signal is not None:
            return ManagerState.FAILED
        if exit_code is None:
            return (
                ManagerState.INACTIVE
                if re.search(r"^\s*state = not running\s*$", output, re.MULTILINE)
                else ManagerState.UNKNOWN
            )
        return ManagerState.INACTIVE if int(exit_code.group(1)) == 0 else ManagerState.FAILED
    return ManagerState.UNKNOWN


def _launchd_job_is_missing(result: subprocess.CompletedProcess[str]) -> bool:
    detail = f"{result.stdout}\n{result.stderr}".lower()
    return result.returncode == 113 or "could not find service" in detail or "not found" in detail


def _launchd_scalar(output: str, name: str) -> str | None:
    match = re.search(rf"^\s*{re.escape(name)} = (.+?)\s*$", output, re.MULTILINE)
    return match.group(1) if match is not None else None


def _launchd_block(output: str, name: str) -> list[str]:
    match = re.search(rf"^\s*{re.escape(name)} = \{{\s*$\n(?P<body>.*?)^\s*\}}\s*$", output, re.MULTILINE | re.DOTALL)
    if match is None:
        return []
    return [line.strip() for line in match.group("body").splitlines() if line.strip()]


def _launchd_environment_value(output: str, name: str) -> str | None:
    match = re.search(rf"^\s*{re.escape(name)} => (.+?)\s*$", output, re.MULTILINE)
    return match.group(1) if match is not None else None


def _require_owned_or_not_loaded(registration: ManagerRegistration) -> None:
    if registration.state in {ManagerOwnershipState.FOREIGN, ManagerOwnershipState.UNKNOWN}:
        raise ServiceError(registration.detail or "cannot verify the loaded LaunchAgent ownership")


def _command_detail(stderr: str) -> str:
    detail = " ".join(stderr.strip().splitlines())
    return f": {detail[:500]}" if detail else ""


__all__ = ["LaunchdUserAdapter"]
