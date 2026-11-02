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

"""Ops behavior with an isolated fake native manager; no host service is changed."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import sysconfig
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest

import powercontext.ops.runtime as ops_runtime
from powercontext.ops import Ops, TargetBinding
from powercontext.ops.protocol import canonical
from powercontext.service.controller import ServiceController
from powercontext.service.environment import load_protected_environment_file, protect_directory, write_protected_file
from powercontext.service.model import ManagerState, ProbeResult, ProbeState, ServiceError, ServiceTarget
from tests.test_service import FakeAdapter, _manager_probe


def _request(action: str, **fields: Any) -> dict[str, Any]:
    return {"protocol_version": 1, "request_id": "test", "action": action, **fields}


def _plan(ops: Ops, operation: str, **options: Any) -> dict[str, Any]:
    result = ops.handle(_request("preflight", operation=operation, options=options))
    assert result["outcome"] == "succeeded", result
    return result["plan"]


def _apply_request(plan: dict[str, Any], operation_id: str = "operation") -> dict[str, Any]:
    return _request(
        "apply", operation_id=operation_id, plan=plan, authorization={"plan_id": plan["plan_id"], "approved": True}
    )


@pytest.fixture
def fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Ops, FakeAdapter]:
    for key in tuple(os.environ):
        if key == "POWERCONTEXT_HOME" or key.startswith("POWERCONTEXT_SERVER_"):
            monkeypatch.delenv(key)
    directory = protect_directory(tmp_path / "private", create=True)
    executable = directory / ("pythonw.exe" if sys.platform == "win32" else "server-python")
    executable.touch()
    environment = directory / "server.env"
    write_protected_file(environment, b"POWERCONTEXT_SERVER_HTTP_HOST=127.0.0.1\nPOWERCONTEXT_SERVER_HTTP_PORT=8888\n")
    target = TargetBinding("personal", str(executable), "target-version", str(environment), True)
    adapter = FakeAdapter(directory)
    controller = ServiceController(
        adapter,
        target=ServiceTarget(target.python_executable, target.package_version),
        probe=_manager_probe(adapter),
        readiness_probe=_manager_probe(adapter),
    )
    return Ops(target, directory / "operations", controller=controller), adapter


def test_preflight_is_readonly_and_unknown_protocol_has_no_record(fixture: tuple[Ops, FakeAdapter]) -> None:
    ops, adapter = fixture
    plan = _plan(ops, "register")
    assert plan["effects"] == {"may_start": True, "preserves_data": True}
    assert plan["target_summary"]["python_executable"] == ops.target.python_executable
    assert plan["target_summary"]["env_file"] == ops.target.env_file
    assert plan["target_summary"]["endpoint"] == "http://127.0.0.1:8888"
    assert Path(plan["target_summary"]["data_dir"]).is_absolute()
    assert not ops.store.directory.exists()
    result = ops.handle({**_request("inspect"), "protocol_version": 42})
    assert result["error"]["code"] == "unsupported_protocol"
    assert not ops.store.directory.exists()
    assert adapter.definition is None


def test_duplicate_apply_returns_original_result_after_native_state_changes(fixture: tuple[Ops, FakeAdapter]) -> None:
    ops, adapter = fixture
    request = _apply_request(_plan(ops, "register"))
    first = ops.handle(request)
    assert first["outcome"] == "succeeded", first
    assert adapter.definition is not None
    assert adapter.definition.python_executable == ops.target.python_executable
    assert adapter.definition.package_version == "target-version"
    # A lost final response must not turn into stale-plan failure or a restart.
    adapter.manager = ManagerState.INACTIVE
    events_before = list(adapter.events)
    duplicate = ops.handle(request)
    assert duplicate["result"] == first["result"]
    assert adapter.events == events_before
    assert duplicate["attempt"] == 1
    assert ops.handle(_request("inspect"))["components"]["manager"] == "inactive"


def test_approval_stale_plan_and_unknown_options_reject_before_acceptance(fixture: tuple[Ops, FakeAdapter]) -> None:
    ops, adapter = fixture
    plan = _plan(ops, "register")
    request = _apply_request(plan)
    request["authorization"]["approved"] = False
    assert ops.handle(request)["error"]["code"] == "authorization_required"
    assert not ops.store.directory.exists()
    request["authorization"]["approved"] = True
    write_protected_file(Path(ops.target.env_file), b"POWERCONTEXT_SERVER_HTTP_PORT=9999\n")
    assert ops.handle(request)["error"]["code"] == "plan_stale"
    assert ops.store.operation("operation") is None
    bad = ops.handle(_request("preflight", operation="register", options={"executable": "arbitrary"}))
    assert bad["error"]["code"] == "invalid_request"
    assert adapter.definition is None


def test_cancel_resume_deduplicates_attempts_and_old_cancel_does_not_apply(fixture: tuple[Ops, FakeAdapter]) -> None:
    ops, adapter = fixture
    plan = _plan(ops, "register")
    request = _apply_request(plan)

    def cancel_on_accept(message: dict[str, Any]) -> None:
        if message["type"] == "accepted":
            reply = ops.handle(_request("cancel", operation_id="operation", attempt=1))
            assert reply["cancel_requested"]
            assert reply["outcome"] == "pending"

    first = ops.handle(request, emit=cancel_on_accept)
    assert first["outcome"] == "cancelled"
    assert adapter.definition is None
    resume = {**request, "action": "resume", "expected_attempt": 1}
    second = ops.handle(resume)
    assert second["outcome"] == "succeeded", second
    assert second["attempt"] == 2
    assert not second["cancel_requested"]
    events_before = list(adapter.events)
    assert ops.handle(resume)["result"] == second["result"]
    assert ops.handle(request)["attempt"] == 2
    assert adapter.events == events_before
    late = ops.handle(_request("cancel", operation_id="operation", attempt=2))
    assert late["outcome"] == "succeeded"
    assert not late["cancel_requested"]
    assert second["history"][0]["outcome"] == "cancelled"


def test_configuration_switch_preserves_secrets_and_stopped_state(fixture: tuple[Ops, FakeAdapter]) -> None:
    ops, adapter = fixture
    original = Path(ops.target.env_file)
    content = original.read_bytes() + b"CUSTOM_TOKEN='private token value'\n"
    write_protected_file(original, content)
    assert ops.handle(_apply_request(_plan(ops, "register"), "register"))["outcome"] == "succeeded"
    assert ops.handle(_apply_request(_plan(ops, "stop"), "stop"))["outcome"] == "succeeded"
    result = ops.handle(_apply_request(_plan(ops, "config_apply", http_port=9999), "configure"))
    assert result["outcome"] == "succeeded", result
    assert adapter.manager == ManagerState.INACTIVE
    assert adapter.definition is not None and adapter.definition.env_file is not None
    assert adapter.definition.endpoint == "http://127.0.0.1:9999"
    assert original.read_bytes() == content
    current = load_protected_environment_file(Path(adapter.definition.env_file.path))
    assert current.values["CUSTOM_TOKEN"] == "private token value"  # noqa: S105
    assert "private token value" not in json.dumps(result)
    assert "private token value" not in (ops.store.directory / "configure.json").read_text()
    # Subsequent changes must read the selected immutable revision.
    again = ops.handle(_apply_request(_plan(ops, "config_apply", dashboard_enabled=True), "configure-again"))
    assert again["outcome"] == "succeeded", again
    assert adapter.definition.endpoint == "http://127.0.0.1:9999"


def test_damaged_server_can_be_stopped_and_removed(fixture: tuple[Ops, FakeAdapter]) -> None:
    ops, adapter = fixture
    assert ops.handle(_apply_request(_plan(ops, "register"), "register"))["outcome"] == "succeeded"
    Path(ops.target.python_executable).unlink()
    observed = ops.handle(_request("inspect"))
    assert observed["components"]["definition"] == "missing_executable"
    assert observed["components"]["readiness"] == "not_probed"
    assert ops.handle(_apply_request(_plan(ops, "stop"), "stop"))["outcome"] == "succeeded"
    assert ops.handle(_apply_request(_plan(ops, "remove_registration"), "remove"))["outcome"] == "succeeded"
    assert adapter.definition is None
    assert Path(ops.target.env_file).exists()


def test_readiness_failure_is_partial_failure_with_confirmed_stopped_service(fixture: tuple[Ops, FakeAdapter]) -> None:
    ops, adapter = fixture
    controller = ServiceController(
        adapter,
        target=ServiceTarget(ops.target.python_executable, ops.target.package_version),
        probe=_manager_probe(adapter),
        readiness_probe=lambda _: ProbeResult(ProbeState.CONFLICT, "secret-provider-detail"),
    )
    ops = Ops(ops.target, ops.store.directory, controller=controller)
    result = ops.handle(_apply_request(_plan(ops, "register")))
    assert result["outcome"] == "partial_failure", result
    assert result["result"]["components"]["readiness"] == "failed"
    assert adapter.manager == ManagerState.INACTIVE
    assert adapter.suspended
    assert "secret-provider-detail" not in json.dumps(result)


def test_interrupted_configuration_switch_resumes_exact_revision_stopped(
    fixture: tuple[Ops, FakeAdapter], monkeypatch: pytest.MonkeyPatch
) -> None:
    ops, adapter = fixture
    assert ops.handle(_apply_request(_plan(ops, "register"), "register"))["outcome"] == "succeeded"
    original_update = adapter.update_suspended

    def interrupt_after_publication() -> None:
        original_update()
        raise KeyboardInterrupt

    monkeypatch.setattr(adapter, "update_suspended", interrupt_after_publication)
    request = _apply_request(_plan(ops, "config_apply", http_port=9999), "configuration")
    with pytest.raises(KeyboardInterrupt):
        ops.handle(request)
    monkeypatch.setattr(adapter, "update_suspended", original_update)
    status = ops.handle(_request("operation_status", operation_id="configuration"))
    assert status["outcome"] == "pending"
    assert status["observation"]["maintenance"] == "configuration_switching"
    # Even if the manager is still active during an interrupted switch,
    # recovery's disclosed effects must preserve the stopped boundary.
    adapter.manager = ManagerState.ACTIVE
    assert not _plan(ops, "config_apply", http_port=9999, restart=True)["effects"]["may_start"]
    adapter.manager = ManagerState.INACTIVE
    changed = _apply_request(_plan(ops, "config_apply", http_port=7777), "configuration")
    assert ops.handle({**changed, "action": "resume", "expected_attempt": 1})["error"]["code"] == "operation_conflict"
    resume = _apply_request(_plan(ops, "config_apply", http_port=9999), "configuration")
    resumed = ops.handle({**resume, "action": "resume", "expected_attempt": 1})
    assert resumed["outcome"] == "succeeded", resumed
    assert resumed["attempt"] == 2
    assert resumed["history"][0]["outcome"] is None
    assert adapter.definition is not None and adapter.definition.endpoint.endswith(":9999")
    assert adapter.manager == ManagerState.INACTIVE
    assert adapter.suspended


def test_cancel_after_progress_does_not_start_native_mutation(fixture: tuple[Ops, FakeAdapter]) -> None:
    ops, adapter = fixture

    def cancel(message: dict[str, Any]) -> None:
        if message["type"] == "progress":
            ops.handle(_request("cancel", operation_id="operation", attempt=1))

    result = ops.handle(_apply_request(_plan(ops, "register")), emit=cancel)
    assert result["outcome"] == "cancelled"
    assert adapter.definition is None


def test_lost_progress_before_mutation_is_durable_and_allows_new_work(fixture: tuple[Ops, FakeAdapter]) -> None:
    ops, adapter = fixture

    def disconnected(message: dict[str, Any]) -> None:
        if message["type"] == "progress":
            raise BrokenPipeError

    request = _apply_request(_plan(ops, "register"))
    result = ops.handle(request, emit=disconnected)
    assert result["outcome"] == "failed", result
    assert result["result"]["error"]["code"] == "progress_unavailable"
    assert adapter.definition is None
    assert ops.handle(_request("operation_status", operation_id="operation"))["result"] == result["result"]
    assert ops.handle(_apply_request(_plan(ops, "register"), "next"))["outcome"] == "succeeded"
    assert ops.handle(request)["result"] == result["result"]


def test_configuration_recovery_does_not_mistake_unverified_start_for_stopped_completion(
    fixture: tuple[Ops, FakeAdapter], monkeypatch: pytest.MonkeyPatch
) -> None:
    ops, adapter = fixture
    assert ops.handle(_apply_request(_plan(ops, "register"), "register"))["outcome"] == "succeeded"

    def interrupted_start(_definition) -> None:
        # Model abrupt process loss after native start, before readiness and
        # Python cleanup. This fixture does not claim real host qualification.
        adapter.resume()
        adapter.start(reload_definition=False)
        raise SystemExit

    with monkeypatch.context() as patch:
        patch.setattr(ops.controller, "_start_registered", interrupted_start)
        with pytest.raises(SystemExit):
            ops.handle(_apply_request(_plan(ops, "config_apply", http_port=9999, restart=True), "configuration"))
    assert adapter.manager == ManagerState.ACTIVE
    before = list(adapter.events)
    resume = _apply_request(_plan(ops, "config_apply", http_port=9999, restart=True), "configuration")
    rejected = ops.handle({**resume, "action": "resume", "expected_attempt": 1})
    assert rejected["error"]["code"] == "outcome_uncertain"
    assert adapter.events == before
    assert ops.handle(_request("operation_status", operation_id="configuration"))["observation"]["maintenance"] == (
        "manual_stopped"
    )
    record = ops.store.operation("configuration")
    assert record is not None and len(record["attempts"]) == 1 and record["attempts"][0]["result"] is None

    # A later explicitly requested CLI stop can establish the safe boundary;
    # a stopped service need not retain an OS-loaded registration.
    ops.controller.stop()
    assert adapter.loaded_definition is None
    before = list(adapter.events)
    resume = _apply_request(_plan(ops, "config_apply", http_port=9999, restart=False), "configuration")
    completed = ops.handle({**resume, "action": "resume", "expected_attempt": 1})
    assert completed["outcome"] == "succeeded", completed
    assert adapter.events == before
    assert adapter.manager == ManagerState.INACTIVE


def test_configuration_recovery_verifies_running_only_with_current_authorization(
    fixture: tuple[Ops, FakeAdapter], monkeypatch: pytest.MonkeyPatch
) -> None:
    ops, adapter = fixture
    assert ops.handle(_apply_request(_plan(ops, "register"), "register"))["outcome"] == "succeeded"
    configure = ops.controller.configure

    def interrupt_after_configuration(**kwargs):
        configure(**kwargs)
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(ops.controller, "configure", interrupt_after_configuration)
        with pytest.raises(KeyboardInterrupt):
            ops.handle(_apply_request(_plan(ops, "config_apply", http_port=9999, restart=True), "configuration"))
    before = list(adapter.events)
    revisions = set((ops.store.directory / "configurations").iterdir())
    probes: list[str] = []
    ready = False

    def readiness(endpoint: str) -> ProbeResult:
        probes.append(endpoint)
        return ProbeResult(ProbeState.LIVE if ready else ProbeState.CONFLICT, "readiness result")

    monkeypatch.setattr(ops.controller, "_readiness_probe", readiness)
    narrowed = _apply_request(_plan(ops, "config_apply", http_port=9999, restart=False), "configuration")
    assert ops.handle({**narrowed, "action": "resume", "expected_attempt": 1})["error"]["code"] == "outcome_uncertain"
    assert not probes
    resume = _apply_request(_plan(ops, "config_apply", http_port=9999, restart=True), "configuration")
    failed_verification = ops.handle({**resume, "action": "resume", "expected_attempt": 1})
    assert failed_verification["outcome"] == "uncertain", failed_verification
    assert failed_verification["attempt"] == 2
    ready = True
    resume = _apply_request(_plan(ops, "config_apply", http_port=9999, restart=True), "configuration")
    completed = ops.handle({**resume, "action": "resume", "expected_attempt": 2})
    assert completed["outcome"] == "succeeded", completed
    assert completed["attempt"] == 3
    assert completed["result"]["components"]["readiness"] == "verified_during_operation"
    assert probes == ["http://127.0.0.1:9999", "http://127.0.0.1:9999"]
    assert adapter.events == before
    assert set((ops.store.directory / "configurations").iterdir()) == revisions


@pytest.mark.parametrize("corruption", ["missing_fields", "native_phase", "executor"])
def test_corrupt_record_returns_stable_error_and_is_not_replaced(
    fixture: tuple[Ops, FakeAdapter], corruption: str
) -> None:
    ops, adapter = fixture
    request = _apply_request(_plan(ops, "register"))
    attempt = (
        {"attempt": 1}
        if corruption == "missing_fields"
        else {
            "attempt": 1,
            "plan": request["plan"],
            "phase": "executing",
            "result": None,
            "recovery": None,
            **({"native_command": {"phase": []}} if corruption == "native_phase" else {"executor": {"pid": True}}),
        }
    )
    ops.store.write(
        "operation.json",
        {"operation_id": "operation", "binding_id": request["plan"]["binding_id"], "attempts": [attempt]},
    )
    before = (ops.store.directory / "operation.json").read_bytes()
    assert ops.handle(request)["error"]["code"] == "record_invalid"
    assert ops.handle(_request("operation_status", operation_id="operation"))["error"]["code"] == "record_invalid"
    assert (ops.store.directory / "operation.json").read_bytes() == before
    assert adapter.definition is None


def test_new_operation_cannot_bypass_unresolved_mutation(fixture: tuple[Ops, FakeAdapter]) -> None:
    ops, adapter = fixture
    assert ops.handle(_apply_request(_plan(ops, "register"), "register"))["outcome"] == "succeeded"

    def interrupt(message: dict[str, Any]) -> None:
        if message["type"] == "progress":
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        ops.handle(_apply_request(_plan(ops, "restart"), "interrupted"), emit=interrupt)
    result = ops.handle(_apply_request(_plan(ops, "restart"), "replacement"))
    assert result["error"]["code"] == "outcome_uncertain"
    assert ops.store.operation("replacement") is None
    assert ops.handle(_apply_request(_plan(ops, "stop"), "stop"))["outcome"] == "succeeded"
    assert adapter.manager == ManagerState.INACTIVE
    resume = _apply_request(_plan(ops, "restart"), "interrupted")
    assert ops.handle({**resume, "action": "resume", "expected_attempt": 1})["error"]["code"] == "operation_superseded"


def test_stored_success_survives_current_observation_failure(
    fixture: tuple[Ops, FakeAdapter], monkeypatch: pytest.MonkeyPatch
) -> None:
    ops, _adapter = fixture
    request = _apply_request(_plan(ops, "register"))
    original = ops.handle(request)
    assert original["outcome"] == "succeeded"

    def unavailable():
        raise ServiceError("native manager unavailable")  # noqa: TRY003

    def executor_unavailable(_pid: int) -> bool:
        raise OSError("process observation unavailable")  # noqa: TRY003

    monkeypatch.setattr(ops.controller, "status", unavailable)
    monkeypatch.setattr("powercontext.ops.runtime.native_process_exited", executor_unavailable)
    observed = ops.handle(_request("operation_status", operation_id="operation"))
    assert observed["outcome"] == "succeeded"
    assert observed["result"] == original["result"]
    assert observed["observation"]["state"] == "unknown"
    assert observed["executor"]["observation"] == "unknown"
    assert observed["execution_state"] == "finished"
    assert ops.handle(request)["result"] == original["result"]


def test_surviving_native_process_blocks_even_cleanup(fixture: tuple[Ops, FakeAdapter]) -> None:
    """A real surviving process plus an interrupted checkpoint must block cleanup."""
    ops, adapter = fixture
    assert ops.handle(_apply_request(_plan(ops, "register"), "register"))["outcome"] == "succeeded"
    plan = _plan(ops, "restart")
    child = subprocess.Popen(
        [getattr(sys, "_base_executable", sys.executable), "-c", "import sys; sys.stdin.buffer.read()"],
        stdin=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        ops.store.save({
            "operation_id": "interrupted-native",
            "binding_id": plan["binding_id"],
            "attempts": [
                {
                    "attempt": 1,
                    "plan": plan,
                    "phase": "executing",
                    "result": None,
                    "recovery": None,
                    "native_command": {"phase": "running", "pid": child.pid},
                }
            ],
        })
        ops.store.write("_latest.json", {"operation_id": "interrupted-native"})
        request = _apply_request(_plan(ops, "stop"), "stop")
        assert ops.handle(request)["error"]["code"] == "operation_busy"
        assert adapter.manager == ManagerState.ACTIVE
        assert ops.store.operation("stop") is None
        child.communicate(timeout=10)
        stopped = ops.handle(request)
        assert stopped["outcome"] == "succeeded", stopped
        assert adapter.manager == ManagerState.INACTIVE
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=10)


@pytest.mark.parametrize("terminate", [False, True])
def test_status_and_cancel_do_not_wait_for_real_process_writer(
    fixture: tuple[Ops, FakeAdapter], tmp_path: Path, terminate: bool
) -> None:
    ops, _adapter = fixture
    binding = tmp_path / "private" / "target.json"
    write_protected_file(binding, canonical({"format_version": 1, **asdict(ops.target)}))
    ready = tmp_path / "ready"
    script = tmp_path / "worker.py"
    script.write_text(
        """
