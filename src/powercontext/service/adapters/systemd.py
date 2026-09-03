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

"""Linux ``systemd --user`` adapter for the personal PowerContext Server."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

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

_METADATA = re.compile(rb"^# X-PowerContext-Metadata: ([A-Za-z0-9_=-]+)$", re.MULTILINE)
_LEGACY_DEFINITION_VERSION = 1


class SystemdUserAdapter:
    identifier = "powercontext.service"

    def __init__(self, *, config_home: Path | None = None, identifier: str | None = None) -> None:
        self.identifier = identifier or type(self).identifier
        root = _systemd_config_home() if config_home is None else config_home
        self.artifact_path = root / "systemd" / "user" / self.identifier
        self.lock_path = self.artifact_path.with_name(f".{self.identifier}.lock")

    def platform_support(self) -> tuple[SupportState, str]:
        if sys.platform != "linux":
            return SupportState.UNSUPPORTED, "systemd user services are available only on Linux"
        if shutil.which("systemctl") is None:
            return SupportState.UNSUPPORTED, "systemctl is not installed or is not on PATH"
        return SupportState.SUPPORTED, "systemd user services are available"

    def support(self) -> tuple[SupportState, str]:
        support, detail = self.platform_support()
        if support is SupportState.UNSUPPORTED:
            return support, detail
        result = self._run("show-environment", check=False)
        if result.returncode != 0:
            return SupportState.UNSUPPORTED, "the current user has no available systemd user manager"
        return SupportState.SUPPORTED, "systemd --user is available"

    def inspect(self) -> NativeRegistration:
        state, content, detail = inspect_artifact(self.artifact_path)
        if state is not RegistrationState.INSTALLED or content is None:
            return NativeRegistration(state, content=content, detail=detail)
        match = _METADATA.search(content)
        if not content.startswith(b"# Managed by PowerContext\n") or match is None:
            return NativeRegistration(
                RegistrationState.INVALID,
                content=content,
                detail=f"{self.artifact_path} is not owned by PowerContext",
            )
        try:
            metadata = match.group(1).decode("ascii")
            definition = decode_metadata(metadata)
        except (UnicodeError, ValueError) as error:
            return NativeRegistration(RegistrationState.INVALID, content=content, detail=str(error))
        if definition.definition_version == DEFINITION_VERSION:
            expected = self.render(definition)
        elif definition.definition_version == _LEGACY_DEFINITION_VERSION:
            expected = _render_legacy(definition, metadata)
        else:
            expected = None
        if definition.ownership != OWNERSHIP_MARKER or expected is None or expected != content:
            return NativeRegistration(
                RegistrationState.INVALID,
                content=content,
                detail="the installed systemd unit does not match its PowerContext metadata",
            )
        return NativeRegistration(RegistrationState.INSTALLED, definition=definition, content=content)

    def loaded_registration(self) -> ManagerRegistration:
        result = self._run(
            "show",
            "--property=LoadState",
            "--property=FragmentPath",
            "--property=ExecStart",
            "--property=Environment",
            self.identifier,
            check=False,
        )
        properties = _systemd_properties(result.stdout)
        if properties.get("LoadState") == "not-found":
            return ManagerRegistration(ManagerOwnershipState.NOT_LOADED)
        if result.returncode != 0:
            return ManagerRegistration(
                ManagerOwnershipState.UNKNOWN,
                detail=f"cannot inspect loaded systemd unit{_command_detail(result.stderr)}",
            )
        if properties.get("LoadState") is None:
            return ManagerRegistration(ManagerOwnershipState.NOT_LOADED)
        metadata = _systemd_environment_value(properties.get("Environment", ""), "POWERCONTEXT_SERVICE_METADATA")
        owned = _systemd_environment_value(properties.get("Environment", ""), "POWERCONTEXT_SERVICE_OWNED")
        if owned != "true" or metadata is None:
            artifact = self.inspect()
            if (
                artifact.definition is not None
                and artifact.definition.definition_version < DEFINITION_VERSION
                and os.path.abspath(properties.get("FragmentPath", "")) == os.path.abspath(self.artifact_path)
                and _systemd_execstart_matches(
                    properties.get("ExecStart", ""),
                    artifact.definition.launcher_arguments(include_env_identity=False),
                )
            ):
                return ManagerRegistration(ManagerOwnershipState.OWNED, definition=artifact.definition)
            return ManagerRegistration(
                ManagerOwnershipState.FOREIGN,
                detail=f"loaded systemd unit {self.identifier} has no PowerContext ownership metadata",
            )
        try:
            definition = decode_metadata(metadata)
        except ValueError as error:
            return ManagerRegistration(ManagerOwnershipState.FOREIGN, detail=str(error))
        fragment = properties.get("FragmentPath", "")
        if (
            definition.ownership != OWNERSHIP_MARKER
            or os.path.abspath(fragment) != os.path.abspath(self.artifact_path)
            or not _systemd_execstart_matches(properties.get("ExecStart", ""), definition.launcher_arguments())
        ):
            return ManagerRegistration(
                ManagerOwnershipState.FOREIGN,
                definition=definition,
                detail=f"loaded systemd unit {self.identifier} does not match the PowerContext definition",
            )
        return ManagerRegistration(ManagerOwnershipState.OWNED, definition=definition)

    def render(self, definition: ServiceDefinition) -> bytes:
        metadata = encode_metadata(definition)
        command = " ".join(_systemd_quote(argument) for argument in definition.launcher_arguments())
        return (
            "# Managed by PowerContext\n"
            f"# X-PowerContext-Metadata: {metadata}\n"
            "[Unit]\n"
            "Description=PowerContext personal Server\n"
            "After=network.target\n"
            "StartLimitIntervalSec=60\n"
            "StartLimitBurst=3\n"
            "\n"
            "[Service]\n"
            "Type=simple\n"
            "Environment=POWERCONTEXT_SERVICE_OWNED=true\n"
            f"Environment=POWERCONTEXT_SERVICE_METADATA={metadata}\n"
            f"ExecStart={command}\n"
            "Restart=on-failure\n"
            "RestartSec=5s\n"
            "TimeoutStopSec=30s\n"
            "\n"
            "[Install]\n"
            "WantedBy=default.target\n"
        ).encode()

    def write(self, content: bytes) -> None:
        atomic_write(self.artifact_path, content)

    def restore(self, content: bytes | None) -> None:
        if content is None:
            self.artifact_path.unlink(missing_ok=True)
        else:
            atomic_write(self.artifact_path, content)

    def reload(self) -> None:
        self._run("daemon-reload")

    def enable(self) -> None:
        _require_owned_or_not_loaded(self.loaded_registration())
        self._run("enable", self.identifier)

    def start(self, *, reload_definition: bool) -> None:
        loaded = self.loaded_registration()
        _require_owned_or_not_loaded(loaded)
        command = "restart" if reload_definition else "start"
        self._run(command, self.identifier)

    def stop(self) -> None:
        loaded = self.loaded_registration()
        _require_owned_or_not_loaded(loaded)
        if loaded.state is ManagerOwnershipState.NOT_LOADED:
            return
        if self.manager_state() is not ManagerState.INACTIVE:
            self._run("stop", self.identifier)

    def disable(self) -> None:
        _require_owned_or_not_loaded(self.loaded_registration())
        self._run("disable", self.identifier)

    def remove(self) -> None:
        self.artifact_path.unlink(missing_ok=True)

    def manager_state(self) -> ManagerState:
        result = self._run("show", "--property=ActiveState", "--value", self.identifier, check=False)
        if result.returncode != 0:
            return ManagerState.UNKNOWN
        state = result.stdout.strip()
        if state == "active":
            return ManagerState.ACTIVE
        if state == "failed":
            return ManagerState.FAILED
        if state == "inactive":
            return ManagerState.INACTIVE
        return ManagerState.UNKNOWN

    def log_location(self, definition: ServiceDefinition | None) -> str | None:
        return f"journalctl --user --unit {self.identifier}"

    def uninstall_recovery(self, stage: str) -> str:
        commands = {
            "stop": f"systemctl --user stop {self.identifier}",
            "disable": f"systemctl --user disable {self.identifier}",
            "remove": f"rm -- {shlex.quote(str(self.artifact_path))}",
            "reload": "systemctl --user daemon-reload",
        }
        return commands.get(stage, f"inspect `systemctl --user status {self.identifier}`")

    def _run(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        command = ["systemctl", "--user", *arguments]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)  # noqa: S603
        except (OSError, subprocess.SubprocessError) as error:
            raise ServiceError(f"failed to execute systemctl --user: {error}") from error  # noqa: TRY003
        if check and result.returncode != 0:
            detail = _command_detail(result.stderr)
            raise ServiceError(f"systemctl --user {arguments[0]} failed{detail}")  # noqa: TRY003
        return result


def _systemd_quote(value: str) -> str:
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ServiceError(  # noqa: TRY003
            "service command arguments must not contain control characters", exit_code=2
        )
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$")
    return f'"{escaped}"'


def _render_legacy(definition: ServiceDefinition, metadata: str) -> bytes:
    command = " ".join(
        _systemd_quote(argument) for argument in definition.launcher_arguments(include_env_identity=False)
    )
    return (
        "# Managed by PowerContext\n"
        f"# X-PowerContext-Metadata: {metadata}\n"
        "[Unit]\n"
        "Description=PowerContext personal Server\n"
        "After=network.target\n"
        "StartLimitIntervalSec=60\n"
        "StartLimitBurst=3\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={command}\n"
        "Restart=on-failure\n"
        "RestartSec=5s\n"
        "TimeoutStopSec=30s\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    ).encode()


def _systemd_properties(output: str) -> dict[str, str]:
    properties: dict[str, str] = {}
    for line in output.splitlines():
        name, separator, value = line.partition("=")
        if separator:
            properties[name] = value
    return properties


def _systemd_environment_value(environment: str, name: str) -> str | None:
    prefix = f"{name}="
    for item in environment.split():
        normalized = item.strip('"')
        if normalized.startswith(prefix):
            return normalized[len(prefix) :]
    return None


def _systemd_execstart_matches(output: str, arguments: list[str]) -> bool:
    match = re.search(
        r"(?:^|\{\s*)path=(?P<path>.*?)\s*;\s*argv\[\]=(?P<arguments>.*?)\s*;\s*ignore_errors=",
        output,
    )
    if match is None or not arguments:
        return False
    expected_plain = " ".join(arguments)
    expected_escaped = " ".join(argument.replace(" ", r"\x20") for argument in arguments)
    executable = arguments[0]
    return match.group("path") in {executable, executable.replace(" ", r"\x20")} and match.group("arguments") in {
        expected_plain,
        expected_escaped,
    }


def _require_owned_or_not_loaded(registration: ManagerRegistration) -> None:
    if registration.state in {ManagerOwnershipState.FOREIGN, ManagerOwnershipState.UNKNOWN}:
        raise ServiceError(registration.detail or "cannot verify the loaded systemd unit ownership")


def _systemd_config_home() -> Path:
    configured = os.environ.get("XDG_CONFIG_HOME")
    if configured:
        candidate = Path(configured)
        if candidate.is_absolute():
            return candidate
    return Path.home() / ".config"


def _command_detail(stderr: str) -> str:
    detail = " ".join(stderr.strip().splitlines())
    return f": {detail[:500]}" if detail else ""


__all__: Sequence[str] = ["SystemdUserAdapter"]
