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

from __future__ import annotations

import json
import os
import plistlib
import socket
import subprocess
import sys
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest
from typer.testing import CliRunner

import powercontext_service_bootstrap.__main__ as service_bootstrap
from powercontext.paths import POWERCONTEXT_HOME_ENV, powercontext_data_dir
from powercontext.service import launcher as service_launcher
from powercontext.service import probe as service_probe
from powercontext.service.adapters.base import decode_metadata, definition_state, encode_metadata
from powercontext.service.adapters.launchd import LaunchdUserAdapter
from powercontext.service.adapters.systemd import SystemdUserAdapter
from powercontext.service.cli import app as service_app
from powercontext.service.controller import ServiceController
from powercontext.service.model import (
    DEFINITION_VERSION,
    OWNERSHIP_MARKER,
    DefinitionState,
    EnvironmentFileIdentity,
    LivenessState,
    ManagerOwnershipState,
    ManagerRegistration,
    ManagerState,
    NativeRegistration,
    ProbeResult,
    ProbeState,
    RegistrationState,
    ServiceDefinition,
    ServiceError,
    ServiceStatus,
    SupportState,
)
from powercontext.service.probe import probe_server


def _definition(tmp_path: Path, **overrides: object) -> ServiceDefinition:
    values = {
        "ownership": OWNERSHIP_MARKER,
        "definition_version": DEFINITION_VERSION,
        "package_version": version("powercontext"),
        "python_executable": os.path.abspath(sys.executable),
        "endpoint": "http://127.0.0.1:8000",
        "data_dir": str(tmp_path / "data"),
        "env_file": None,
        **overrides,
    }
    return ServiceDefinition(**values)


class FakeAdapter:
    identifier = "powercontext.test"

    def __init__(self, tmp_path: Path) -> None:
        self.artifact_path = tmp_path / "powercontext.test"
        self.lock_path = tmp_path / ".powercontext.test.lock"
        self.definition: ServiceDefinition | None = None
        self.content: bytes | None = None
        self.manager = ManagerState.INACTIVE
        self.loaded_definition: ServiceDefinition | None = None
        self.manager_registration_override: ManagerRegistration | None = None
        self.events: list[str] = []
        self.fail_enable = False
        self.fail_start = False
        self.fail_stop = False
        self.fail_disable = False
        self.fail_remove = False
        self.fail_reload = False

    def support(self) -> tuple[SupportState, str]:
        return SupportState.SUPPORTED, "fake manager available"

    def platform_support(self) -> tuple[SupportState, str]:
        return SupportState.SUPPORTED, "fake platform available"

    def inspect(self) -> NativeRegistration:
        if self.definition is None:
            return NativeRegistration(RegistrationState.NOT_INSTALLED)
        return NativeRegistration(RegistrationState.INSTALLED, self.definition, self.content)

    def loaded_registration(self) -> ManagerRegistration:
        if self.manager_registration_override is not None:
            return self.manager_registration_override
        if self.loaded_definition is None:
            return ManagerRegistration(ManagerOwnershipState.NOT_LOADED)
        return ManagerRegistration(ManagerOwnershipState.OWNED, definition=self.loaded_definition)

    def render(self, definition: ServiceDefinition) -> bytes:
        return encode_metadata(definition).encode()

    def write(self, content: bytes) -> None:
        self.events.append("write")
        self.content = content
        self.definition = decode_metadata(content.decode())

    def restore(self, content: bytes | None) -> None:
        self.events.append("restore")
        self.content = content
        self.definition = decode_metadata(content.decode()) if content is not None else None

    def reload(self) -> None:
        self.events.append("reload")
        if self.fail_reload:
            raise ServiceError("reload failed")  # noqa: TRY003

    def enable(self) -> None:
        self.events.append("enable")
        if self.fail_enable:
            raise ServiceError("enable failed")  # noqa: TRY003

    def start(self, *, reload_definition: bool) -> None:
        self.events.append(f"start:{reload_definition}")
        if self.fail_start:
            raise ServiceError("start failed")  # noqa: TRY003
        self.manager = ManagerState.ACTIVE
        self.loaded_definition = self.definition

    def stop(self) -> None:
        self.events.append("stop")
        if self.fail_stop:
            raise ServiceError("stop failed")  # noqa: TRY003
        self.manager = ManagerState.INACTIVE
        self.loaded_definition = None

    def disable(self) -> None:
        self.events.append("disable")
        if self.fail_disable:
            raise ServiceError("disable failed")  # noqa: TRY003

    def remove(self) -> None:
        self.events.append("remove")
        if self.fail_remove:
            raise ServiceError("remove failed")  # noqa: TRY003
        self.definition = None
        self.content = None

    def manager_state(self) -> ManagerState:
        return self.manager

    def log_location(self, definition: ServiceDefinition | None) -> str | None:
        return "fake logs"

    def uninstall_recovery(self, stage: str) -> str:
        return f"fake-manager recover {stage} {self.artifact_path}"


def _manager_probe(adapter: FakeAdapter):
    def probe(endpoint: str) -> ProbeResult:
        if adapter.manager is ManagerState.ACTIVE:
            return ProbeResult(ProbeState.LIVE, f"{endpoint} status=ok")
        return ProbeResult(ProbeState.UNREACHABLE, f"cannot reach {endpoint}")

    return probe


@pytest.fixture(autouse=True)
def _clear_server_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name == "POWERCONTEXT_HOME" or name.startswith("POWERCONTEXT_SERVER_"):
            monkeypatch.delenv(name, raising=False)