import json, sys
from pathlib import Path
from powercontext.ops import Ops, TargetBinding
from powercontext.service.controller import ServiceController
from powercontext.service.model import ServiceTarget
from tests.test_service import FakeAdapter, _manager_probe
binding, state, ready = map(Path, sys.argv[1:])
target = TargetBinding.read(binding)
adapter = FakeAdapter(binding.parent)
controller = ServiceController(adapter, target=ServiceTarget(target.python_executable, target.package_version), probe=_manager_probe(adapter), readiness_probe=_manager_probe(adapter))
ops = Ops(target, state, controller=controller)
def request(action, **kw):
    return {"protocol_version":1,"request_id":"child","action":action,**kw}
plan = ops.handle(request("preflight",operation="register",options={}))['plan']
def emit(message):
    if message['type'] == 'accepted':
        ready.write_text('ready')
        if sys.stdin.readline() != 'release\\n':
            raise SystemExit('supervisor disconnected before release')
result = ops.handle(request("apply",operation_id="child-operation",plan=plan,authorization={"plan_id":plan['plan_id'],"approved":True}),emit=emit)
print(json.dumps(result), flush=True)
""",
        encoding="utf-8",
    )
    # Windows virtualenv python.exe may only be a redirect launcher. Start the
    # real interpreter with the current environment's packages so this handle
    # owns the worker whose exit releases both locks.
    bootstrap = """import runpy, site, sys
