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

import errno
import json
import os
import plistlib
import re
import socket
import sqlite3
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from contextlib import ExitStack, closing, contextmanager, suppress
from importlib.metadata import version
from pathlib import Path
from socketserver import BaseRequestHandler, TCPServer
from threading import Thread
from typing import Any

import httpx
import pytest
from alembic import command
from sqlalchemy import create_engine
from typer.testing import CliRunner

from powercontext.builtin.persistence.migrations.deployment import deployment_runner, server_bundle
from powercontext.cli.app import create_cli
from powercontext.server import database_migration
from powercontext.server.cli import app as server_app
from powercontext.server.configuration import server_settings_context
from powercontext.service.adapters.base import NativeServiceAdapter, service_python_executable
from powercontext.service.adapters.launchd import LaunchdUserAdapter
from powercontext.service.adapters.systemd import SystemdUserAdapter
from powercontext.service.adapters.windows import WindowsTaskSchedulerAdapter
from powercontext.service.controller import ServiceController
from powercontext.service.environment import load_protected_environment_file
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
        _capture_native_failure(adapter, tmp_path)
        _cleanup(adapter)


def test_native_maintenance_preserves_registration_and_original_running_state(tmp_path: Path) -> None:
    """The manager must not recreate an old writer while a migration owns maintenance."""

    adapter = _native_adapter(suffix="maintenance")
    controller = ServiceController(adapter)
    environment = _environment_file(tmp_path)
    try:
        assert controller.install(env_file=environment).ok
        original = adapter.inspect().definition
        summary = controller.maintenance_summary(env_file=environment)
        assert summary.originally_running
        with controller.maintenance(expected_fingerprint=summary.fingerprint, env_file=environment) as session:
            assert adapter.inspect().definition == original
            assert controller.maintenance_path.exists()
            # Cross the native RestartSec/ThrottleInterval boundary so a
            # KeepAlive/retry policy cannot silently restart the old process.
            time.sleep(6)
            assert adapter.manager_state() in {ManagerState.INACTIVE, ManagerState.FAILED}
            assert session.complete().ok
        assert not controller.maintenance_path.exists()

        controller.stop()
        stopped = controller.maintenance_summary(env_file=environment)
        assert not stopped.originally_running
        with controller.maintenance(expected_fingerprint=stopped.fingerprint, env_file=environment) as session:
            session.complete()
        assert adapter.inspect().definition == stopped.target_definition
        assert adapter.manager_state() in {ManagerState.INACTIVE, ManagerState.FAILED}
        assert controller.start().ok
    finally:
        _capture_native_failure(adapter, tmp_path)
        with suppress(Exception):
            controller.uninstall()
        _cleanup(adapter)


