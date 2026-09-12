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
import time
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Callable
from contextlib import suppress
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pytest

from powercontext.service.adapters.base import NativeServiceAdapter, service_python_executable
from powercontext.service.adapters.launchd import LaunchdUserAdapter
from powercontext.service.adapters.systemd import SystemdUserAdapter
from powercontext.service.adapters.windows import WindowsTaskSchedulerAdapter
from powercontext.service.controller import ServiceController
from powercontext.service.model import (
    DEFINITION_VERSION,
    OWNERSHIP_MARKER,
    ManagerOwnershipState,
    ManagerState,
    RegistrationState,
    ServiceDefinition,
    ServiceError,
    ServiceStatus,
    SupportState,
)

pytestmark = [
    pytest.mark.native_service,
    pytest.mark.skipif(
        os.environ.get("POWERCONTEXT_RUN_NATIVE_SERVICE_TESTS") != "1",
        reason="set POWERCONTEXT_RUN_NATIVE_SERVICE_TESTS=1 on a disposable matching-platform runner",
    ),
]


def test_native_personal_service_lifecycle(tmp_path: Path) -> None:
    adapter = _native_adapter()
    support, detail = adapter.support()
    assert support is SupportState.SUPPORTED, detail
    environment = _environment_file(tmp_path)
    controller = ServiceController(adapter)

    try:
        try:
            installed = controller.install(env_file=environment)
        except ServiceError as error:
            pytest.fail(f"{error}\n\n{_failure_diagnostics(adapter, tmp_path)}")

        assert installed.ok, f"{installed}\n\n{_failure_diagnostics(adapter, tmp_path)}"
        assert installed.manager_ownership is ManagerOwnershipState.OWNED
        loaded = adapter.loaded_registration()
        assert loaded.state is ManagerOwnershipState.OWNED
        assert loaded.definition is not None
        assert loaded.definition.python_executable == service_python_executable()

        adapter.stop()
        if isinstance(adapter, LaunchdUserAdapter):
            assert adapter.loaded_registration().state is ManagerOwnershipState.NOT_LOADED
        else:
            assert adapter.loaded_registration().state is ManagerOwnershipState.OWNED
            assert adapter.manager_state() is ManagerState.INACTIVE

        adapter.start(reload_definition=False)
        restarted = _wait_for_status(controller)
        assert restarted.ok, f"{restarted}\n\n{_failure_diagnostics(adapter, tmp_path)}"

        removed = controller.uninstall()

        assert removed.registration is RegistrationState.NOT_INSTALLED
        assert adapter.loaded_registration().state is ManagerOwnershipState.NOT_LOADED
        assert not adapter.artifact_path.exists()
    finally:
        _cleanup(adapter)


def test_failure_diagnostics_reports_an_absent_service(tmp_path: Path) -> None:
    """The report must survive the state it exists to describe: nothing there.

    It runs on the failure path, so every probe is read-only and independent.
    If one of them raises, the report replaces the failure it was meant to
    explain and the run says nothing about either (#1571).
    """
    adapter = _native_adapter(suffix="diagnostics")
    environment = _environment_file(tmp_path)
    port = _service_port(tmp_path)
    assert port is not None, f"the environment file records no port: {environment.read_text()}"

    report = _failure_diagnostics(adapter, tmp_path)

    assert adapter.identifier in report
    assert "loaded_registration:" in report
    assert "manager_state:" in report
    assert "inspect:" in report
    assert f"artifact {adapter.artifact_path}: exists=False" in report
    assert f"port {port}: reachable=False" in report
    # The logs never existed, and saying so is the point: the issue's failure
    # reported an empty stderr tail with no way to tell absent from silent.
    assert "server.stdout.log: not created" in report
    assert "server.stderr.log: not created" in report
    # The platform's own service manager was asked, and its answer is quoted.
    expected_manager = {
        LaunchdUserAdapter: "launchctl",
        SystemdUserAdapter: "systemctl",
        WindowsTaskSchedulerAdapter: "schtasks",
    }[type(adapter)]
    assert expected_manager in report


