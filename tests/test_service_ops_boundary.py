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
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from powercontext.service.adapters.windows import WindowsTaskSchedulerAdapter
from powercontext.service.controller import ServiceController
from powercontext.service.model import (
    DefinitionState,
    ManagerState,
    ProbeResult,
    ProbeState,
    RegistrationState,
    ServiceError,
    ServiceTarget,
    SupportState,
)
from tests.test_service import FakeAdapter, _manager_probe, _secure_windows_file


def _controller(tmp_path: Path) -> tuple[ServiceController, FakeAdapter, ServiceTarget]:
    executable = tmp_path / ("pythonw.exe" if sys.platform == "win32" else "python")
    executable.touch()
    target = ServiceTarget(str(executable), "server-version-different-from-ops")
    adapter = FakeAdapter(tmp_path)
    probe = _manager_probe(adapter)
    return ServiceController(adapter, target=target, probe=probe, readiness_probe=probe), adapter, target


def _configuration(tmp_path: Path, name: str, port: int = 8000) -> Path:
    path = tmp_path / name
    path.write_text(f"POWERCONTEXT_SERVER_HTTP_PORT={port}\n", encoding="utf-8")
    path.chmod(0o600)
    _secure_windows_file(path)
    return path


def test_target_identity_survives_independent_ops_and_missing_server_runtime(tmp_path: Path) -> None:
    controller, adapter, target = _controller(tmp_path)
    assert controller.install(require_ready=True).ok
    assert adapter.definition is not None
    assert adapter.definition.python_executable == target.python_executable
    assert adapter.definition.package_version == target.package_version
    assert controller.registration_status().definition is DefinitionState.CURRENT
    assert controller.maintenance_summary().target_definition == adapter.definition
    Path(target.python_executable).unlink()
    assert controller.status().definition is DefinitionState.MISSING_EXECUTABLE
    assert controller.stop().manager is ManagerState.INACTIVE
    assert controller.uninstall().registration is RegistrationState.NOT_INSTALLED