def test_native_public_migration_upgrades_a_stopped_historical_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise the installed CLI, frozen SQLite baseline and real native manager together."""

    adapter = _native_adapter(suffix="database-upgrade")
    controller = ServiceController(adapter)
    environment = _environment_file(tmp_path)
    database = tmp_path / "data" / "powercontext.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    with server_settings_context(env_file=environment) as settings:
        runner = deployment_runner(settings)
    bundle = server_bundle()
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    try:
        with engine.begin() as connection:
            config = bundle.config(connection)
            config.attributes["configuration"] = runner.configuration
            command.upgrade(config, "s1300")
            connection.exec_driver_sql("DROP TABLE pc_schema_revision")
            connection.exec_driver_sql(
                "INSERT INTO pc_scopes (scope_id,title,summary,scope_id_search,title_search,summary_search,version) "
                "VALUES ('historical','retained title','retained summary','historical','retained title','retained summary',1)"
            )
    finally:
        engine.dispose()
    port = _service_port(tmp_path)
    assert port is not None
    # A stopped registration represents the historical deployment. Its database
    # comes from frozen migration resources, not an installed 1.3.0 executable.
    definition = ServiceDefinition(
        ownership=OWNERSHIP_MARKER,
        definition_version=DEFINITION_VERSION,
        package_version="frozen-s1300-fixture",
        python_executable=service_python_executable(),
        endpoint=f"http://127.0.0.1:{port}",
        data_dir=str(database.parent),
        env_file=load_protected_environment_file(environment).identity,
    )
    try:
        adapter.write(adapter.render(definition))
        adapter.update_suspended()
        controller.stop()
        # Select only this uniquely named native registration; all manager,
        # database, backup and readiness operations remain real.
        monkeypatch.setattr(database_migration, "ServiceController", lambda: controller)
        cli = create_cli([server_app])
        reviewed = _native_migration_command(cli, environment, "plan")
        assert reviewed["state"] == "migration_required"
        assert reviewed["service"]["originally_running"] is False

        result = _native_migration_command(
            cli,
            environment,
            "apply",
            "--backup",
            "auto",
            "--plan-id",
            reviewed["plan_id"],
            "--yes",
            "--maintenance-confirmed",
        )

        assert result["server_ready"] is True
        assert result["backup_state"] == "completed"
        assert result["backup_ref"] and Path(result["backup_ref"]).is_file()
        assert runner.verify().state == "ready"
        assert adapter.manager_state() in {ManagerState.INACTIVE, ManagerState.FAILED}
        with closing(sqlite3.connect(database)) as connection:
            assert connection.execute("SELECT title,summary FROM pc_scopes WHERE scope_id='historical'").fetchone() == (
                "retained title",
                "retained summary",
            )
        assert controller.maintenance_path.exists()
        assert controller.start().ok, _failure_diagnostics(adapter, tmp_path)
        assert not controller.maintenance_path.exists()
    finally:
        _capture_native_failure(adapter, tmp_path)
        with suppress(Exception):
            controller.uninstall()
        _cleanup(adapter)


def test_native_public_migration_of_ready_storage_restores_a_running_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reviewed ready-database retry must still finish native service maintenance."""

    adapter = _native_adapter(suffix="ready-database")
    controller = ServiceController(adapter)
    environment = _environment_file(tmp_path)
    try:
        assert controller.install(env_file=environment).ok
        monkeypatch.setattr(database_migration, "ServiceController", lambda: controller)
        cli = create_cli([server_app])
        reviewed = _native_migration_command(cli, environment, "plan")
        assert reviewed["state"] == "ready"
        assert reviewed["service"]["originally_running"] is True

        result = _native_migration_command(
            cli,
            environment,
            "apply",
            "--backup",
            "auto",
            "--plan-id",
            reviewed["plan_id"],
            "--yes",
            "--maintenance-confirmed",
        )

        assert result["server_ready"] is True and result["changed"] is False
        assert controller.status().ok, _failure_diagnostics(adapter, tmp_path)
        assert adapter.manager_state() is ManagerState.ACTIVE
        assert not controller.maintenance_path.exists()
    finally:
        _capture_native_failure(adapter, tmp_path)
        with suppress(Exception):
            controller.uninstall()
        _cleanup(adapter)