packages, repository, script, *arguments = sys.argv[1:]
sys.path.insert(0, packages)
site.addsitedir(packages)
sys.path.insert(0, repository)
sys.argv = [script, *arguments]
runpy.run_path(script, run_name='__main__')
"""
    output_path, error_path = tmp_path / "worker.stdout", tmp_path / "worker.stderr"
    with output_path.open("w", encoding="utf-8") as output_file, error_path.open("w", encoding="utf-8") as error_file:
        child = subprocess.Popen(
            [
                getattr(sys, "_base_executable", sys.executable),
                "-I",
                "-c",
                bootstrap,
                sysconfig.get_path("purelib"),
                str(Path(__file__).resolve().parents[1]),
                str(script),
                str(binding),
                str(ops.store.directory),
                str(ready),
            ],
            stdin=subprocess.PIPE,
            stdout=output_file,
            stderr=error_file,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        try:
            # Imports and private Windows ACL setup are fixture preparation,
            # not a product response-time guarantee, including under xdist.
            deadline = time.monotonic() + 180
            while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            if not ready.exists():
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=30)
                pytest.fail(
                    f"Ops worker did not accept the request within its startup budget; exit={child.returncode}. "
                    f"stdout={output_path.read_text(encoding='utf-8')} "
                    f"stderr={error_path.read_text(encoding='utf-8')}"
                )
            before = (ops.store.directory / "child-operation.json").read_bytes()
            status = ops.handle(_request("operation_status", operation_id="child-operation"))
            assert status["outcome"] == "pending"
            assert status["executor"] == {"pid": child.pid, "observation": "not_confirmed_exited"}
            assert status["execution_state"] == "unknown"
            child_record = ops.store.operation("child-operation")
            assert child_record is not None
            duplicate_plan = child_record["attempts"][0]["plan"]
            duplicate = ops.handle(_apply_request(duplicate_plan, "child-operation"))
            assert duplicate["attempt"] == 1
            busy = ops.handle(_apply_request(_plan(ops, "register"), "other"))
            assert busy["error"]["code"] == "operation_busy"
            cancelled = ops.handle(_request("cancel", operation_id="child-operation", attempt=1))
            assert cancelled["cancel_requested"]
            assert (ops.store.directory / "child-operation.json").read_bytes() == before
            if terminate:
                child.kill()
                child.wait(timeout=30)
                interrupted = ops.handle(_request("operation_status", operation_id="child-operation"))
                assert interrupted["outcome"] == "pending"
                assert interrupted["result"] is None
                assert interrupted["phase"] == "accepted"
                assert interrupted["executor"] == {"pid": child.pid, "observation": "exited"}
                assert interrupted["execution_state"] == "interrupted"
                assert interrupted["cancel_requested"]
                assert (ops.store.directory / "child-operation.json").read_bytes() == before
            else:
                child.communicate(input="release\n", timeout=120)
                assert child.returncode == 0, error_path.read_text(encoding="utf-8")
                completed = json.loads(output_path.read_text(encoding="utf-8"))
                assert completed["outcome"] == "cancelled"
                assert completed["execution_state"] == "finished"
        finally:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=30)
            if child.stdin is not None:
                child.stdin.close()


def test_checkpoint_without_executor_is_observed_as_unknown(fixture: tuple[Ops, FakeAdapter]) -> None:
    ops, _adapter = fixture

    def interrupt(message: dict[str, Any]) -> None:
        if message["type"] == "accepted":
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        ops.handle(_apply_request(_plan(ops, "register")), emit=interrupt)
    record = ops.store.operation("operation")
    assert record is not None
    del record["attempts"][0]["executor"]
    ops.store.save(record)
    before = (ops.store.directory / "operation.json").read_bytes()
    status = ops.handle(_request("operation_status", operation_id="operation"))
    assert status["executor"] == {"pid": None, "observation": "unknown"}
    assert status["execution_state"] == "unknown"
    assert status["phase"] == "accepted"
    assert status["result"] is None
    assert (ops.store.directory / "operation.json").read_bytes() == before


@pytest.mark.parametrize("operation", ["register", "config_apply"])
def test_recovery_accepts_posix_environment_identity_serialization(
    fixture: tuple[Ops, FakeAdapter], monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    """Exercise POSIX's omitted owner_sid shape, not native POSIX service behavior."""
    ops, _adapter = fixture
    if operation == "config_apply":
        assert ops.handle(_apply_request(_plan(ops, "register"), "register"))["outcome"] == "succeeded"
    snapshot = ops.controller.operation_snapshot
    read_environment = ops_runtime.load_protected_environment_file
    read_file = ops_runtime.read_protected_file

    def posix_shapes(value):
        if isinstance(value, dict):
            return {key: posix_shapes(item) for key, item in value.items() if key != "owner_sid"}
        return value

    def posix_environment(path):
        loaded = read_environment(path)
        return replace(loaded, identity=replace(loaded.identity, owner_sid=None))

    def posix_file(path):
        loaded = read_file(path)
        return replace(loaded, identity=replace(loaded.identity, owner_sid=None))

    monkeypatch.setattr(ops.controller, "operation_snapshot", lambda: posix_shapes(snapshot()))
    monkeypatch.setattr(ops_runtime, "load_protected_environment_file", posix_environment)
    monkeypatch.setattr(ops_runtime, "read_protected_file", posix_file)
    method = "install" if operation == "register" else "configure"
    mutate = getattr(ops.controller, method)

    def interrupt_after_mutation(**kwargs):
        mutate(**kwargs)
        raise KeyboardInterrupt

    options = {} if operation == "register" else {"http_port": 9999, "restart": True}
    with monkeypatch.context() as patch:
        patch.setattr(ops.controller, method, interrupt_after_mutation)
        with pytest.raises(KeyboardInterrupt):
            ops.handle(_apply_request(_plan(ops, operation, **options), "interrupted"))
    resume = _apply_request(_plan(ops, operation, **options), "interrupted")
    result = ops.handle({**resume, "action": "resume", "expected_attempt": 1})
    assert result["outcome"] == "succeeded", result


