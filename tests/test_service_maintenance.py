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
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

import powercontext.service.cli as service_cli
from powercontext.service.adapters.base import service_python_executable
from powercontext.service.adapters.systemd import SystemdUserAdapter
from powercontext.service.adapters.windows import WindowsTaskSchedulerAdapter
from powercontext.service.controller import ServiceController
from powercontext.service.model import (
    LivenessState,
    ManagerOwnershipState,
    ManagerRegistration,
    ManagerState,
    ProbeResult,
    ProbeState,
    ServiceError,
)
from powercontext.service.probe import probe_readiness
from tests.test_service import FakeAdapter, _definition, _LivenessHandler, _manager_probe, _serve


def _installed(tmp_path: Path, *, running: bool = True) -> tuple[ServiceController, FakeAdapter]:
    adapter = FakeAdapter(tmp_path)
    old_python = tmp_path / "old-python"
    old_python.touch()
    adapter.definition = _definition(tmp_path, python_executable=str(old_python), package_version="old")
    adapter.content = adapter.render(adapter.definition)
    adapter.loaded_definition = adapter.definition
    adapter.manager = ManagerState.ACTIVE if running else ManagerState.INACTIVE
    probe = _manager_probe(adapter)
    return ServiceController(adapter, probe=probe, readiness_probe=probe), adapter


def test_service_lifecycle_commands_preserve_registration_and_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, adapter = _installed(tmp_path)
    definition = adapter.definition
    monkeypatch.setattr(service_cli, "_controller", lambda: controller)
    runner = CliRunner()

    assert runner.invoke(service_cli.app, ["stop"]).exit_code == 0
    assert adapter.definition == definition
    assert adapter.suspended
    assert adapter.manager is ManagerState.INACTIVE
    assert runner.invoke(service_cli.app, ["start"]).exit_code == 0
    assert adapter.definition == definition
    assert not adapter.suspended
    assert adapter.manager is ManagerState.ACTIVE
    assert runner.invoke(service_cli.app, ["restart"]).exit_code == 0
    assert adapter.definition == definition
    assert adapter.manager is ManagerState.ACTIVE


@pytest.mark.parametrize("manager_loads_definition", [False, True])
def test_install_updates_a_manually_stopped_service_without_starting_the_removed_old_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manager_loads_definition: bool
) -> None:
    controller, adapter = _installed(tmp_path)
    original = adapter.definition
    assert original is not None
    controller.stop()
    Path(original.python_executable).unlink()
    if not manager_loads_definition:
        # An unloaded manager, like launchd after bootout, reads the new
        # artifact only when the user explicitly starts the service.
        monkeypatch.setattr(adapter, "update_suspended", lambda: None)
    monkeypatch.setattr(service_cli, "_controller", lambda: controller)
    runner = CliRunner()

    updated = runner.invoke(service_cli.app, ["install", "--start-on-login"])

    assert updated.exit_code == 0, updated.output
    assert "remains stopped" in updated.output
    assert "automatic activation suppressed" in updated.output
    assert "powercontext service start" in updated.output
    assert adapter.definition is not None
    assert adapter.definition.python_executable == service_python_executable()
    assert adapter.definition != original
    assert adapter.manager is ManagerState.INACTIVE
    assert adapter.suspended
    assert json.loads(controller.maintenance_path.read_text())["phase"] == "manual_stopped"
    assert "powercontext service start" in (controller.status().recovery_action or "")
    assert runner.invoke(service_cli.app, ["start"]).exit_code == 0
    assert adapter.manager is ManagerState.ACTIVE
    assert not adapter.suspended
    assert not controller.maintenance_path.exists()