def test_native_service_definition_matches_running_process(tmp_path: Path) -> None:
    adapter = _native_adapter()
    controller = ServiceController(adapter)

    try:
        installed = controller.install(env_file=_environment_file(tmp_path))
        registration = adapter.inspect()
        loaded = adapter.loaded_registration()

        assert installed.ok
        assert registration.definition is not None
        assert loaded.state is ManagerOwnershipState.OWNED
        assert loaded.definition == registration.definition
        assert registration.definition.python_executable == service_python_executable()
        content = adapter.artifact_path.read_bytes()
        if isinstance(adapter, LaunchdUserAdapter):
            payload = plistlib.loads(content)
            assert payload["ProgramArguments"][0] == os.path.abspath(sys.executable)
            assert payload["ProgramArguments"][1:3] == ["-m", "powercontext_service_bootstrap"]
            assert payload["RunAtLoad"] is True
            assert "PathState" in payload["KeepAlive"]
            assert payload["StandardOutPath"].endswith("logs/server.stdout.log")
            assert payload["StandardErrorPath"].endswith("logs/server.stderr.log")
        elif isinstance(adapter, WindowsTaskSchedulerAdapter):
            namespace = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"
            payload = ET.fromstring(content)  # noqa: S314
            logon_trigger = payload.find(f"{namespace}Triggers/{namespace}LogonTrigger")
            logon_type = payload.find(f"{namespace}Principals/{namespace}Principal/{namespace}LogonType")
            run_level = payload.find(f"{namespace}Principals/{namespace}Principal/{namespace}RunLevel")
            hidden = payload.find(f"{namespace}Settings/{namespace}Hidden")
            restart_count = payload.find(f"{namespace}Settings/{namespace}RestartOnFailure/{namespace}Count")
            command = payload.find(f"{namespace}Actions/{namespace}Exec/{namespace}Command")
            assert logon_trigger is not None
            assert logon_type is not None and logon_type.text == "InteractiveToken"
            assert run_level is not None and run_level.text == "LeastPrivilege"
            assert hidden is not None and hidden.text == "true"
            assert restart_count is not None and restart_count.text == "3"
            assert command is not None and Path(command.text or "").name.casefold() == "pythonw.exe"
            log_location = adapter.log_location(registration.definition)
            assert log_location is not None and log_location.endswith("logs")
        else:
            unit = content.decode()
            assert f'ExecStart="{os.path.abspath(sys.executable)}"' in unit
            assert "Restart=on-failure" in unit
            assert "StartLimitIntervalSec=60" in unit
            assert "StartLimitBurst=3" in unit
            assert f"journalctl --user --unit {adapter.identifier}" == adapter.log_location(registration.definition)
    finally:
        with suppress(Exception):
            controller.uninstall()
        _cleanup(adapter)


@pytest.mark.skipif(sys.platform != "win32", reason="Task Scheduler login-trigger behavior is Windows-specific")
def test_native_windows_service_can_disable_login_trigger(tmp_path: Path) -> None:
    adapter = _native_adapter(suffix="manual")
    controller = ServiceController(adapter)

    try:
        installed = controller.install(env_file=_environment_file(tmp_path), start_on_login=False)

        assert installed.ok
        assert installed.manager is ManagerState.ACTIVE
        loaded = adapter.loaded_registration()
        assert loaded.state is ManagerOwnershipState.OWNED
        content = adapter.artifact_path.read_bytes()
        namespace = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"
        payload = ET.fromstring(content)  # noqa: S314
        assert payload.find(f"{namespace}Triggers/{namespace}LogonTrigger") is None
        assert adapter.manager_state() is ManagerState.ACTIVE
    finally:
        with suppress(Exception):
            controller.uninstall()
        _cleanup(adapter)


def test_native_service_rejects_foreign_registration(tmp_path: Path) -> None:
    adapter = _native_adapter()
    environment = _environment_file(tmp_path)
    _load_foreign_registration(adapter, tmp_path)

    try:
        assert adapter.loaded_registration().state is ManagerOwnershipState.FOREIGN

        with pytest.raises(ServiceError, match=r"ownership|PowerContext"):
            ServiceController(adapter).install(env_file=environment)

        assert not adapter.artifact_path.exists()
        assert adapter.loaded_registration().state is ManagerOwnershipState.FOREIGN
    finally:
        _remove_foreign_registration(adapter)