def test_windows_manager_support_does_not_require_ops_pythonw(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import powercontext.service.adapters.windows as windows

    monkeypatch.setattr(windows.sys, "platform", "win32")
    monkeypatch.setattr(windows.sys, "executable", str(tmp_path / "missing" / "python.exe"))
    monkeypatch.setattr(windows.shutil, "which", lambda name: name)
    monkeypatch.setattr(windows, "_current_user_identity", lambda: ("test\\user", "S-1-5-21-1000"))
    adapter = WindowsTaskSchedulerAdapter(home=tmp_path, user_account="test\\user", user_sid="S-1-5-21-1000")
    assert adapter.platform_support()[0] is SupportState.SUPPORTED


def test_readiness_reconciliation_never_starts_a_service_that_exited(tmp_path: Path) -> None:
    controller, adapter, _target = _controller(tmp_path)
    controller.install(require_ready=True)
    assert controller.verify_running().ok
    adapter.manager = ManagerState.INACTIVE
    with pytest.raises(ServiceError, match="no longer running"):
        controller.verify_running()
    assert adapter.manager is ManagerState.INACTIVE
    assert not controller.maintenance_path.exists()


def test_stopped_reconciliation_rejects_a_conflicting_listener(tmp_path: Path) -> None:
    controller, adapter, target = _controller(tmp_path)
    controller.install(require_ready=True)
    controller.stop()
    assert controller.verify_stopped().manager is ManagerState.INACTIVE
    conflict = ServiceController(
        adapter, target=target, probe=lambda endpoint: ProbeResult(ProbeState.CONFLICT, "different listener")
    )
    with pytest.raises(ServiceError, match="another listener"):
        conflict.verify_stopped()
    assert adapter.manager is ManagerState.INACTIVE


def test_initial_registration_requires_readiness_and_guards_failed_start(tmp_path: Path) -> None:
    _controller_instance, adapter, target = _controller(tmp_path)
    controller = ServiceController(
        adapter,
        target=target,
        probe=_manager_probe(adapter),
        readiness_probe=lambda endpoint: ProbeResult(ProbeState.CONFLICT, "not ready"),
    )
    with pytest.raises(ServiceError, match="failed readiness"):
        controller.install(require_ready=True)
    assert adapter.definition is not None
    assert adapter.suspended
    assert adapter.manager is ManagerState.INACTIVE
    assert json.loads(controller.maintenance_path.read_text())["phase"] == "start_failed"
    recovered = ServiceController(
        adapter, target=target, probe=_manager_probe(adapter), readiness_probe=_manager_probe(adapter)
    )
    assert recovered.install(require_ready=True).ok
    assert not recovered.maintenance_path.exists()


def test_configuration_revision_preserves_original_file_and_explicit_stop(tmp_path: Path) -> None:
    controller, adapter, _target = _controller(tmp_path)
    original = _configuration(tmp_path, "original.env")
    controller.install(env_file=original, require_ready=True)
    controller.stop()
    revision = _configuration(tmp_path, "revision.env", 8100)
    before = original.read_bytes()
    status = controller.configure(env_file=revision, restart=True)
    assert status.manager is ManagerState.INACTIVE
    assert adapter.definition is not None and adapter.definition.endpoint == "http://127.0.0.1:8100"
    assert adapter.definition.env_file is not None and adapter.definition.env_file.path == str(revision)
    assert original.read_bytes() == before
    assert adapter.suspended
    assert controller.start().ok


def test_unchanged_configuration_with_activation_authorization_still_verifies_readiness(tmp_path: Path) -> None:
    controller, adapter, target = _controller(tmp_path)
    original = _configuration(tmp_path, "original.env")
    controller.install(env_file=original, require_ready=True)
    unready = ServiceController(
        adapter,
        target=target,
        probe=_manager_probe(adapter),
        readiness_probe=lambda endpoint: ProbeResult(ProbeState.CONFLICT, "not ready"),
    )
    with pytest.raises(ServiceError, match="running service is not ready"):
        unready.configure(env_file=original, restart=True)
    assert adapter.manager is ManagerState.ACTIVE


def test_configuration_failure_blocks_start_and_resumes_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, adapter, _target = _controller(tmp_path)
    original = _configuration(tmp_path, "original.env")
    controller.install(env_file=original, require_ready=True)
    revision = _configuration(tmp_path, "revision.env", 8100)

    def interrupt() -> None:
        raise KeyboardInterrupt

    with monkeypatch.context() as fault:
        fault.setattr(adapter, "update_suspended", interrupt)
        with pytest.raises(KeyboardInterrupt):
            controller.configure(env_file=revision, restart=True)
    with pytest.raises(ServiceError, match="not been verified"):
        controller.start()
    assert adapter.manager is ManagerState.INACTIVE
    recovery = controller.status().recovery_action
    assert recovery is not None and "Ops" in recovery and "config_apply" in recovery
    assert controller.configure(env_file=revision, restart=True).manager is ManagerState.INACTIVE
    assert controller.start().ok


def test_configuration_switch_rejects_replaced_revision_after_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, adapter, _target = _controller(tmp_path)
    controller.install(require_ready=True)
    revision = _configuration(tmp_path, "revision.env", 8100)

    def interrupt() -> None:
        raise KeyboardInterrupt

    with monkeypatch.context() as fault:
        fault.setattr(adapter, "update_suspended", interrupt)
        with pytest.raises(KeyboardInterrupt):
            controller.configure(env_file=revision)
    revision.write_text("POWERCONTEXT_SERVER_HTTP_PORT=8200\n", encoding="utf-8")
    with pytest.raises(ServiceError, match="revision changed"):
        controller.configure(env_file=revision)
    assert adapter.manager is ManagerState.INACTIVE


def test_configuration_rejects_changed_original_with_a_service_error(tmp_path: Path) -> None:
    controller, adapter, _target = _controller(tmp_path)
    original = _configuration(tmp_path, "original.env")
    controller.install(env_file=original, require_ready=True)
    installed = adapter.definition
    revision = _configuration(tmp_path, "revision.env", 8100)
    original.write_text("POWERCONTEXT_SERVER_HTTP_PORT=8200\n", encoding="utf-8")
    with pytest.raises(ServiceError, match="registered environment file is not usable"):
        controller.configure(env_file=revision)
    assert adapter.definition == installed
    assert adapter.manager is ManagerState.ACTIVE
    assert not controller.maintenance_path.exists()


def test_maintenance_switch_rejects_changed_environment_with_a_service_error(tmp_path: Path) -> None:
    controller, adapter, _target = _controller(tmp_path)
    original = _configuration(tmp_path, "original.env")
    controller.install(env_file=original, require_ready=True)
    summary = controller.maintenance_summary()
    with controller.maintenance(expected_fingerprint=summary.fingerprint) as maintenance:
        original.write_text("POWERCONTEXT_SERVER_HTTP_PORT=8200\n", encoding="utf-8")
        with pytest.raises(ServiceError, match="pending service environment file is not usable"):
            maintenance.complete()
    assert adapter.manager is ManagerState.INACTIVE
    assert adapter.suspended
    assert json.loads(controller.maintenance_path.read_text())["phase"] == "switching"


@pytest.mark.parametrize("invalid_target", [None, {}])
def test_configuration_recovery_rejects_malformed_intent_with_a_service_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_target: object
) -> None:
    controller, adapter, _target = _controller(tmp_path)
    controller.install(require_ready=True)
    revision = _configuration(tmp_path, "revision.env", 8100)

    def interrupt() -> None:
        raise KeyboardInterrupt

    with monkeypatch.context() as fault:
        fault.setattr(adapter, "update_suspended", interrupt)
        with pytest.raises(KeyboardInterrupt):
            controller.configure(env_file=revision)
    record = json.loads(controller.maintenance_path.read_text())
    record["summary"]["target_definition"] = invalid_target
    controller.maintenance_path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ServiceError, match="pending service switch evidence is invalid"):
        controller.configure(env_file=revision)
    assert adapter.manager is ManagerState.INACTIVE
    assert adapter.suspended
    assert json.loads(controller.maintenance_path.read_text()) == record