@pytest.mark.parametrize("error_type", [ServiceError, KeyboardInterrupt])
@pytest.mark.parametrize("recovery_operation", ["install", "start"])
def test_interrupted_manual_registration_update_remains_stopped_and_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error_type: type[BaseException], recovery_operation: str
) -> None:
    controller, adapter = _installed(tmp_path)
    original = adapter.definition
    assert original is not None
    controller.stop()
    Path(original.python_executable).unlink()

    def fail_update() -> None:
        # A failed native replacement can retain the stopped old definition
        # after the artifact already contains the accepted new definition.
        adapter.loaded_definition = original
        raise error_type("interrupted native registration update")  # noqa: TRY003

    with monkeypatch.context() as injection:
        injection.setattr(adapter, "update_suspended", fail_update)
        with pytest.raises(error_type, match="interrupted native registration update"):
            controller.install()

    assert adapter.manager is ManagerState.INACTIVE
    assert adapter.suspended
    assert json.loads(controller.maintenance_path.read_text())["phase"] == "manual_switching"
    if recovery_operation == "install":
        assert controller.install().manager is ManagerState.INACTIVE
        assert adapter.suspended
        assert json.loads(controller.maintenance_path.read_text())["phase"] == "manual_stopped"
    assert controller.start().ok
    assert adapter.definition is not None
    assert adapter.definition.python_executable == service_python_executable()
    assert not adapter.suspended
    assert not controller.maintenance_path.exists()


def test_install_cannot_replace_a_service_with_an_unverified_migration(tmp_path: Path) -> None:
    controller, adapter = _installed(tmp_path)
    original = adapter.definition
    summary = controller.maintenance_summary()
    with controller.maintenance(expected_fingerprint=summary.fingerprint):
        pass

    with pytest.raises(ServiceError, match="finish migration"):
        controller.install()

    assert adapter.definition == original
    assert adapter.manager is ManagerState.INACTIVE
    assert adapter.suspended
    assert json.loads(controller.maintenance_path.read_text())["phase"] == "stopped"


def test_successful_maintenance_switches_to_confirmed_executable_and_preserves_configuration(tmp_path: Path) -> None:
    controller, adapter = _installed(tmp_path)
    original = adapter.definition
    assert original is not None
    summary = controller.maintenance_summary()
    assert summary.as_dict()["fingerprint"] == summary.fingerprint
    assert summary.target_definition.python_executable == service_python_executable()

    with controller.maintenance(expected_fingerprint=summary.fingerprint) as session:
        assert adapter.suspended
        assert adapter.manager is ManagerState.INACTIVE
        assert adapter.definition == original
        assert not session.completed
        completed = session.complete()

    assert completed.ok
    assert adapter.definition == summary.target_definition
    assert adapter.definition is not None
    assert adapter.definition.data_dir == original.data_dir
    assert adapter.definition.env_file == original.env_file
    assert not adapter.suspended
    assert not controller.maintenance_path.exists()


def test_failed_migration_keeps_service_stopped_and_retry_preserves_original_running_state(tmp_path: Path) -> None:
    controller, adapter = _installed(tmp_path)
    summary = controller.maintenance_summary()
    original = adapter.definition

    with (
        pytest.raises(RuntimeError, match="migration failed"),
        controller.maintenance(expected_fingerprint=summary.fingerprint),
    ):
        raise RuntimeError("migration failed")  # noqa: TRY003

    assert adapter.suspended
    assert adapter.manager is ManagerState.INACTIVE
    assert adapter.definition == original
    with pytest.raises(ServiceError, match="not been verified"):
        controller.start()
    assert controller.maintenance_summary().fingerprint == summary.fingerprint
    with controller.maintenance(expected_fingerprint=summary.fingerprint) as session:
        session.complete()
    assert adapter.manager is ManagerState.ACTIVE


def test_maintenance_retains_an_originally_stopped_service_without_automatic_start(tmp_path: Path) -> None:
    controller, adapter = _installed(tmp_path, running=False)
    summary = controller.maintenance_summary()
    with controller.maintenance(expected_fingerprint=summary.fingerprint) as session:
        session.complete()

    assert adapter.manager is ManagerState.INACTIVE
    assert adapter.suspended
    assert adapter.definition == summary.target_definition
    assert json.loads(controller.maintenance_path.read_text())["phase"] == "database_ready"
    assert not any(event.startswith("start:") for event in adapter.events)
    assert controller.start().ok


