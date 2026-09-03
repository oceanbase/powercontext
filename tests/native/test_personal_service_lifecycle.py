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
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest

from powercontext.service.adapters.base import NativeServiceAdapter
from powercontext.service.adapters.launchd import LaunchdUserAdapter
from powercontext.service.adapters.systemd import SystemdUserAdapter
from powercontext.service.controller import ServiceController
from powercontext.service.model import (
    ManagerOwnershipState,
    ManagerState,
    RegistrationState,
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
        installed = controller.install(env_file=environment)

        assert installed.ok
        assert installed.manager_ownership is ManagerOwnershipState.OWNED
        loaded = adapter.loaded_registration()
        assert loaded.state is ManagerOwnershipState.OWNED
        assert loaded.definition is not None
        assert loaded.definition.python_executable == os.path.abspath(sys.executable)

        adapter.stop()
        if isinstance(adapter, LaunchdUserAdapter):
            assert adapter.loaded_registration().state is ManagerOwnershipState.NOT_LOADED
        else:
            assert adapter.loaded_registration().state is ManagerOwnershipState.OWNED
            assert adapter.manager_state() is ManagerState.INACTIVE

        adapter.start(reload_definition=False)
        restarted = _wait_for_status(controller)
        assert restarted.ok

        removed = controller.uninstall()

        assert removed.registration is RegistrationState.NOT_INSTALLED
        assert adapter.loaded_registration().state is ManagerOwnershipState.NOT_LOADED
        assert not adapter.artifact_path.exists()
    finally:
        _cleanup(adapter)


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
        assert registration.definition.python_executable == os.path.abspath(sys.executable)
        content = adapter.artifact_path.read_bytes()
        if isinstance(adapter, LaunchdUserAdapter):
            payload = plistlib.loads(content)
            assert payload["ProgramArguments"][0] == os.path.abspath(sys.executable)
            assert payload["ProgramArguments"][1:3] == ["-m", "powercontext_service_bootstrap"]
            assert payload["RunAtLoad"] is True
            assert "PathState" in payload["KeepAlive"]
            assert payload["StandardOutPath"].endswith("logs/server.stdout.log")
            assert payload["StandardErrorPath"].endswith("logs/server.stderr.log")
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
        _run("launchctl", "enable", f"gui/{os.getuid()}/{adapter.identifier}")
        _run("launchctl", "bootstrap", f"gui/{os.getuid()}", str(adapter.artifact_path))
        _wait_for(lambda: not token.exists() and _attempt_count(state) == 3, timeout=20)
        _wait_for(lambda: adapter.manager_state() is ManagerState.INACTIVE)

        assert adapter.manager_state() is ManagerState.INACTIVE
        result = _run("launchctl", "print", f"gui/{os.getuid()}/{adapter.identifier}")
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
    pytest.skip(f"no native personal-service adapter for {sys.platform}")


def _environment_file(tmp_path: Path) -> Path:
    environment = tmp_path / "powercontext.env"
    environment.write_text(
        "\n".join((
            f"POWERCONTEXT_HOME={tmp_path / 'data'}",
            f"POWERCONTEXT_SERVER_HTTP_PORT={_unused_loopback_port()}",
            "POWERCONTEXT_SERVER_DASHBOARD_ENABLED=false",
            "",
        )),
        encoding="utf-8",
    )
    environment.chmod(0o600)
    return environment


def _unused_loopback_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


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


def _wait_for_status(controller: ServiceController, *, timeout: float = 15) -> ServiceStatus:
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
        _run("launchctl", "enable", f"gui/{os.getuid()}/{adapter.identifier}")
        _run("launchctl", "bootstrap", f"gui/{os.getuid()}", str(foreign))
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
        target = f"gui/{os.getuid()}/{adapter.identifier}"
        _run_ignoring_failure("launchctl", "bootout", target)
        _run_ignoring_failure("launchctl", "disable", target)
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