def test_stop_accepts_verified_old_loaded_definition_during_configuration_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, adapter, _target = _controller(tmp_path)
    controller.install(require_ready=True)
    original = adapter.definition
    revision = _configuration(tmp_path, "revision.env", 8100)

    def interrupt() -> None:
        # Windows can retain the old disabled task after publishing the new XML.
        adapter.loaded_definition = original
        raise KeyboardInterrupt

    with monkeypatch.context() as fault:
        fault.setattr(adapter, "update_suspended", interrupt)
        with pytest.raises(KeyboardInterrupt):
            controller.configure(env_file=revision)
    assert controller.stop().manager is ManagerState.INACTIVE
    assert json.loads(controller.maintenance_path.read_text())["phase"] == "configuration_switching"
    assert controller.configure(env_file=revision).manager is ManagerState.INACTIVE


def test_configuration_cancellation_at_consistent_boundary_keeps_service_stopped(tmp_path: Path) -> None:
    controller, adapter, _target = _controller(tmp_path)
    controller.install(require_ready=True)
    revision = _configuration(tmp_path, "revision.env", 8100)
    result = controller.configure(env_file=revision, restart=True, cancelled=lambda: True)
    assert result.manager is ManagerState.INACTIVE
    assert adapter.definition is not None and adapter.definition.env_file is not None
    assert adapter.definition.env_file.path == str(revision)
    assert controller.start().ok


def test_pending_database_migration_cannot_be_replaced_by_configuration(tmp_path: Path) -> None:
    controller, adapter, _target = _controller(tmp_path)
    controller.install(require_ready=True)
    summary = controller.maintenance_summary()
    with controller.maintenance(expected_fingerprint=summary.fingerprint):
        pass
    revision = _configuration(tmp_path, "revision.env", 8100)
    with pytest.raises(ServiceError, match="existing service maintenance"):
        controller.configure(env_file=revision)
    assert adapter.definition == summary.definition
    assert adapter.suspended


def test_mutation_nesting_retains_real_process_exclusion_and_thread_exclusion(tmp_path: Path) -> None:
    controller, _adapter, _target = _controller(tmp_path)
    script = """import sys
from pathlib import Path
from powercontext.service.controller import _service_lock
from powercontext.service.model import ServiceError
try:
    with _service_lock(Path(sys.argv[1]), timeout=0.1):
        print('acquired')
except ServiceError:
    print('busy')
"""

    def competing_thread() -> str:
        try:
            with controller.mutation(timeout=0.1):
                return "acquired"
        except ServiceError:
            return "busy"

    with ThreadPoolExecutor(max_workers=1) as executor, controller.mutation():
        controller.install(require_ready=True)
        assert executor.submit(competing_thread).result(timeout=5) == "busy"
        child = subprocess.run(
            [sys.executable, "-c", script, str(tmp_path / ".powercontext.test.lock")],
            capture_output=True,
            text=True,
            timeout=45,
            check=True,
        )
        assert child.stdout.strip() == "busy"
    assert competing_thread() == "acquired"