@pytest.mark.parametrize(
    ("originally_running", "failure"),
    [(True, None), (False, None), (True, "backup"), (False, "backup"), (True, "startup"), (True, "switch")],
    ids=["running", "stopped", "backup-retry-running", "backup-retry-stopped", "startup-retry", "switch-retry"],
)
def test_native_public_migration_switches_an_installed_130_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, originally_running: bool, failure: str | None
) -> None:
    """Upgrade storage populated by the released service through its actual native manager."""

    source = Path(os.environ["POWERCONTEXT_NATIVE_UPGRADE_SOURCE_PYTHON"])
    source_version = _run(
        str(source), "-I", "-c", 'from importlib.metadata import version; print(version("powercontext"))'
    ).stdout.strip()
    assert source_version == "1.3.0"
    source_launcher = source.with_name("pythonw.exe") if os.name == "nt" else source
    assert source_launcher.is_file()
    adapter = _native_adapter(suffix=f"installed-upgrade-{'running' if originally_running else 'stopped'}")
    controller = ServiceController(adapter)
    environment = _environment_file(tmp_path)
    with environment.open("a", encoding="utf-8") as output:
        output.write(
            "POWERCONTEXT_SERVER_AUTH_TOKEN=native-upgrade-fixture-token\nPOWERCONTEXT_SERVER_ACCESS_MODE=enforced\n"
        )
    port = _service_port(tmp_path)
    assert port is not None
    definition = ServiceDefinition(
        ownership=OWNERSHIP_MARKER,
        definition_version=DEFINITION_VERSION,
        package_version=source_version,
        python_executable=str(source_launcher.absolute()),
        endpoint=f"http://127.0.0.1:{port}",
        data_dir=str(tmp_path / "data"),
        env_file=load_protected_environment_file(environment).identity,
    )
    try:
        adapter.write(adapter.render(definition))
        adapter.update_suspended()
        # Starting a valid older executable intentionally leaves a stale-version
        # status until migration switches the registration to this environment.
        try:
            controller.start()
        except ServiceError as error:
            pytest.fail(f"{error}\n\n{_failure_diagnostics(adapter, tmp_path)}")
        assert adapter.manager_state() is ManagerState.ACTIVE, _failure_diagnostics(adapter, tmp_path)
        with httpx.Client(
            base_url=definition.endpoint,
            headers={"Authorization": "Bearer native-upgrade-fixture-token"},
            timeout=30,
            trust_env=False,
        ) as client:
            created = client.post(
                "/v1/scopes",
                json={
                    "title": "Installed service upgrade",
                    "summary": "Preserve data across the native executable switch.",
                    "idempotency_key": "native-installed-upgrade",
                },
            )
            assert created.is_success, created.text
            scope_id = created.json()["scope_id"]
            if not originally_running:
                controller.stop()
            # Select this isolated registration; the manager and migration remain real.
            monkeypatch.setattr(database_migration, "ServiceController", lambda: controller)
            cli = create_cli([server_app])
            reviewed = _native_migration_command(cli, environment, "plan")
            assert reviewed["state"] == "migration_required"
            assert reviewed["service"]["originally_running"] is originally_running
            assert reviewed["service"]["definition"]["python_executable"] == definition.python_executable
            assert reviewed["service"]["target_definition"]["python_executable"] == service_python_executable()
            database = tmp_path / "data" / "powercontext.db"
            if failure == "backup":
                blocked_directory = database.with_name(database.name + ".pc-migration-backups")
                blocked_directory.write_text("Backup storage is unavailable.", encoding="utf-8")
                failed = CliRunner().invoke(
                    cli,
                    [
                        "server",
                        "db-migrate",
                        "apply",
                        "--env-file",
                        str(environment),
                        "--manage-service",
                        "--backup",
                        "auto",
                        "--plan-id",
                        reviewed["plan_id"],
                        "--yes",
                        "--maintenance-confirmed",
                    ],
                )
                assert failed.exit_code == 1, failed.output
                assert json.loads(failed.output)["error"] == "backup_failed"
                assert adapter.manager_state() in {ManagerState.INACTIVE, ManagerState.FAILED}
                assert controller.maintenance_path.exists()
                assert adapter.inspect().definition == definition
                with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as unchanged:
                    assert unchanged.execute(
                        "SELECT title FROM pc_scopes WHERE scope_id=?", (scope_id,)
                    ).fetchone() == ("Installed service upgrade",)
                    assert (
                        unchanged.execute("SELECT name FROM sqlite_master WHERE name='pc_schema_revision'").fetchall()
                        == []
                    )
                blocked_directory.unlink()
                reviewed = _native_migration_command(cli, environment, "plan")
                assert reviewed["service"]["originally_running"] is originally_running
            with _native_upgrade_fault(adapter, port, failure, monkeypatch):
                applied = CliRunner().invoke(
                    cli,
                    [
                        "server",
                        "db-migrate",
                        "apply",
                        "--env-file",
                        str(environment),
                        "--manage-service",
                        "--backup",
                        "auto",
                        "--plan-id",
                        reviewed["plan_id"],
                        "--yes",
                        "--maintenance-confirmed",
                    ],
                )
                result = json.loads(applied.output)
                if failure in {"startup", "switch"}:
                    assert applied.exit_code == 1, applied.output
                    assert result["error"] == "service_start_failed"
                    assert result["database_state"] == "ready" and result["revision"] == "v1_5_0_001"
                    assert adapter.manager_state() in {ManagerState.INACTIVE, ManagerState.FAILED}
                    phase = "switching" if failure == "switch" else "start_failed"
                    assert json.loads(controller.maintenance_path.read_text())["phase"] == phase
                    if failure == "switch":
                        assert adapter.inspect().definition == definition
                    else:
                        with socket.create_connection(("127.0.0.1", port), timeout=2):
                            pass
                else:
                    assert applied.exit_code == 0, f"{applied.output}\n{applied.exception!r}"
                    assert result["server_ready"] is True
                    assert result["backup_state"] == "completed"
                    assert result["backup_ref"] and Path(result["backup_ref"]).is_file()
            if failure in {"startup", "switch"}:
                before = database.read_bytes()
                with server_settings_context(env_file=environment) as settings:
                    assert deployment_runner(settings).verify().state == "ready"
                assert database.read_bytes() == before
                # Recovery starts the verified registration without another
                # migration apply or backup.
                assert controller.start().ok, _failure_diagnostics(adapter, tmp_path)
            installed = adapter.inspect().definition
            assert installed is not None
            assert installed.python_executable == service_python_executable()
            assert installed.package_version == version("powercontext")
            if not originally_running:
                assert adapter.manager_state() in {ManagerState.INACTIVE, ManagerState.FAILED}
                assert controller.maintenance_path.exists()
                assert controller.start().ok, _failure_diagnostics(adapter, tmp_path)
            assert controller.status().ok, _failure_diagnostics(adapter, tmp_path)
            assert not controller.maintenance_path.exists()
            retained = client.get(f"/v1/scopes/{scope_id}")
            assert retained.is_success, retained.text
            assert retained.json()["title"] == "Installed service upgrade"
            assert client.get("/health/ready").status_code == 200
    finally:
        _capture_native_failure(adapter, tmp_path)
        with suppress(Exception):
            controller.uninstall()
        _cleanup(adapter)