@pytest.mark.skipif(sys.platform != "darwin", reason="launchd retry classification is macOS-specific")
def test_native_service_retry_and_exit_classification(tmp_path: Path) -> None:
    adapter = _native_adapter(suffix="retry")
    state = tmp_path / "logs" / "launchd-retry-state.json"
    token = tmp_path / "logs" / "launchd-retry.enabled"
    state.parent.mkdir(parents=True)
    token.write_text("enabled\n", encoding="utf-8")
    token.chmod(0o600)
    arguments = [
        os.path.abspath(sys.executable),
        "-m",
        "powercontext_service_bootstrap",
        "--retry-state",
        str(state),
        "--retry-token",
        str(token),
        "--retry-limit",
        "3",
        "--retry-window-seconds",
        "60",
        "--",
        "--endpoint",
        "http://127.0.0.1:1",
        "--data-dir",
        str(tmp_path / "data"),
    ]
    payload: dict[str, Any] = {
        "Label": adapter.identifier,
        "ProgramArguments": arguments,
        "RunAtLoad": True,
        "KeepAlive": {"PathState": {str(token): True}},
        "ThrottleInterval": 1,
        "StandardOutPath": str(tmp_path / "logs" / "stdout.log"),
        "StandardErrorPath": str(tmp_path / "logs" / "stderr.log"),
    }
    adapter.artifact_path.parent.mkdir(parents=True, exist_ok=True)
    adapter.artifact_path.write_bytes(plistlib.dumps(payload))

    try:
        _run("launchctl", "enable", f"gui/{_current_uid()}/{adapter.identifier}")
        _run("launchctl", "bootstrap", f"gui/{_current_uid()}", str(adapter.artifact_path))
        _wait_for(lambda: not token.exists() and _attempt_count(state) == 3, timeout=20)
        _wait_for(lambda: adapter.manager_state() is ManagerState.INACTIVE)

        assert adapter.manager_state() is ManagerState.INACTIVE
        result = _run("launchctl", "print", f"gui/{_current_uid()}/{adapter.identifier}")
        assert "last exit code = 0" in result.stdout
    finally:
        _remove_foreign_registration(adapter)


def _native_adapter(*, suffix: str | None = None) -> NativeServiceAdapter:
    configured = os.environ.get("POWERCONTEXT_NATIVE_SERVICE_IDENTIFIER")
    unique = uuid.uuid4().hex
    if sys.platform == "darwin":
        base_identifier = configured or f"com.oceanbase.powercontext.native.{unique}"
        identifier = base_identifier
        if suffix is not None:
            identifier = f"{base_identifier}.{suffix}"
        assert ".native." in identifier
        return LaunchdUserAdapter(identifier=identifier)
    if sys.platform == "linux":
        base_identifier = configured or f"powercontext-native-{unique}.service"
        identifier = base_identifier
        if suffix is not None:
            identifier = f"powercontext-native-{suffix}-{unique}.service"
        assert identifier.startswith("powercontext-native-")
        return SystemdUserAdapter(identifier=identifier)
    if sys.platform == "win32":
        base_identifier = configured or f"PowerContext-Native-{unique}"
        identifier = base_identifier if suffix is None else f"{base_identifier}-{suffix}"
        assert identifier.startswith("PowerContext-Native-")
        return WindowsTaskSchedulerAdapter(identifier=identifier)
    pytest.skip(f"no native personal-service adapter for {sys.platform}")