def test_changed_service_plan_is_rejected_before_stopping_the_service(tmp_path: Path) -> None:
    controller, adapter = _installed(tmp_path)
    summary = controller.maintenance_summary()
    assert adapter.definition is not None
    adapter.definition = replace(adapter.definition, endpoint="http://127.0.0.1:9000")
    adapter.loaded_definition = adapter.definition

    with pytest.raises(ServiceError, match="changed"), controller.maintenance(expected_fingerprint=summary.fingerprint):
        pytest.fail("a changed service plan must not enter maintenance")
    assert not adapter.suspended
    assert adapter.manager is ManagerState.ACTIVE
    assert not controller.maintenance_path.exists()


def test_service_completion_requires_the_still_locked_maintenance_session(tmp_path: Path) -> None:
    controller, adapter = _installed(tmp_path)
    original = adapter.definition
    summary = controller.maintenance_summary()
    with controller.maintenance(expected_fingerprint=summary.fingerprint) as session:
        pass

    with pytest.raises(ServiceError, match="active, uncompleted"):
        session.complete()
    assert adapter.definition == original
    assert adapter.suspended
    assert adapter.manager is ManagerState.INACTIVE


@pytest.mark.parametrize("operation", ["stop", "start", "restart", "maintenance_summary"])
def test_foreign_native_registration_is_not_mutated(tmp_path: Path, operation: str) -> None:
    controller, adapter = _installed(tmp_path)
    adapter.manager_registration_override = ManagerRegistration(ManagerOwnershipState.FOREIGN, detail="foreign task")

    with pytest.raises(ServiceError, match="foreign task"):
        getattr(controller, operation)()
    assert adapter.events == []
    assert not controller.maintenance_path.exists()


def test_startup_readiness_failure_is_separate_from_database_ready_and_keeps_service_stopped(tmp_path: Path) -> None:
    controller, adapter = _installed(tmp_path)
    controller = ServiceController(
        adapter,
        probe=_manager_probe(adapter),
        readiness_probe=lambda _: ProbeResult(ProbeState.CONFLICT, "schema is not ready"),
    )
    summary = controller.maintenance_summary()
    with (
        controller.maintenance(expected_fingerprint=summary.fingerprint) as session,
        pytest.raises(ServiceError, match="failed readiness"),
    ):
        session.complete()
    assert adapter.suspended
    assert adapter.manager is ManagerState.INACTIVE
    assert adapter.definition == summary.target_definition
    assert json.loads(controller.maintenance_path.read_text())["phase"] == "start_failed"


@pytest.mark.parametrize("startup_check", ["liveness", "readiness"])
def test_cancelled_maintenance_startup_stays_stopped_until_explicit_recovery(
    tmp_path: Path, startup_check: str
) -> None:
    _, adapter = _installed(tmp_path)
    manager_probe = _manager_probe(adapter)
    interrupt_pending = False

    def probe(endpoint: str) -> ProbeResult:
        nonlocal interrupt_pending
        result = manager_probe(endpoint)
        if startup_check == "liveness" and interrupt_pending and result.state is ProbeState.LIVE:
            interrupt_pending = False
            raise KeyboardInterrupt
        return result

    def readiness_probe(endpoint: str) -> ProbeResult:
        nonlocal interrupt_pending
        if startup_check == "readiness" and interrupt_pending:
            interrupt_pending = False
            raise KeyboardInterrupt
        return manager_probe(endpoint)

    controller = ServiceController(adapter, probe=probe, readiness_probe=readiness_probe)
    summary = controller.maintenance_summary()
    with controller.maintenance(expected_fingerprint=summary.fingerprint) as session:
        # Only the new service's startup check is interrupted, after migration
        # has stopped the old service and the caller has verified the database.
        interrupt_pending = True
        with pytest.raises(KeyboardInterrupt):
            session.complete()
        assert not session.completed

    assert adapter.manager is ManagerState.INACTIVE
    assert adapter.suspended
    assert adapter.definition == summary.target_definition
    assert json.loads(controller.maintenance_path.read_text())["phase"] == "start_failed"
    assert controller.start().ok
    assert adapter.definition == summary.target_definition
    assert not adapter.suspended
    assert not controller.maintenance_path.exists()