@pytest.mark.parametrize("error", [subprocess.TimeoutExpired("native-command", 1), TypeError("malformed observation")])
def test_accepted_mutation_failure_is_durable_uncertain(
    fixture: tuple[Ops, FakeAdapter], monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    ops, _adapter = fixture
    install = ops.controller.install

    def fail_after_mutation(**kwargs):
        install(**kwargs)
        raise error

    monkeypatch.setattr(ops.controller, "install", fail_after_mutation)
    result = ops.handle(_apply_request(_plan(ops, "register")))
    assert result["outcome"] == "uncertain", result
    assert result["result"]["error"]["code"] == "outcome_uncertain"
    assert ops.handle(_request("operation_status", operation_id="operation"))["result"] == result["result"]


@pytest.mark.parametrize("superseded", ["unchanged", "new_operation", "new_attempt"])
def test_record_only_acceptance_resumes_only_while_predecessor_is_unchanged(
    fixture: tuple[Ops, FakeAdapter], monkeypatch: pytest.MonkeyPatch, superseded: str
) -> None:
    ops, adapter = fixture
    assert ops.handle(_apply_request(_plan(ops, "register"), "register"))["outcome"] == "succeeded"
    write = ops.store.write

    if superseded == "new_attempt":

        def cancel_predecessor(message: dict[str, Any]) -> None:
            if message["type"] == "accepted":
                ops.store.cancel("predecessor", 1)

        cancelled = ops.handle(_apply_request(_plan(ops, "stop"), "predecessor"), emit=cancel_predecessor)
        assert cancelled["outcome"] == "cancelled"

    def fail_coordination(name: str, value: dict[str, Any]) -> None:
        if name == "_latest.json":
            raise OSError("coordination write interrupted")  # noqa: TRY003
        write(name, value)

    with monkeypatch.context() as patch:
        patch.setattr(ops.store, "write", fail_coordination)
        result = ops.handle(_apply_request(_plan(ops, "stop"), "record-only"))
    assert result["outcome"] == "uncertain"
    assert adapter.manager == ManagerState.ACTIVE
    if superseded == "new_operation":
        assert ops.handle(_apply_request(_plan(ops, "stop"), "later-stop"))["outcome"] == "succeeded"
    elif superseded == "new_attempt":
        predecessor = _apply_request(_plan(ops, "stop"), "predecessor")
        advanced = ops.handle({**predecessor, "action": "resume", "expected_attempt": 1})
        assert advanced["outcome"] == "succeeded" and advanced["attempt"] == 2
    before = list(adapter.events)
    resume = _apply_request(_plan(ops, "stop"), "record-only")
    result = ops.handle({**resume, "action": "resume", "expected_attempt": 1})
    if superseded != "unchanged":
        assert result["error"]["code"] == "operation_superseded"
        assert adapter.events == before
    else:
        assert result["outcome"] == "succeeded", result
        assert result["attempt"] == 2
        assert adapter.manager == ManagerState.INACTIVE


@pytest.mark.parametrize("stage", ["initial", "accepted_response"])
def test_checkpoint_failure_reports_whether_acceptance_was_durable(
    fixture: tuple[Ops, FakeAdapter], monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    ops, adapter = fixture
    save = ops.store.save

    def fail_before_publication(record) -> None:
        if stage == "initial" or record["attempts"][-1]["result"] is not None:
            raise OSError("checkpoint unavailable")  # noqa: TRY003
        save(record)

    def disconnected(_message) -> None:
        raise BrokenPipeError

    monkeypatch.setattr(ops.store, "save", fail_before_publication)
    result = ops.handle(_apply_request(_plan(ops, "register")), emit=disconnected)
    if stage == "initial":
        assert result["outcome"] == "failed", result
        assert result["recovery_actions"] == ["preflight"]
        assert ops.store.operation("operation") is None
    else:
        assert result["outcome"] == "uncertain", result
        assert result["recovery_actions"] == ["operation_status"]
        assert result["result"]["error"]["code"] == "checkpoint_failed"
        status = ops.handle(_request("operation_status", operation_id="operation"))
        assert status["outcome"] == "pending" and status["result"] is None
    assert adapter.definition is None


@pytest.mark.parametrize("restart", [False, True])
def test_cancel_during_configuration_switch_only_cancels_unfinished_start(
    fixture: tuple[Ops, FakeAdapter], monkeypatch: pytest.MonkeyPatch, restart: bool
) -> None:
    ops, adapter = fixture
    assert ops.handle(_apply_request(_plan(ops, "register"), "register"))["outcome"] == "succeeded"
    update = adapter.update_suspended

    def cancel_after_switch() -> None:
        update()
        ops.store.cancel("configuration", 1)

    monkeypatch.setattr(adapter, "update_suspended", cancel_after_switch)
    result = ops.handle(_apply_request(_plan(ops, "config_apply", http_port=9999, restart=restart), "configuration"))
    assert result["outcome"] == ("cancelled" if restart else "succeeded"), result
    assert adapter.manager == ManagerState.INACTIVE
    assert adapter.definition is not None and adapter.definition.endpoint.endswith(":9999")


def test_confirmed_native_spawn_failure_does_not_block_explicit_cleanup(fixture: tuple[Ops, FakeAdapter]) -> None:
    ops, adapter = fixture
    assert ops.handle(_apply_request(_plan(ops, "register"), "register"))["outcome"] == "succeeded"

    def interrupt(message: dict[str, Any]) -> None:
        if message["type"] == "progress":
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        ops.handle(_apply_request(_plan(ops, "restart"), "spawn-failed"), emit=interrupt)
    record = ops.store.operation("spawn-failed")
    assert record is not None
    record["attempts"][-1]["native_command"] = {"phase": "spawn_failed"}
    ops.store.save(record)
    result = ops.handle(_apply_request(_plan(ops, "stop"), "stop"))
    assert result["outcome"] == "succeeded", result
    assert adapter.manager == ManagerState.INACTIVE