def _environment_file(tmp_path: Path) -> Path:
    environment = tmp_path / "powercontext.env"
    data_dir = str(tmp_path / "data")
    if os.name == "nt":
        data_dir = f'"{data_dir}"'
    environment.write_text(
        "\n".join((
            f"POWERCONTEXT_HOME={data_dir}",
            f"POWERCONTEXT_SERVER_HTTP_PORT={_unused_loopback_port()}",
            "",
        )),
        encoding="utf-8",
    )
    environment.chmod(0o600)
    if os.name == "nt":
        account = subprocess.run(
            ["whoami.exe"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
        # Hosted Windows runners can create pytest's temporary files with an
        # inherited owner that differs from the account running the test.
        _run("icacls.exe", str(environment), "/setowner", account)
        _run(
            "icacls.exe",
            str(environment),
            "/inheritance:r",
            "/grant:r",
            f"{account}:(F)",
            "SYSTEM:(F)",
            "Administrators:(F)",
        )
    return environment


def _unused_loopback_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _log_tail(tmp_path: Path, name: str, limit: int = 4000) -> str:
    path = tmp_path / "data" / "logs" / name
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return f"{name}: not created"
    if not content.strip():
        return f"{name}: empty ({path.stat().st_size} bytes)"
    return f"{name} tail:\n{content[-limit:]}"


def _command_output(argv: list[str], *, timeout: float = 10) -> str:
    """Run a read-only diagnostic command and render whatever it said."""
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return f"$ {' '.join(argv)}\n(command not available)"
    except subprocess.TimeoutExpired:
        return f"$ {' '.join(argv)}\n(timed out after {timeout}s)"
    except OSError as error:  # pragma: no cover - platform dependent
        return f"$ {' '.join(argv)}\n(failed: {error})"
    body = (completed.stdout + completed.stderr).strip() or "(no output)"
    return f"$ {' '.join(argv)} -> exit {completed.returncode}\n{body[-4000:]}"


def _service_port(tmp_path: Path) -> int | None:
    environment = tmp_path / "powercontext.env"
    try:
        lines = environment.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        key, _, value = line.partition("=")
        if key.strip() == "POWERCONTEXT_SERVER_HTTP_PORT":
            with suppress(ValueError):
                return int(value.strip().strip('"'))
    return None


def _platform_diagnostics(adapter: NativeServiceAdapter) -> list[str]:
    """Ask the platform's own service manager what it thinks the state is."""
    identifier = adapter.identifier
    if isinstance(adapter, LaunchdUserAdapter):
        uid = os.getuid() if hasattr(os, "getuid") else 0
        return [
            _command_output(["launchctl", "print", f"gui/{uid}/{identifier}"]),
            _command_output(["launchctl", "list", identifier]),
        ]
    if isinstance(adapter, SystemdUserAdapter):
        return [
            _command_output(["systemctl", "--user", "status", identifier, "--no-pager"]),
            _command_output(["journalctl", "--user", "--unit", identifier, "--no-pager", "-n", "50"]),
        ]
    if isinstance(adapter, WindowsTaskSchedulerAdapter):
        return [_command_output(["schtasks.exe", "/Query", "/TN", identifier, "/V", "/FO", "LIST"])]
    return ["(no platform diagnostics for this adapter)"]


def _failure_diagnostics(adapter: NativeServiceAdapter, tmp_path: Path) -> str:
    """Everything about a failed start, collected BEFORE the test cleans up.

    The `finally` block stops, disables and removes the registration, so a CI
    step that inspects the service afterwards finds nothing and reports "could
    not be found" whatever the failure was (#1571). Each probe is independent
    and swallows its own errors: a report that raises replaces the failure it
    was meant to explain.

    Every probe is read-only and scoped to this service: its own label, its own
    port, its own entry point. Nothing here dumps a global process table or a
    log this test did not create, because the report lands in a public CI log.
    """
    sections: list[str] = [f"identifier: {adapter.identifier}"]

    for label, probe in (
        ("loaded_registration", adapter.loaded_registration),
        ("manager_state", adapter.manager_state),
        ("inspect", adapter.inspect),
    ):
        try:
            sections.append(f"{label}: {probe()}")
        except Exception as error:
            sections.append(f"{label}: probe failed: {error!r}")

    try:
        artifact = adapter.artifact_path
        sections.append(
            f"artifact {artifact}: exists={artifact.exists()}"
            + (f" size={artifact.stat().st_size}" if artifact.exists() else "")
        )
    except Exception as error:
        sections.append(f"artifact: probe failed: {error!r}")

    port = _service_port(tmp_path)
    if port is None:
        sections.append("port: not recorded in the environment file")
    else:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe_socket:
            probe_socket.settimeout(2)
            reachable = probe_socket.connect_ex(("127.0.0.1", port)) == 0
        sections.append(f"port {port}: reachable={reachable}")
        if sys.platform != "win32":
            sections.append(_command_output(["lsof", "-nP", f"-iTCP:{port}"]))

    # Matched on the service's own entry point rather than dumped whole: a CI
    # log is public, and a full process table carries other people's command
    # lines, tokens and paths with it.
    if sys.platform == "win32":
        sections.append(_command_output(["tasklist.exe", "/FI", "IMAGENAME eq pythonw.exe", "/FO", "LIST"]))
    else:
        sections.append(_command_output(["pgrep", "-fl", "powercontext_service_bootstrap"]))

    sections.extend(_platform_diagnostics(adapter))
    sections.append(_log_tail(tmp_path, "server.stdout.log"))
    sections.append(_log_tail(tmp_path, "server.stderr.log", limit=8000))

    return "\n\n".join(sections)


def _cleanup(adapter: NativeServiceAdapter) -> None:
    with suppress(Exception):
        loaded = adapter.loaded_registration()
        if loaded.state is ManagerOwnershipState.OWNED:
            adapter.stop()
    with suppress(Exception):
        adapter.disable()
    with suppress(Exception):
        adapter.remove()
    with suppress(Exception):
        adapter.reload()
    adapter.lock_path.unlink(missing_ok=True)


def _wait_for_status(controller: ServiceController, *, timeout: float = 30) -> ServiceStatus:
    # Allow the same startup window as installation, including launchd scheduling.
    status = controller.status()
    deadline = time.monotonic() + timeout
    while not status.ok and time.monotonic() < deadline:
        time.sleep(0.1)
        status = controller.status()
    return status


def _wait_for(predicate: Callable[[], bool], *, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert predicate()


def _attempt_count(path: Path) -> int:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return 0
    attempts = payload.get("attempts") if isinstance(payload, dict) else None
    return len(attempts) if isinstance(attempts, list) else 0


def _load_foreign_registration(adapter: NativeServiceAdapter, tmp_path: Path) -> None:
    if isinstance(adapter, LaunchdUserAdapter):
        foreign = tmp_path / f"{adapter.identifier}.plist"
        foreign.write_bytes(
            plistlib.dumps({
                "Label": adapter.identifier,
                "ProgramArguments": ["/bin/sleep", "30"],
                "RunAtLoad": True,
            })
        )
        _run("launchctl", "enable", f"gui/{_current_uid()}/{adapter.identifier}")
        _run("launchctl", "bootstrap", f"gui/{_current_uid()}", str(foreign))
    elif isinstance(adapter, WindowsTaskSchedulerAdapter):
        foreign = tmp_path / "foreign.xml"
        definition = ServiceDefinition(
            ownership=OWNERSHIP_MARKER,
            definition_version=DEFINITION_VERSION,
            package_version=version("powercontext"),
            python_executable=os.path.abspath(sys.executable),
            endpoint="http://127.0.0.1:1",
            data_dir=str(tmp_path / "foreign-data"),
            env_file=None,
        )
        payload = ET.fromstring(adapter.render(definition))  # noqa: S314
        namespace = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"
        description = payload.find(f"{namespace}RegistrationInfo/{namespace}Description")
        assert description is not None
        description.text = "Foreign Task"
        foreign.write_bytes(ET.tostring(payload, encoding="utf-16", xml_declaration=True))
        _run(
            "schtasks.exe",
            "/Create",
            "/TN",
            adapter.identifier,
            "/XML",
            str(foreign),
            "/F",
            "/HRESULT",
        )
    else:
        _run(
            "systemd-run",
            "--user",
            f"--unit={adapter.identifier}",
            "--property=RemainAfterExit=yes",
            "/bin/sleep",
            "30",
        )
    _wait_for(lambda: adapter.loaded_registration().state is ManagerOwnershipState.FOREIGN)


def _remove_foreign_registration(adapter: NativeServiceAdapter) -> None:
    if isinstance(adapter, LaunchdUserAdapter):
        target = f"gui/{_current_uid()}/{adapter.identifier}"
        _run_ignoring_failure("launchctl", "bootout", target)
        _run_ignoring_failure("launchctl", "disable", target)
    elif isinstance(adapter, WindowsTaskSchedulerAdapter):
        _run_ignoring_failure("schtasks.exe", "/End", "/TN", adapter.identifier, "/HRESULT")
        _run_ignoring_failure("schtasks.exe", "/Delete", "/TN", adapter.identifier, "/F", "/HRESULT")
    else:
        _run_ignoring_failure("systemctl", "--user", "stop", adapter.identifier)
        _run_ignoring_failure("systemctl", "--user", "reset-failed", adapter.identifier)
    adapter.artifact_path.unlink(missing_ok=True)
    adapter.lock_path.unlink(missing_ok=True)


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(arguments),
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )


def _run_ignoring_failure(*arguments: str) -> None:
    subprocess.run(
        list(arguments),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _current_uid() -> int:
    getuid = getattr(os, "getuid", None)
    return int(getuid()) if getuid is not None else 0