def test_cancelled_public_start_restores_stopped_service_and_allows_explicit_retry(tmp_path: Path) -> None:
    controller, adapter = _installed(tmp_path)
    controller.stop()
    original = adapter.definition
    manager_probe = _manager_probe(adapter)
    interrupt_pending = True

    def readiness_probe(endpoint: str) -> ProbeResult:
        nonlocal interrupt_pending
        if interrupt_pending:
            interrupt_pending = False
            raise KeyboardInterrupt
        return manager_probe(endpoint)

    controller = ServiceController(adapter, probe=manager_probe, readiness_probe=readiness_probe)
    with pytest.raises(KeyboardInterrupt):
        controller.start()

    assert adapter.manager is ManagerState.INACTIVE
    assert adapter.suspended
    assert adapter.definition == original
    assert json.loads(controller.maintenance_path.read_text())["phase"] == "manual_stopped"
    recovered = controller.start()
    assert recovered.manager is ManagerState.ACTIVE
    assert recovered.server_liveness is LivenessState.LIVE
    assert adapter.definition == original
    assert not adapter.suspended
    assert not controller.maintenance_path.exists()


@pytest.mark.skipif(os.name == "nt", reason="requires creating a symlink without elevation")
def test_foreign_maintenance_record_is_not_overwritten(tmp_path: Path) -> None:
    controller, adapter = _installed(tmp_path)
    external = tmp_path / "external.json"
    external.write_text('{"phase":"manual_stopped"}')
    controller.maintenance_path.symlink_to(external)

    with pytest.raises(ServiceError, match="regular file"):
        controller.stop()
    assert adapter.events == []
    assert external.read_text() == '{"phase":"manual_stopped"}'


@pytest.mark.parametrize(
    ("status", "code", "expected"),
    [("ready", 200, ProbeState.LIVE), ("degraded", 200, ProbeState.LIVE), ("not_ready", 503, ProbeState.UNREACHABLE)],
)
def test_service_readiness_uses_business_readiness_beyond_liveness(
    status: str, code: int, expected: ProbeState
) -> None:
    class Handler(_LivenessHandler):
        def do_GET(self) -> None:
            if self.path == "/health/live":
                super().do_GET()
                return
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-PowerContext-Request-ID", "0123456789abcdef")
            self.end_headers()
            self.wfile.write(json.dumps({"status": status, "checks": {"application": status}}).encode())

    server, thread, endpoint = _serve(Handler)
    try:
        assert probe_readiness(endpoint).state is expected
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_windows_definition_switch_registers_a_disabled_task_without_changing_login_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = WindowsTaskSchedulerAdapter(
        config_home=tmp_path,
        identifier="PowerContext Maintenance Test",
        user_account="test\\alice",
        user_sid="S-1-5-21-1",
    )
    definition = _definition(tmp_path)
    adapter.write(adapter.render(definition))
    monkeypatch.setattr(
        adapter, "loaded_registration", lambda: ManagerRegistration(ManagerOwnershipState.OWNED, definition=definition)
    )
    registered: list[bytes] = []

    def run(*arguments: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if "/XML" in arguments:
            registered.append(Path(arguments[arguments.index("/XML") + 1]).read_bytes())
        return subprocess.CompletedProcess(["schtasks.exe", *arguments], 0, "", "")

    monkeypatch.setattr(adapter, "_run", run)
    adapter.update_suspended()

    assert registered
    loaded = ET.fromstring(registered[-1])  # noqa: S314
    namespace = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"
    enabled = loaded.find(f"{namespace}Settings/{namespace}Enabled")
    assert enabled is not None and enabled.text == "false"
    assert loaded.find(f"{namespace}Triggers/{namespace}LogonTrigger") is not None
    assert adapter.inspect().definition == definition


def test_systemd_maintenance_guard_uses_a_negated_absolute_path_and_preserves_the_unit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = SystemdUserAdapter(config_home=tmp_path / "config with $ and %")
    definition = _definition(tmp_path)
    unit = adapter.render(definition)
    adapter.write(unit)
    marker = adapter.lock_path.with_name(f"{adapter.lock_path.name}.maintenance.json")
    marker.write_text('{"phase":"stopped"}')
    monkeypatch.setattr(
        adapter, "loaded_registration", lambda: ManagerRegistration(ManagerOwnershipState.OWNED, definition=definition)
    )
    monkeypatch.setattr(adapter, "reload", lambda: None)

    adapter.suspend(marker)

    dropin = adapter.artifact_path.with_name(f"{adapter.identifier}.d") / "90-powercontext-maintenance.conf"
    assert f"ConditionPathExists=!{str(marker).replace('%', '%%')}\n" in dropin.read_text()
    assert adapter.artifact_path.read_bytes() == unit


@pytest.mark.parametrize("after_reload", [False, True])
def test_interrupted_definition_switch_can_resume_with_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    after_reload: bool,
) -> None:
    controller, adapter = _installed(tmp_path)
    summary = controller.maintenance_summary()
    reload = adapter.update_suspended

    def fail():
        assert json.loads(controller.maintenance_path.read_text())["phase"] == "switching"
        if after_reload:
            reload()
        raise OSError("interrupted reload")  # noqa: TRY003

    with (
        controller.maintenance(expected_fingerprint=summary.fingerprint) as session,
        monkeypatch.context() as injection,
    ):
        injection.setattr(adapter, "update_suspended", fail)
        with pytest.raises(OSError, match="interrupted reload"):
            session.complete()
    assert adapter.suspended
    assert adapter.manager is ManagerState.INACTIVE
    assert controller.start().ok
    assert adapter.definition == summary.target_definition
    assert not controller.maintenance_path.exists()