@pytest.mark.skipif(os.name == "nt", reason="POSIX virtualenv executables may be symbolic links")
def test_service_definition_preserves_virtualenv_python(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    virtualenv_python = tmp_path / "venv" / "bin" / "python"
    virtualenv_python.parent.mkdir(parents=True)
    virtualenv_python.symlink_to(Path(sys.executable))
    monkeypatch.setattr(sys, "executable", str(virtualenv_python))
    adapter = FakeAdapter(tmp_path)

    ServiceController(adapter, probe=_manager_probe(adapter), sleep=lambda _: None).install()

    assert adapter.definition is not None
    assert adapter.definition.python_executable == str(virtualenv_python)
    assert adapter.definition.python_executable != str(virtualenv_python.resolve())


@pytest.mark.skipif(os.name == "nt", reason="POSIX virtualenv executables may be symbolic links")
def test_recorded_virtualenv_python_can_import_service_launcher() -> None:
    virtualenv_python = Path(sys.executable)
    if virtualenv_python.resolve() == virtualenv_python:
        pytest.skip("the verification Python is not reached through a virtualenv symbolic link")

    result = subprocess.run(
        [str(virtualenv_python), "-m", "powercontext.service.launcher", "--help"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "powercontext-personal-service-launcher" in result.stdout


@pytest.mark.skipif(os.name == "nt", reason="POSIX virtualenv executables may be symbolic links")
def test_resolved_base_python_definition_is_stale(tmp_path: Path) -> None:
    virtualenv_python = tmp_path / "venv" / "bin" / "python"
    virtualenv_python.parent.mkdir(parents=True)
    virtualenv_python.symlink_to(Path(sys.executable).resolve())
    definition = _definition(tmp_path, python_executable=str(virtualenv_python.resolve()))

    assert (
        definition_state(
            definition,
            package_version=version("powercontext"),
            python_executable=str(virtualenv_python),
        )
        is DefinitionState.STALE
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX virtualenv executables may be symbolic links")
def test_install_reconciles_resolved_base_python_definition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    virtualenv_python = tmp_path / "venv" / "bin" / "python"
    virtualenv_python.parent.mkdir(parents=True)
    virtualenv_python.symlink_to(Path(sys.executable).resolve())
    monkeypatch.setattr(sys, "executable", str(virtualenv_python))
    adapter = FakeAdapter(tmp_path)
    old_definition = _definition(tmp_path, python_executable=str(virtualenv_python.resolve()))
    adapter.definition = old_definition
    adapter.content = adapter.render(old_definition)
    adapter.loaded_definition = old_definition
    adapter.manager = ManagerState.ACTIVE

    status = ServiceController(adapter, probe=_manager_probe(adapter), sleep=lambda _: None).install()

    assert status.definition is DefinitionState.CURRENT
    assert adapter.definition is not None
    assert adapter.definition.python_executable == str(virtualenv_python)
    assert adapter.events == ["write", "reload", "enable", "start:True"]


def test_missing_recorded_python_executable_is_reported(tmp_path: Path) -> None:
    definition = _definition(tmp_path, python_executable=str(tmp_path / "removed-venv" / "python"))

    assert (
        definition_state(
            definition,
            package_version=version("powercontext"),
            python_executable=definition.python_executable,
        )
        is DefinitionState.MISSING_EXECUTABLE
    )


def test_direct_python_executable_definition_remains_current(tmp_path: Path) -> None:
    executable = tmp_path / "python"
    executable.write_bytes(b"")
    definition = _definition(tmp_path, python_executable=str(executable))

    assert (
        definition_state(
            definition,
            package_version=definition.package_version,
            python_executable=str(executable),
        )
        is DefinitionState.CURRENT
    )


def test_service_controller_installs_and_starts_one_native_registration(tmp_path: Path) -> None:
    adapter = FakeAdapter(tmp_path)
    controller = ServiceController(adapter, probe=_manager_probe(adapter), sleep=lambda _: None)

    status = controller.install()

    assert status.ok
    assert adapter.definition is not None
    assert adapter.definition.endpoint == "http://127.0.0.1:8000"
    assert adapter.events == ["write", "reload", "enable", "start:True"]


def test_service_install_is_idempotent_when_definition_is_current(tmp_path: Path) -> None:
    adapter = FakeAdapter(tmp_path)
    controller = ServiceController(adapter, probe=_manager_probe(adapter), sleep=lambda _: None)
    controller.install()
    adapter.events.clear()

    status = controller.install()

    assert status.ok
    assert adapter.events == ["enable"]


def test_registration_status_does_not_query_manager_availability(tmp_path: Path) -> None:
    adapter = FakeAdapter(tmp_path)
    definition = _definition(tmp_path)
    adapter.definition = definition
    adapter.content = adapter.render(definition)
    support = Mock(side_effect=AssertionError("registration metadata must not query the manager"))
    adapter.support = support

    status = ServiceController(adapter).registration_status()

    assert status.registration is RegistrationState.INSTALLED
    assert status.endpoint == definition.endpoint
    support.assert_not_called()


def test_install_reloads_a_stale_loaded_definition_even_when_a_foreground_server_is_live(tmp_path: Path) -> None:
    adapter = FakeAdapter(tmp_path)
    ServiceController(adapter, probe=_manager_probe(adapter), sleep=lambda _: None).install()
    assert adapter.definition is not None
    current = adapter.definition
    adapter.loaded_definition = replace(current, package_version="old")
    adapter.manager = ManagerState.INACTIVE
    adapter.events.clear()
    controller = ServiceController(
        adapter,
        probe=lambda endpoint: ProbeResult(ProbeState.LIVE, f"{endpoint} status=ok"),
        sleep=lambda _: None,
    )

    status = controller.install()

    assert status.ok
    assert adapter.loaded_definition == current
    assert adapter.events == ["enable", "start:True"]


def test_service_install_restarts_an_active_but_unreachable_registration(tmp_path: Path) -> None:
    adapter = FakeAdapter(tmp_path)
    ServiceController(adapter, probe=_manager_probe(adapter), sleep=lambda _: None).install()
    adapter.events.clear()
    probes = iter([
        ProbeResult(ProbeState.UNREACHABLE, "hung process"),
        ProbeResult(ProbeState.LIVE, "restarted"),
        ProbeResult(ProbeState.LIVE, "restarted"),
    ])

    status = ServiceController(adapter, probe=lambda _: next(probes), sleep=lambda _: None).install()

    assert status.ok
    assert adapter.events == ["enable", "start:True"]


def test_service_install_rejects_a_non_powercontext_listener_before_writing(tmp_path: Path) -> None:
    adapter = FakeAdapter(tmp_path)
    controller = ServiceController(
        adapter,
        probe=lambda _: ProbeResult(ProbeState.CONFLICT, "invalid liveness response"),
    )

    with pytest.raises(ServiceError, match="another listener"):
        controller.install()

    assert adapter.events == []


def test_service_install_restores_the_previous_definition_when_enable_fails(tmp_path: Path) -> None:
    adapter = FakeAdapter(tmp_path)
    previous = _definition(tmp_path, package_version="old")
    adapter.write(adapter.render(previous))
    adapter.events.clear()
    adapter.fail_enable = True
    controller = ServiceController(adapter, probe=_manager_probe(adapter), sleep=lambda _: None)

    with pytest.raises(ServiceError, match="enable failed"):
        controller.install()

    assert adapter.definition == previous
    assert adapter.events == ["write", "reload", "enable", "disable", "restore", "reload", "enable"]


def test_service_install_preserves_status_when_the_native_start_command_fails(tmp_path: Path) -> None:
    adapter = FakeAdapter(tmp_path)
    adapter.fail_start = True
    controller = ServiceController(
        adapter,
        probe=lambda endpoint: ProbeResult(ProbeState.UNREACHABLE, f"cannot reach {endpoint}"),
    )

    with pytest.raises(ServiceError, match="native manager could not start") as raised:
        controller.install()

    assert raised.value.status is not None
    assert raised.value.status.registration is RegistrationState.INSTALLED
    assert raised.value.status.manager is ManagerState.INACTIVE
    assert raised.value.status.server_liveness is LivenessState.UNREACHABLE
    assert raised.value.status.log_location == "fake logs"


def test_service_uninstall_stops_before_removing_the_owned_definition(tmp_path: Path) -> None:
    adapter = FakeAdapter(tmp_path)
    controller = ServiceController(adapter, probe=_manager_probe(adapter), sleep=lambda _: None)
    controller.install()
    adapter.events.clear()

    status = controller.uninstall()

    assert status.registration is RegistrationState.NOT_INSTALLED
    assert adapter.events == ["stop", "disable", "remove", "reload"]


@pytest.mark.parametrize(
    ("stage", "flag", "expected_events"),
    [
        ("stop", "fail_stop", ["stop"]),
        ("disable", "fail_disable", ["stop", "disable"]),
        ("remove", "fail_remove", ["stop", "disable", "remove"]),
        ("reload", "fail_reload", ["stop", "disable", "remove", "reload"]),
    ],
)
def test_uninstall_partial_failure_reports_remaining_registration_and_recovery(
    tmp_path: Path,
    stage: str,
    flag: str,
    expected_events: list[str],
) -> None:
    adapter = FakeAdapter(tmp_path)
    controller = ServiceController(adapter, probe=_manager_probe(adapter), sleep=lambda _: None)
    controller.install()
    adapter.events.clear()
    setattr(adapter, flag, True)

    with pytest.raises(ServiceError, match=f"during {stage}") as raised:
        controller.uninstall()

    assert adapter.events == expected_events
    assert raised.value.status is not None
    assert raised.value.status.recovery_action is not None
    assert f"recover {stage}" in raised.value.status.recovery_action
    if stage in {"stop", "disable", "remove"}:
        assert str(adapter.artifact_path) in raised.value.status.recovery_action


@pytest.mark.parametrize(
    ("adapter", "stage", "expected"),
    [
        pytest.param(
            SystemdUserAdapter(config_home=Path("relative-systemd-home"), identifier="powercontext-test.service"),
            "disable",
            "systemctl --user disable powercontext-test.service",
            id="systemd-disable",
        ),
        pytest.param(
            SystemdUserAdapter(config_home=Path("relative-systemd-home"), identifier="powercontext-test.service"),
            "reload",
            "systemctl --user daemon-reload",
            id="systemd-reload",
        ),
        pytest.param(
            LaunchdUserAdapter(home=Path("relative-launchd-home"), uid=501, identifier="powercontext.test"),
            "stop",
            "launchctl bootout gui/501/powercontext.test",
            id="launchd-stop",
        ),
        pytest.param(
            LaunchdUserAdapter(home=Path("relative-launchd-home"), uid=501, identifier="powercontext.test"),
            "disable",
            "launchctl disable gui/501/powercontext.test",
            id="launchd-disable",
        ),
    ],
)
def test_native_uninstall_recovery_uses_an_exact_scoped_command(
    adapter: LaunchdUserAdapter | SystemdUserAdapter,
    stage: str,
    expected: str,
) -> None:
    assert adapter.uninstall_recovery(stage) == expected


@pytest.mark.parametrize("ownership", [ManagerOwnershipState.FOREIGN, ManagerOwnershipState.UNKNOWN])
def test_install_rejects_unverified_loaded_service_without_mutation(
    tmp_path: Path,
    ownership: ManagerOwnershipState,
) -> None:
    adapter = FakeAdapter(tmp_path)
    adapter.manager_registration_override = ManagerRegistration(ownership, detail="unverified loaded service")

    with pytest.raises(ServiceError, match="unverified loaded service"):
        ServiceController(adapter, probe=_manager_probe(adapter), sleep=lambda _: None).install()

    assert adapter.events == []
    assert adapter.definition is None


def test_uninstall_rejects_foreign_loaded_service_without_mutation(tmp_path: Path) -> None:
    adapter = FakeAdapter(tmp_path)
    definition = _definition(tmp_path)
    adapter.definition = definition
    adapter.content = adapter.render(definition)
    adapter.manager_registration_override = ManagerRegistration(
        ManagerOwnershipState.FOREIGN,
        detail="foreign loaded service",
    )

    with pytest.raises(ServiceError, match="foreign loaded service"):
        ServiceController(adapter, probe=_manager_probe(adapter), sleep=lambda _: None).uninstall()

    assert adapter.events == []
    assert adapter.definition == definition


def test_service_install_requires_persistent_config_for_shell_server_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP_PORT", "8123")
    adapter = FakeAdapter(tmp_path)

    with pytest.raises(ServiceError, match="do not copy shell environment variables") as raised:
        ServiceController(adapter).install()

    assert raised.value.exit_code == 2
    assert adapter.events == []


def test_service_install_accepts_a_private_environment_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = tmp_path / "powercontext.env"
    environment.write_text("POWERCONTEXT_SERVER_HTTP_PORT=8123\n", encoding="utf-8")
    environment.chmod(0o600)
    adapter = FakeAdapter(tmp_path)
    controller = ServiceController(adapter, probe=_manager_probe(adapter), sleep=lambda _: None)

    status = controller.install(env_file=environment)

    assert status.ok
    assert adapter.definition is not None
    assert adapter.definition.endpoint == "http://127.0.0.1:8123"
    assert adapter.definition.env_file is not None
    assert adapter.definition.env_file.path == str(environment)


def test_service_install_does_not_inherit_an_unrecorded_shell_data_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = tmp_path / "powercontext.env"
    environment.write_text("POWERCONTEXT_SERVER_HTTP_PORT=8123\n", encoding="utf-8")
    environment.chmod(0o600)
    ambient_data = tmp_path / "ambient-data"
    monkeypatch.setenv(POWERCONTEXT_HOME_ENV, str(ambient_data))
    adapter = FakeAdapter(tmp_path)

    ServiceController(adapter, probe=_manager_probe(adapter), sleep=lambda _: None).install(env_file=environment)

    assert adapter.definition is not None
    assert Path(adapter.definition.data_dir).is_absolute()
    assert Path(adapter.definition.data_dir) != ambient_data


def test_service_install_rejects_a_group_readable_environment_file(tmp_path: Path) -> None:
    environment = tmp_path / "powercontext.env"
    environment.write_text("POWERCONTEXT_SERVER_HTTP_PORT=8123\n", encoding="utf-8")
    environment.chmod(0o640)

    with pytest.raises(ServiceError, match="chmod 600"):
        ServiceController(FakeAdapter(tmp_path)).install(env_file=environment)


def test_systemd_definition_round_trips_and_detects_tampering(tmp_path: Path) -> None:
    adapter = SystemdUserAdapter(config_home=tmp_path)
    executable = str(tmp_path / "Power Context" / "bin" / "python")
    definition = _definition(tmp_path, python_executable=executable)
    rendered = adapter.render(definition)

    adapter.write(rendered)
    installed = adapter.inspect()

    assert installed.state is RegistrationState.INSTALLED
    assert installed.definition == definition
    assert f'"{executable}"'.encode() in rendered

    adapter.artifact_path.write_bytes(rendered + b"# changed\n")
    assert adapter.inspect().state is RegistrationState.INVALID


def test_systemd_inspect_accepts_only_an_intact_legacy_owned_definition(tmp_path: Path) -> None:
    adapter = SystemdUserAdapter(config_home=tmp_path)
    definition = _definition(tmp_path, definition_version=1)
    legacy = adapter.render(definition).replace(
        (
            "Environment=POWERCONTEXT_SERVICE_OWNED=true\n"
            f"Environment=POWERCONTEXT_SERVICE_METADATA={encode_metadata(definition)}\n"
        ).encode(),
        b"",
    )
    adapter.artifact_path.parent.mkdir(parents=True)
    adapter.artifact_path.write_bytes(legacy)

    registration = adapter.inspect()

    assert registration.state is RegistrationState.INSTALLED
    assert registration.definition == definition
    assert (
        definition_state(
            definition,
            package_version=definition.package_version,
            python_executable=definition.python_executable,
        )
        is DefinitionState.STALE
    )

    adapter.artifact_path.write_bytes(legacy.replace(b"RestartSec=5s", b"RestartSec=1s"))
    assert adapter.inspect().state is RegistrationState.INVALID


@pytest.mark.parametrize("configured", ["", "relative/config"])
def test_systemd_ignores_non_absolute_xdg_config_home(
    configured: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", configured)

    adapter = SystemdUserAdapter()

    assert adapter.artifact_path == Path.home() / ".config" / "systemd" / "user" / "powercontext.service"


def test_launchd_definition_round_trips_with_argument_array_and_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = LaunchdUserAdapter(home=tmp_path, uid=501)
    executable = str(tmp_path / "Power Context" / "bin" / "python")
    definition = _definition(tmp_path, python_executable=executable)
    rendered = adapter.render(definition)

    adapter.write(rendered)
    installed = adapter.inspect()
    payload = plistlib.loads(rendered)

    assert installed.state is RegistrationState.INSTALLED
    assert installed.definition == definition
    assert payload["ProgramArguments"][0] == executable
    assert payload["StandardOutPath"].endswith("logs/server.stdout.log")
    retry_token = Path(definition.data_dir) / "logs" / "launchd-retry.enabled"
    assert payload["KeepAlive"] == {"PathState": {str(retry_token): True}}
    assert payload["ProgramArguments"][1:3] == ["-m", "powercontext_service_bootstrap"]
    assert payload["ProgramArguments"][3:11] == [
        "--retry-state",
        str(Path(definition.data_dir) / "logs" / "launchd-retry-state.json"),
        "--retry-token",
        str(retry_token),
        "--retry-limit",
        "3",
        "--retry-window-seconds",
        "60",
    ]
    assert (Path(definition.data_dir) / "logs").is_dir()

    failure_state = Path(definition.data_dir) / "logs" / "launchd-retry-state.json"
    failure_state.write_text("[]", encoding="utf-8")
    run = Mock(
        side_effect=[
            subprocess.CompletedProcess(["launchctl"], 113, "", "Could not find service"),
            subprocess.CompletedProcess(["launchctl"], 0, "", ""),
        ]
    )
    monkeypatch.setattr(adapter, "_run", run)
    adapter.enable()
    assert not failure_state.exists()
    assert retry_token.read_text(encoding="utf-8") == "enabled\n"
    assert run.call_args_list == [
        (("print", "gui/501/com.oceanbase.powercontext"), {"check": False}),
        (("enable", "gui/501/com.oceanbase.powercontext"), {}),
    ]


def test_launchd_inspect_accepts_only_an_intact_legacy_owned_definition(tmp_path: Path) -> None:
    adapter = LaunchdUserAdapter(home=tmp_path, uid=501)
    definition = _definition(tmp_path, definition_version=1)
    payload = plistlib.loads(adapter.render(definition))
    failure_state = Path(definition.data_dir) / "logs" / "launchd-retry-state.json"
    payload["ProgramArguments"] = [
        *definition.launcher_arguments(include_env_identity=False),
        "--failure-state",
        str(failure_state),
        "--failure-limit",
        "3",
        "--failure-window-seconds",
        "60",
    ]
    payload["KeepAlive"] = {"SuccessfulExit": False}
    adapter.artifact_path.parent.mkdir(parents=True)
    adapter.artifact_path.write_bytes(plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True))

    registration = adapter.inspect()

    assert registration.state is RegistrationState.INSTALLED
    assert registration.definition == definition
    assert (
        definition_state(
            definition,
            package_version=definition.package_version,
            python_executable=definition.python_executable,
        )
        is DefinitionState.STALE
    )

    payload["ThrottleInterval"] = 1
    adapter.artifact_path.write_bytes(plistlib.dumps(payload, fmt=plistlib.FMT_XML, sort_keys=True))
    assert adapter.inspect().state is RegistrationState.INVALID


@pytest.mark.parametrize(
    ("output", "returncode", "expected"),
    [
        ("state = running\n", 0, ManagerState.ACTIVE),
        ("state = exited\nlast exit code = 0\n", 0, ManagerState.INACTIVE),
        ("state = exited\nlast exit code = 1\n", 0, ManagerState.FAILED),
        ("state = exited\nlast terminating signal = Terminated: 15\n", 0, ManagerState.FAILED),
        ("state = exited\n", 0, ManagerState.UNKNOWN),
        ("state = not running\nlast exit code = 0\n", 0, ManagerState.INACTIVE),
        ("state = not running\nlast exit code = 1\n", 0, ManagerState.FAILED),
        ("state = not running\n", 0, ManagerState.INACTIVE),
        ("", 113, ManagerState.INACTIVE),
        ("unexpected output\n", 0, ManagerState.UNKNOWN),
    ],
)
def test_launchd_manager_state_uses_exit_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output: str,
    returncode: int,
    expected: ManagerState,
) -> None:
    adapter = LaunchdUserAdapter(home=tmp_path, uid=501)
    result = subprocess.CompletedProcess(["launchctl"], returncode, stdout=output, stderr="Could not find service")
    monkeypatch.setattr(adapter, "_run", Mock(return_value=result))

    assert adapter.manager_state() is expected


@pytest.mark.parametrize("corruption", ["path", "program", "arguments", "marker", "metadata"])
def test_launchd_loaded_registration_requires_matching_path_arguments_and_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    adapter = LaunchdUserAdapter(home=tmp_path, uid=501)
    definition = _definition(tmp_path)
    arguments = plistlib.loads(adapter.render(definition))["ProgramArguments"]

    def output(path: Path, values: list[str]) -> str:
        rendered_arguments = "\n".join(f"    {value}" for value in values)
        return (
            f"path = {path}\n"
            f"program = {values[0]}\n"
            "arguments = {\n"
            f"{rendered_arguments}\n"
            "}\n"
            "environment = {\n"
            "    POWERCONTEXT_SERVICE_OWNED => true\n"
            f"    POWERCONTEXT_SERVICE_METADATA => {encode_metadata(definition)}\n"
            "}\n"
            "state = running\n"
        )

    monkeypatch.setattr(
        adapter,
        "_run",
        Mock(return_value=subprocess.CompletedProcess(["launchctl"], 0, output(adapter.artifact_path, arguments), "")),
    )
    assert adapter.loaded_registration().state is ManagerOwnershipState.OWNED

    foreign = output(adapter.artifact_path, arguments)
    replacements = {
        "path": (str(adapter.artifact_path), str(tmp_path / "foreign.plist")),
        "program": (f"program = {arguments[0]}", f"program = {arguments[0]}.foreign"),
        "arguments": (definition.endpoint, "http://127.0.0.1:9000"),
        "marker": ("POWERCONTEXT_SERVICE_OWNED", "FOREIGN_SERVICE_OWNED"),
        "metadata": (encode_metadata(definition), "invalid-metadata"),
    }
    old, new = replacements[corruption]
    foreign = foreign.replace(old, new, 1)
    monkeypatch.setattr(
        adapter,
        "_run",
        Mock(return_value=subprocess.CompletedProcess(["launchctl"], 0, foreign, "")),
    )
    assert adapter.loaded_registration().state is ManagerOwnershipState.FOREIGN


def test_launchd_loaded_registration_recognizes_an_intact_legacy_definition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = LaunchdUserAdapter(home=tmp_path, uid=501)
    definition = _definition(tmp_path, definition_version=1)
    failure_state = Path(definition.data_dir) / "logs" / "launchd-retry-state.json"
    arguments = [
        *definition.launcher_arguments(include_env_identity=False),
        "--failure-state",
        str(failure_state),
        "--failure-limit",
        "3",
        "--failure-window-seconds",
        "60",
    ]
    rendered_arguments = "\n".join(f"    {value}" for value in arguments)
    output = (
        f"path = {adapter.artifact_path}\n"
        f"program = {arguments[0]}\n"
        "arguments = {\n"
        f"{rendered_arguments}\n"
        "}\n"
        "environment = {\n"
        "    POWERCONTEXT_SERVICE_OWNED => true\n"
        f"    POWERCONTEXT_SERVICE_METADATA => {encode_metadata(definition)}\n"
        "}\n"
        "state = not running\n"
    )
    monkeypatch.setattr(
        adapter,
        "_run",
        Mock(return_value=subprocess.CompletedProcess(["launchctl"], 0, output, "")),
    )

    registration = adapter.loaded_registration()

    assert registration.state is ManagerOwnershipState.OWNED
    assert registration.definition == definition


def test_launchd_stop_waits_until_bootout_removes_the_loaded_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = LaunchdUserAdapter(home=tmp_path, uid=501)
    definition = _definition(tmp_path)
    states = iter((
        ManagerRegistration(ManagerOwnershipState.OWNED, definition=definition),
        ManagerRegistration(ManagerOwnershipState.OWNED, definition=definition),
        ManagerRegistration(ManagerOwnershipState.NOT_LOADED),
    ))
    monkeypatch.setattr(adapter, "loaded_registration", lambda: next(states))
    run = Mock()
    monkeypatch.setattr(adapter, "_run", run)
    monkeypatch.setattr("powercontext.service.adapters.launchd.time.sleep", lambda _delay: None)

    adapter.stop()

    run.assert_called_once_with("bootout", "gui/501/com.oceanbase.powercontext")


@pytest.mark.parametrize("corruption", ["fragment", "path", "arguments", "marker", "metadata"])
def test_systemd_loaded_registration_requires_matching_fragment_command_and_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: str,
) -> None:
    adapter = SystemdUserAdapter(config_home=tmp_path)
    definition = _definition(tmp_path)

    def output(endpoint: str) -> str:
        arguments = definition.launcher_arguments()
        arguments[arguments.index("--endpoint") + 1] = endpoint
        return (
            "LoadState=loaded\n"
            f"FragmentPath={adapter.artifact_path}\n"
            f"ExecStart={{ path={arguments[0]} ; argv[]={' '.join(arguments)} ; ignore_errors=no ; }}\n"
            "Environment=POWERCONTEXT_SERVICE_OWNED=true "
            f"POWERCONTEXT_SERVICE_METADATA={encode_metadata(definition)}\n"
        )

    monkeypatch.setattr(
        adapter,
        "_run",
        Mock(return_value=subprocess.CompletedProcess(["systemctl"], 0, output(definition.endpoint), "")),
    )
    assert adapter.loaded_registration().state is ManagerOwnershipState.OWNED

    foreign = output(definition.endpoint)
    replacements = {
        "fragment": (str(adapter.artifact_path), str(tmp_path / "foreign.service")),
        "path": (f"path={definition.python_executable}", f"path={definition.python_executable}.foreign"),
        "arguments": (definition.endpoint, "http://127.0.0.1:9000"),
        "marker": ("POWERCONTEXT_SERVICE_OWNED", "FOREIGN_SERVICE_OWNED"),
        "metadata": (encode_metadata(definition), "invalid-metadata"),
    }
    old, new = replacements[corruption]
    foreign = foreign.replace(old, new, 1)
    monkeypatch.setattr(
        adapter,
        "_run",
        Mock(return_value=subprocess.CompletedProcess(["systemctl"], 0, foreign, "")),
    )
    assert adapter.loaded_registration().state is ManagerOwnershipState.FOREIGN


def test_systemd_loaded_registration_recognizes_an_intact_legacy_definition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = SystemdUserAdapter(config_home=tmp_path)
    definition = _definition(tmp_path, definition_version=1)
    metadata = encode_metadata(definition)
    legacy = adapter.render(definition).replace(
        (
            f"Environment=POWERCONTEXT_SERVICE_OWNED=true\nEnvironment=POWERCONTEXT_SERVICE_METADATA={metadata}\n"
        ).encode(),
        b"",
    )
    adapter.artifact_path.parent.mkdir(parents=True)
    adapter.artifact_path.write_bytes(legacy)
    arguments = definition.launcher_arguments(include_env_identity=False)
    output = (
        "LoadState=loaded\n"
        f"FragmentPath={adapter.artifact_path}\n"
        f"ExecStart={{ path={arguments[0]} ; argv[]={' '.join(arguments)} ; ignore_errors=no ; }}\n"
        "Environment=\n"
    )
    monkeypatch.setattr(
        adapter,
        "_run",
        Mock(return_value=subprocess.CompletedProcess(["systemctl"], 0, output, "")),
    )

    registration = adapter.loaded_registration()

    assert registration.state is ManagerOwnershipState.OWNED
    assert registration.definition == definition


@pytest.mark.parametrize(
    "adapter_type",
    [pytest.param("launchd", id="launchd"), pytest.param("systemd", id="systemd")],
)
def test_manager_query_failure_reports_unknown_ownership(
    tmp_path: Path,
    adapter_type: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = (
        LaunchdUserAdapter(home=tmp_path, uid=501)
        if adapter_type == "launchd"
        else SystemdUserAdapter(config_home=tmp_path)
    )
    result = subprocess.CompletedProcess(["manager"], 1, stdout="", stderr="permission denied")
    monkeypatch.setattr(adapter, "_run", Mock(return_value=result))

    registration = adapter.loaded_registration()

    assert registration.state is ManagerOwnershipState.UNKNOWN
    assert registration.detail is not None
    assert "permission denied" in registration.detail


@pytest.mark.parametrize("state", ["activating", "deactivating"])
def test_systemd_transitional_manager_state_remains_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    adapter = SystemdUserAdapter(config_home=tmp_path)
    result = subprocess.CompletedProcess(["systemctl"], 0, stdout=state, stderr="")
    monkeypatch.setattr(adapter, "_run", Mock(return_value=result))

    assert adapter.manager_state() is ManagerState.UNKNOWN


def test_systemd_stop_does_not_skip_a_transitional_loaded_unit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = SystemdUserAdapter(config_home=tmp_path)
    definition = _definition(tmp_path)
    monkeypatch.setattr(
        adapter,
        "loaded_registration",
        lambda: ManagerRegistration(ManagerOwnershipState.OWNED, definition=definition),
    )
    monkeypatch.setattr(adapter, "manager_state", lambda: ManagerState.UNKNOWN)
    run = Mock()
    monkeypatch.setattr(adapter, "_run", run)

    adapter.stop()

    run.assert_called_once_with("stop", "powercontext.service")


def test_systemd_definition_reload_restarts_a_transitional_loaded_unit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = SystemdUserAdapter(config_home=tmp_path)
    definition = _definition(tmp_path)
    monkeypatch.setattr(
        adapter,
        "loaded_registration",
        lambda: ManagerRegistration(ManagerOwnershipState.OWNED, definition=definition),
    )
    run = Mock()
    monkeypatch.setattr(adapter, "_run", run)

    adapter.start(reload_definition=True)

    run.assert_called_once_with("restart", "powercontext.service")


class _LivenessHandler(BaseHTTPRequestHandler):
    include_request_id = True

    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        if self.include_request_id:
            self.send_header("X-PowerContext-Request-ID", "0123456789abcdef")
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')

    def log_message(self, format: str, *_args: object) -> None:  # noqa: A002
        return None


def _serve(handler: type[BaseHTTPRequestHandler]) -> tuple[ThreadingHTTPServer, threading.Thread, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = cast(tuple[str, int], server.server_address)
    return server, thread, f"http://{host}:{port}"


def test_service_probe_recognizes_the_powercontext_liveness_contract() -> None:
    server, thread, endpoint = _serve(_LivenessHandler)
    try:
        assert probe_server(endpoint).state is ProbeState.LIVE
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_service_probe_bypasses_process_http_proxy_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    server, thread, endpoint = _serve(_LivenessHandler)
    try:
        assert service_probe.probe_server(endpoint).state is ProbeState.LIVE
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_service_probe_treats_an_invalid_listener_as_a_conflict() -> None:
    class InvalidHandler(_LivenessHandler):
        include_request_id = False

    server, thread, endpoint = _serve(InvalidHandler)
    try:
        assert probe_server(endpoint).state is ProbeState.CONFLICT
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_service_probe_reports_an_absent_listener_as_unreachable() -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    _, port = listener.getsockname()
    listener.close()

    assert probe_server(f"http://127.0.0.1:{port}").state is ProbeState.UNREACHABLE


def test_service_status_json_preserves_the_stable_state_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    status = ServiceStatus(
        support=SupportState.SUPPORTED,
        registration=RegistrationState.INSTALLED,
        definition=DefinitionState.CURRENT,
        manager=ManagerState.ACTIVE,
        server_liveness=LivenessState.LIVE,
        endpoint="http://127.0.0.1:8000",
        log_location="fake logs",
        manager_ownership=ManagerOwnershipState.OWNED,
    )
    controller = Mock()
    controller.status.return_value = status
    monkeypatch.setattr("powercontext.service.cli._controller", lambda: controller)

    result = CliRunner().invoke(service_app, ["status", "--json"])

    assert result.exit_code == 0
    assert json.loads(result.output) == status.as_json()


def test_service_install_cli_renders_post_commit_failure_status(monkeypatch: pytest.MonkeyPatch) -> None:
    status = ServiceStatus(
        support=SupportState.SUPPORTED,
        registration=RegistrationState.INSTALLED,
        definition=DefinitionState.CURRENT,
        manager=ManagerState.FAILED,
        server_liveness=LivenessState.UNREACHABLE,
        endpoint="http://127.0.0.1:8000",
        log_location="fake logs",
        recovery_action="inspect logs",
        detail="cannot reach endpoint",
    )
    controller = Mock()
    controller.install.side_effect = ServiceError("start failed", status=status)
    monkeypatch.setattr("powercontext.service.cli._controller", lambda: controller)

    result = CliRunner().invoke(service_app, ["install"])

    assert result.exit_code == 1
    assert "installation failed: start failed" in result.output
    assert "registration: installed" in result.output
    assert "manager: failed" in result.output
    assert "server liveness: unreachable (http://127.0.0.1:8000)" in result.output
    assert "logs: fake logs" in result.output


def test_service_uninstall_cli_renders_partial_failure_status(monkeypatch: pytest.MonkeyPatch) -> None:
    status = ServiceStatus(
        support=SupportState.SUPPORTED,
        registration=RegistrationState.INSTALLED,
        definition=DefinitionState.CURRENT,
        manager=ManagerState.INACTIVE,
        server_liveness=LivenessState.UNREACHABLE,
        endpoint="http://127.0.0.1:8000",
        log_location="fake logs",
        recovery_action="remaining artifact: /tmp/powercontext.service; run `fake recovery`",
        manager_ownership=ManagerOwnershipState.OWNED,
    )
    controller = Mock()
    controller.uninstall.side_effect = ServiceError("disable failed", status=status)
    monkeypatch.setattr("powercontext.service.cli._controller", lambda: controller)

    result = CliRunner().invoke(service_app, ["uninstall"])

    assert result.exit_code == 1
    assert "uninstall failed: disable failed" in result.output
    assert "registration: installed" in result.output
    assert "remaining artifact" in result.output
    assert "fake recovery" in result.output


def test_service_launcher_hands_control_to_the_foreground_server_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_server = Mock()
    monkeypatch.setattr(
        service_launcher,
        "probe_server",
        lambda _endpoint: ProbeResult(ProbeState.UNREACHABLE, "not listening"),
    )
    monkeypatch.setattr(service_launcher.server_cli, "_run_configured_server", run_server)

    data_dir = tmp_path / "data"
    exit_code = service_launcher.main(["--endpoint", "http://127.0.0.1:8000", "--data-dir", str(data_dir)])

    assert exit_code == 0
    run_server.assert_called_once()
    assert run_server.call_args.args[0].http.host == "127.0.0.1"
    assert run_server.call_args.args[0].http.port == 8000


def test_service_launcher_pins_the_recorded_data_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = tmp_path / "powercontext.env"
    environment.write_text(f"{POWERCONTEXT_HOME_ENV}={tmp_path / 'other-data'}\n", encoding="utf-8")
    environment.chmod(0o600)
    recorded_data = tmp_path / "recorded-data"
    observed_data: list[Path] = []
    monkeypatch.setattr(
        service_launcher,
        "probe_server",
        lambda _endpoint: ProbeResult(ProbeState.UNREACHABLE, "not listening"),
    )
    monkeypatch.setattr(
        service_launcher.server_cli,
        "_run_configured_server",
        lambda _settings: observed_data.append(powercontext_data_dir()),
    )

    definition = _definition(
        tmp_path,
        data_dir=str(recorded_data),
        env_file=EnvironmentFileIdentity.from_path(environment),
    )
    exit_code = service_launcher.main(definition.launcher_arguments()[3:])

    assert exit_code == 0
    assert observed_data == [recorded_data]


def test_service_launcher_does_not_start_over_an_existing_powercontext_server(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_server = Mock()
    monkeypatch.setattr(
        service_launcher,
        "probe_server",
        lambda endpoint: ProbeResult(ProbeState.LIVE, f"{endpoint} status=ok"),
    )
    monkeypatch.setattr(service_launcher.server_cli, "_run_configured_server", run_server)

    exit_code = service_launcher.main(["--endpoint", "http://127.0.0.1:8000", "--data-dir", str(tmp_path / "data")])

    assert exit_code == 0
    run_server.assert_not_called()


def test_launchd_launcher_stops_after_a_bounded_number_of_rapid_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service_launcher, "main", lambda _arguments: 1)
    failure_state = tmp_path / "logs" / "launchd-retry-state.json"
    retry_token = tmp_path / "logs" / "launchd-retry.enabled"
    retry_token.parent.mkdir(parents=True)
    retry_token.write_text("enabled\n", encoding="utf-8")
    arguments = [
        "--retry-state",
        str(failure_state),
        "--retry-token",
        str(retry_token),
        "--retry-limit",
        "3",
        "--retry-window-seconds",
        "60",
        "--",
    ]

    assert [service_bootstrap.main(arguments) for _ in range(4)] == [1, 1, 0, 0]
    assert not retry_token.exists()