@contextmanager
def _native_upgrade_fault(
    adapter: NativeServiceAdapter, port: int, failure: str | None, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    with ExitStack() as resources, monkeypatch.context() as fault:
        if failure == "startup":
            native_start = adapter.start

            def occupy_then_start(*, reload_definition: bool) -> None:
                # Bind after migration and stopped checks, then let the actual
                # native manager attempt to start the registered executable.
                resources.enter_context(_occupy_service_port(port))
                native_start(reload_definition=reload_definition)

            fault.setattr(adapter, "start", occupy_then_start)
        elif failure == "switch":
            native_replace = os.replace

            def fail_definition_replace(source: str | os.PathLike[str], destination: str | os.PathLike[str]) -> None:
                if Path(destination) == adapter.artifact_path:
                    raise OSError(errno.ENOSPC, "Test registration storage is full")
                native_replace(source, destination)

            fault.setattr(os, "replace", fail_definition_replace)
        yield


@contextmanager
def _occupy_service_port(port: int) -> Iterator[None]:
    # Accept and close probe connections so repeated liveness checks cannot fill
    # the backlog and turn a live port conflict into a connection timeout.
    with TCPServer(("127.0.0.1", port), BaseRequestHandler, bind_and_activate=False) as listener:
        # Reuse the stopped server's port while its connections remain in TIME_WAIT.
        listener.allow_reuse_address = sys.platform != "win32"
        listener.server_bind()
        listener.server_activate()
        thread = Thread(target=listener.serve_forever, daemon=True)
        thread.start()
        try:
            yield
        finally:
            listener.shutdown()
            thread.join()


def _native_migration_command(cli, environment: Path, action: str, *options: str) -> dict[str, Any]:
    result = CliRunner().invoke(
        cli, ["server", "db-migrate", action, "--env-file", str(environment), "--manage-service", *options]
    )
    assert result.exit_code == 0, f"{result.output}\n{result.exception!r}"
    return json.loads(result.output)


def test_native_stopped_service_registration_updates_without_starting(tmp_path: Path) -> None:
    """Updating a stopped registration must leave activation to an explicit start."""

    adapter = _native_adapter(suffix="stopped-upgrade")
    controller = ServiceController(adapter)
    environment = _environment_file(tmp_path)
    try:
        assert controller.install(env_file=environment).ok
        original = adapter.inspect().definition
        assert original is not None and original.env_file is not None
        controller.stop()
        assert adapter.manager_state() is ManagerState.INACTIVE
        assert controller.maintenance_path.exists()

        # Keep the same valid configuration and permissions while changing its
        # protected identity, as an upgrade may update the deployment file.
        environment.write_text(environment.read_text(encoding="utf-8") + "# upgraded registration\n", encoding="utf-8")
        controller.install(env_file=environment)
        updated = adapter.inspect().definition
        assert updated is not None and updated.env_file is not None
        assert updated.env_file.path == original.env_file.path
        assert updated.env_file != original.env_file
        assert adapter.manager_state() is ManagerState.INACTIVE
        assert controller.maintenance_path.exists()

        controller.install(env_file=environment)
        assert adapter.inspect().definition == updated
        # Cross the native restart-policy boundary to catch accidental
        # enablement during either registration update or its idempotent retry.
        time.sleep(6)
        assert adapter.manager_state() is ManagerState.INACTIVE
        assert controller.maintenance_path.exists()
        loaded = adapter.loaded_registration()
        if isinstance(adapter, LaunchdUserAdapter):
            assert loaded.state is ManagerOwnershipState.NOT_LOADED
        else:
            assert loaded.state is ManagerOwnershipState.OWNED
            assert loaded.definition == updated

        assert controller.start().ok
        assert not controller.maintenance_path.exists()
        loaded = adapter.loaded_registration()
        assert loaded.state is ManagerOwnershipState.OWNED
        assert loaded.definition == updated
        assert adapter.manager_state() is ManagerState.ACTIVE
    finally:
        _capture_native_failure(adapter, tmp_path)
        with suppress(Exception):
            controller.uninstall()
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
    # Annotated: without it the key type is inferred as the union of the three
    # concrete classes, which `type(adapter)` (a `type[NativeServiceAdapter]`)
    # cannot index.
    expected_managers: dict[type[NativeServiceAdapter], str] = {
        LaunchdUserAdapter: "launchctl",
        SystemdUserAdapter: "systemctl",
        WindowsTaskSchedulerAdapter: "schtasks",
    }
    expected_manager = expected_managers[type(adapter)]
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
            assert payload["ProcessType"] == "Standard"
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
        _capture_native_failure(adapter, tmp_path)
        with suppress(Exception):
            controller.uninstall()
        _cleanup(adapter)


@pytest.mark.skipif(sys.platform != "darwin", reason="LaunchAgent scheduling is macOS-specific")
def test_native_service_upgrades_background_scheduling(tmp_path: Path) -> None:
    adapter = _native_adapter(suffix="upgrade")
    environment = _environment_file(tmp_path)
    controller = ServiceController(adapter)
    target = f"gui/{_current_uid()}/{adapter.identifier}"

    try:
        assert controller.install(env_file=environment).ok
        original_pid = re.search(r"^\s*pid = (\d+)\s*$", _run("launchctl", "print", target).stdout, re.MULTILINE)
        assert original_pid is not None
        original_definition = adapter.inspect().definition
        # Represent an existing registration from before the scheduling fix,
        # retaining its metadata and the live process until install reconciles it.
        payload = plistlib.loads(adapter.artifact_path.read_bytes())
        payload["ProcessType"] = "Background"
        adapter.artifact_path.write_bytes(plistlib.dumps(payload))

        assert controller.install(env_file=environment).ok

        assert adapter.inspect().definition == original_definition
        assert plistlib.loads(adapter.artifact_path.read_bytes())["ProcessType"] == "Standard"
        upgraded_pid = re.search(r"^\s*pid = (\d+)\s*$", _run("launchctl", "print", target).stdout, re.MULTILINE)
        assert upgraded_pid is not None and upgraded_pid.group(1) != original_pid.group(1)

        assert controller.install(env_file=environment).ok
        repeated_pid = re.search(r"^\s*pid = (\d+)\s*$", _run("launchctl", "print", target).stdout, re.MULTILINE)
        assert repeated_pid is not None and repeated_pid.group(1) == upgraded_pid.group(1)
    finally:
        _capture_native_failure(adapter, tmp_path)
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
        _capture_native_failure(adapter, tmp_path)
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


def _capture_native_failure(adapter: NativeServiceAdapter, tmp_path: Path) -> None:
    if sys.exception() is None or not isinstance(adapter, LaunchdUserAdapter):
        return
    # Cleanup unloads the job and removes its retry state, so workflow-level
    # diagnostics cannot recover the startup failure afterwards.
    log_dir = tmp_path / "data" / "logs"
    with suppress(Exception):
        log_dir.mkdir(parents=True, exist_ok=True)
        retry_state = log_dir / "launchd-retry-state.json"
        snapshot = {
            "retry_token_present": (log_dir / "launchd-retry.enabled").exists(),
            "retry_state": retry_state.read_text(encoding="utf-8") if retry_state.exists() else None,
        }
        (log_dir / "launchd-failure-snapshot.json").write_text(json.dumps(snapshot), encoding="utf-8")
    with suppress(Exception):
        result = subprocess.run(
            ["launchctl", "print", f"gui/{_current_uid()}/{adapter.identifier}"],  # noqa: S607
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        report = f"exit_code={result.returncode}\n{result.stdout}\n{result.stderr}"
        (log_dir / "launchd-before-cleanup.log").write_text(report, encoding="utf-8")
        print(f"LaunchAgent state before cleanup:\n{report}")
        pid = re.search(r"^\s*pid = (\d+)\s*$", result.stdout, re.MULTILINE)
        if result.returncode == 0 and pid is not None:
            # A running process with empty logs may still be blocked before
            # Server initialization. Sample only this test's launchd job.
            subprocess.run(
                ["/usr/bin/sample", pid.group(1), "1", "-file", str(log_dir / "launchd-process-sample.log")],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )


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