def test_stop_waits_for_transient_connection_conflict(tmp_path: Path) -> None:
    _, adapter = _installed(tmp_path)
    outcomes = iter([
        ProbeResult(ProbeState.LIVE, "owned server"),
        ProbeResult(ProbeState.CONFLICT, "connection reset during shutdown"),
        ProbeResult(ProbeState.UNREACHABLE, "stopped"),
    ])
    controller = ServiceController(
        adapter,
        probe=lambda _: next(outcomes, ProbeResult(ProbeState.UNREACHABLE, "stopped")),
        sleep=lambda _: None,
    )
    controller.stop()
    assert adapter.manager is ManagerState.INACTIVE
    assert adapter.suspended


def test_stop_rejects_a_persistent_listener_after_wait_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import powercontext.service.controller as module

    _, adapter = _installed(tmp_path)
    elapsed = iter([0.0, 0.1, 31.0])
    monkeypatch.setattr(module.time, "monotonic", lambda: next(elapsed))
    controller = ServiceController(
        adapter,
        probe=lambda _: ProbeResult(
            ProbeState.LIVE if adapter.manager is ManagerState.ACTIVE else ProbeState.CONFLICT, "listener remains"
        ),
        sleep=lambda _: None,
    )
    with pytest.raises(ServiceError, match=r"cannot prove.*stopped"):
        controller.stop()
    assert adapter.suspended
    assert controller.maintenance_path.exists()


@pytest.mark.parametrize("manual_update", [False, True], ids=["migration", "manual-update"])
@pytest.mark.parametrize("changed_registration", ["artifact", "loaded"])
def test_pending_switch_refuses_a_replaced_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manual_update: bool, changed_registration: str
) -> None:
    controller, adapter = _installed(tmp_path)
    summary = controller.maintenance_summary()

    def fail():
        raise OSError("reload failed")  # noqa: TRY003

    with monkeypatch.context() as injection:
        injection.setattr(adapter, "update_suspended", fail)
        if manual_update:
            controller.stop()
            with pytest.raises(OSError):
                controller.install()
        else:
            with controller.maintenance(expected_fingerprint=summary.fingerprint) as session, pytest.raises(OSError):
                session.complete()
    assert adapter.definition is not None
    replacement = replace(adapter.definition, endpoint="http://127.0.0.1:9010")
    if changed_registration == "artifact":
        adapter.write(adapter.render(replacement))
    else:
        adapter.loaded_definition = replacement
    events = list(adapter.events)
    with pytest.raises(ServiceError, match="changed outside"):
        controller.start()
    assert adapter.events == events
    assert adapter.suspended
