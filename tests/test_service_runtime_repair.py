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
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from powercontext.service.controller import ServiceController
from powercontext.service.model import ManagerState, RegistrationState, ServiceDefinition, ServiceError, ServiceTarget
from tests.test_service import _manager_probe
from tests.test_service_ops_boundary import _controller


def test_runtime_repair_guards_interruption_and_resumes_without_starting(tmp_path: Path) -> None:
    controller, adapter, target = _controller(tmp_path)
    controller.install(require_ready=True)
    definition = adapter.definition
    assert definition is not None
    with (
        pytest.raises(RuntimeError, match="interrupted"),
        controller.runtime_repair(expected_definition=definition, repair_id="repair-1"),
    ):
        assert adapter.manager is ManagerState.INACTIVE
        Path(target.python_executable).unlink()
        raise RuntimeError("interrupted")
    assert "runtime repair" in (controller.status().recovery_action or "")
    with pytest.raises(ServiceError, match="owning Ops runtime repair"):
        controller.start()
    controller.stop()
    assert json.loads(controller.maintenance_path.read_text())["phase"] == "runtime_repair"
    with (
        pytest.raises(ServiceError, match="existing service maintenance"),
        controller.runtime_repair(expected_definition=definition, repair_id="another-operation"),
    ):
        pytest.fail("a different operation cannot clear the repair guard")
    with controller.runtime_repair(expected_definition=definition, repair_id="repair-1"):
        Path(target.python_executable).touch()
    assert controller.verify_stopped().manager is ManagerState.INACTIVE
    assert adapter.definition == definition
    assert controller.start().ok


def test_runtime_repair_rejects_registration_changes_and_preserves_guard(tmp_path: Path) -> None:
    controller, adapter, _target = _controller(tmp_path)
    controller.install(require_ready=True)
    definition = adapter.definition
    assert definition is not None
    with (
        pytest.raises(ServiceError, match="changed; review"),
        controller.runtime_repair(expected_definition=replace(definition, package_version="other"), repair_id="r"),
    ):
        pytest.fail("the approved definition no longer matches")
    assert adapter.manager is ManagerState.ACTIVE
    with (
        pytest.raises(ServiceError, match="changed during runtime repair"),
        controller.runtime_repair(expected_definition=definition, repair_id="r"),
    ):
        adapter.write(adapter.render(replace(definition, package_version="other")))
    assert json.loads(controller.maintenance_path.read_text())["phase"] == "runtime_repair"


def test_runtime_repair_does_not_clear_database_maintenance(tmp_path: Path) -> None:
    controller, adapter, _target = _controller(tmp_path)
    controller.install(require_ready=True)
    definition = adapter.definition
    assert definition is not None
    summary = controller.maintenance_summary()
    with controller.maintenance(expected_fingerprint=summary.fingerprint):
        pass
    before = controller.maintenance_path.read_bytes()
    with (
        pytest.raises(ServiceError, match="existing service maintenance"),
        controller.runtime_repair(expected_definition=definition, repair_id="r"),
    ):
        pytest.fail("database maintenance belongs to its existing owner")
    assert controller.maintenance_path.read_bytes() == before


def test_remove_during_interrupted_runtime_repair_allows_fresh_installation(tmp_path: Path) -> None:
    controller, adapter, _target = _controller(tmp_path)
    controller.install(require_ready=True)
    definition = adapter.definition
    assert definition is not None
    with pytest.raises(RuntimeError), controller.runtime_repair(expected_definition=definition, repair_id="r"):
        raise RuntimeError
    assert controller.uninstall().registration is RegistrationState.NOT_INSTALLED
    assert not controller.maintenance_path.exists()
    with (
        pytest.raises(ServiceError, match="no owned personal service"),
        controller.runtime_repair(expected_definition=definition, repair_id="r"),
    ):
        pytest.fail("recovery cannot recreate a subsequently removed service")
    assert controller.install(require_ready=True).ok


def test_runtime_repair_cannot_complete_with_missing_executable(tmp_path: Path) -> None:
    controller, adapter, target = _controller(tmp_path)
    controller.install(require_ready=True)
    controller.stop()
    definition = adapter.definition
    assert definition is not None
    with (
        pytest.raises(ServiceError, match="repaired Server executable is unavailable"),
        controller.runtime_repair(expected_definition=definition, repair_id="r"),
    ):
        Path(target.python_executable).unlink()
    assert json.loads(controller.maintenance_path.read_text())["phase"] == "runtime_repair"
    assert adapter.manager is ManagerState.INACTIVE


def test_managed_definition_preserves_isolation_and_legacy_serialization(tmp_path: Path) -> None:
    _old_controller, adapter, target = _controller(tmp_path)
    managed_target = ServiceTarget(target.python_executable, target.package_version, isolated_runtime=True)
    probe = _manager_probe(adapter)
    controller = ServiceController(adapter, target=managed_target, probe=probe, readiness_probe=probe)
    controller.install(require_ready=True)
    definition = adapter.definition
    assert definition is not None and definition.isolated_runtime
    assert ServiceDefinition.from_dict(definition.as_dict()) == definition
    legacy = replace(definition, isolated_runtime=False)
    assert "isolated_runtime" not in legacy.as_dict()
    assert ServiceDefinition.from_dict(legacy.as_dict()) == legacy
    with controller.runtime_repair(expected_definition=definition, repair_id="r"):
        pass
    assert adapter.definition == definition


def test_managed_launcher_ignores_injected_python_module(tmp_path: Path) -> None:
    controller, _adapter, _target = _controller(tmp_path)
    definition = replace(controller.preview_definition(), python_executable=sys.executable, isolated_runtime=True)
    injected = tmp_path / "injected"
    injected.mkdir()
    (injected / "powercontext_injected.py").write_text("print('injected module executed')\n", encoding="utf-8")
    environment = {**os.environ, "PYTHONPATH": str(injected)}
    command = definition.launcher_arguments(module="powercontext_injected")
    result = subprocess.run(command, cwd=injected, env=environment, capture_output=True, text=True, timeout=60)
    assert result.returncode != 0
    assert "injected module executed" not in result.stdout
    assert not (injected / "__pycache__").exists()
    # The interpreter actually receives no-bytecode mode, even when the
    # native launcher is used with a trusted module in its installed package.
    module_index = command.index("-m")
    flags = subprocess.run(
        [*command[:module_index], "-c", "import sys; print(sys.dont_write_bytecode, sys.flags.isolated)"],
        cwd=injected,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    assert flags.stdout.strip() == "True 1"
