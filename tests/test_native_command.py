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
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from powercontext.service.adapters.systemd import SystemdUserAdapter
from powercontext.service.controller import ServiceController
from powercontext.service.environment import ProtectedEnvironmentFileError, write_protected_file
from powercontext.service.model import ManagerOwnershipState, ManagerRegistration, ManagerState, ServiceError
from powercontext.service.native_command import native_process_exited, observe_native_commands, run_native_command
from tests.test_service import FakeAdapter


@pytest.mark.parametrize("operation", ["reload", "enable", "disable", "start", "restart", "stop"])
def test_systemd_mutations_record_native_process_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
):
    adapter = SystemdUserAdapter(config_home=tmp_path)
    controller = ServiceController(FakeAdapter(tmp_path))
    monkeypatch.setattr(adapter, "loaded_registration", lambda: ManagerRegistration(ManagerOwnershipState.OWNED))
    monkeypatch.setattr(adapter, "manager_state", lambda: ManagerState.ACTIVE)
    # The native manager is simulated, but its mutation runs in a real child.
    popen = subprocess.Popen

    def native_child(command, *args, **kwargs):
        if command[:2] == ["systemctl", "--user"]:
            command = [sys.executable, "-c", "pass"]
        return popen(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", native_child)
    events = []

    def observe(event):
        assert controller.operation_snapshot()["native_command"] == {"format_version": 1, **event}
        events.append(event)

    with controller.mutation(), observe_native_commands(observe):
        if operation in {"start", "restart"}:
            adapter.start(reload_definition=operation == "restart")
        else:
            getattr(adapter, operation)()
    assert [event["phase"] for event in events] == ["dispatching", "running", "completed"]
    assert native_process_exited(events[1]["pid"])


def test_observed_native_process_output_and_confirmed_exit() -> None:
    events: list[dict[str, object]] = []
    with observe_native_commands(events.append):
        result = run_native_command([sys.executable, "-c", "print('ready')"], timeout=10, text=True)
    assert result.stdout == "ready\n"
    assert result.returncode == 0
    assert events[0] == {"phase": "dispatching"}
    assert events[-1] == {**events[1], "phase": "completed"}
    assert native_process_exited(int(str(events[1]["pid"])))


def test_failed_pid_checkpoint_reaps_the_spawned_native_child() -> None:
    events: list[dict[str, object]] = []

    def record(event: dict[str, object]) -> None:
        events.append(event)
        if event["phase"] == "running":
            raise OSError("checkpoint write failed")  # noqa: TRY003

    with observe_native_commands(record), pytest.raises(OSError, match="checkpoint write failed"):
        run_native_command([sys.executable, "-c", "import time; time.sleep(60)"], timeout=10)
    assert events[-1]["phase"] == "completed"
    assert native_process_exited(int(str(events[1]["pid"])))


def test_timeout_reaps_child_before_recording_completion() -> None:
    events: list[dict[str, object]] = []
    with observe_native_commands(events.append), pytest.raises(subprocess.TimeoutExpired):
        run_native_command([sys.executable, "-c", "import time; time.sleep(60)"], timeout=0.1)
    assert events[-1]["phase"] == "completed"
    assert native_process_exited(int(str(events[1]["pid"])))


@pytest.mark.parametrize("phase", ["dispatching", "completed"])
def test_checkpoint_protection_failure_is_a_service_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    controller = ServiceController(FakeAdapter(tmp_path))
    marker = tmp_path / "executed"

    def fail_checkpoint(path: Path, content: bytes):
        if path == controller.native_command_path and json.loads(content)["phase"] == phase:
            raise ProtectedEnvironmentFileError("isolated protection failure")  # noqa: TRY003
        return write_protected_file(path, content)

    monkeypatch.setattr("powercontext.service.environment.write_protected_file", fail_checkpoint)
    with pytest.raises(ServiceError, match="native command evidence could not be recorded"), controller.mutation():
        run_native_command(
            [sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).touch()", str(marker)],
            timeout=10,
        )
    assert marker.exists() is (phase == "completed")
    # A missing final checkpoint retains process evidence, but a positively
    # exited child must not prevent the next explicit operation.
    with controller.mutation():
        pass


@pytest.mark.parametrize("missing", [True, False])
def test_failed_native_launch_does_not_block_subsequent_service_work(tmp_path: Path, missing: bool) -> None:
    controller = ServiceController(FakeAdapter(tmp_path))
    command = tmp_path / "missing-command" if missing else tmp_path
    with controller.mutation(), pytest.raises(OSError):
        run_native_command([str(command)], timeout=10)
    assert controller.operation_snapshot()["native_command"] == {"format_version": 1, "phase": "spawn_failed"}
    with controller.mutation():
        result = run_native_command([sys.executable, "-c", "print('recovered')"], timeout=10, text=True)
    assert result.stdout == "recovered\n"


@pytest.mark.parametrize("error", [OSError(errno.EIO, "unknown spawn state"), KeyboardInterrupt()])
def test_uncertain_native_launch_still_blocks_service_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    controller = ServiceController(FakeAdapter(tmp_path))
    original = subprocess.Popen

    def fail(command: Any, *args: Any, **kwargs: Any) -> Any:
        if command == ["native-command"]:
            raise error
        return original(command, *args, **kwargs)

    with monkeypatch.context() as fault:
        fault.setattr(subprocess, "Popen", fail)
        with controller.mutation(), pytest.raises(type(error)):
            run_native_command(["native-command"], timeout=10)
    assert controller.operation_snapshot()["native_command"] == {"format_version": 1, "phase": "dispatching"}
    with pytest.raises(ServiceError, match="not been confirmed"), controller.mutation():
        pytest.fail("uncertain launch must block new service work")


def test_all_service_callers_reject_a_live_native_command_and_accept_confirmed_exit(tmp_path: Path) -> None:
    adapter = FakeAdapter(tmp_path)
    first = ServiceController(adapter)
    second = ServiceController(adapter)
    child = subprocess.Popen([getattr(sys, "_base_executable", sys.executable), "-c", "import time; time.sleep(60)"])
    try:
        write_protected_file(
            first.native_command_path,
            json.dumps({"format_version": 1, "phase": "running", "pid": child.pid}).encode(),
        )
        for controller in (first, second):
            with pytest.raises(ServiceError, match="not been confirmed"):
                controller.start()
        child.kill()
        child.wait(timeout=10)
        # The existing service lock permits work after positive process-exit
        # evidence, without deleting or guessing at the persisted checkpoint.
        with second.mutation():
            assert second.operation_snapshot()["native_command"] is not None
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)


def test_service_mutation_refuses_an_unrecorded_native_pid(tmp_path: Path) -> None:
    controller = ServiceController(FakeAdapter(tmp_path))
    write_protected_file(controller.native_command_path, b'{"format_version":1,"phase":"dispatching"}')
    with pytest.raises(ServiceError, match="not been confirmed"):
        controller.uninstall()
    assert controller.operation_snapshot()["native_command"] == {"format_version": 1, "phase": "dispatching"}


def test_service_evidence_is_durable_before_ops_observes_the_command(tmp_path: Path) -> None:
    controller = ServiceController(FakeAdapter(tmp_path))
    observations = []

    def project(event: dict[str, object]) -> None:
        evidence = controller.operation_snapshot()["native_command"]
        assert evidence == {"format_version": 1, **event}
        observations.append(event)

    with controller.mutation(), observe_native_commands(project):
        run_native_command([getattr(sys, "_base_executable", sys.executable), "-c", "pass"], timeout=10)
    assert observations[-1]["phase"] == "completed"


@pytest.mark.parametrize("lose_pid_checkpoint", [False, True])
@pytest.mark.parametrize("entrypoint", ["runner", "systemd"])
def test_parent_death_does_not_prove_native_child_exit(
    tmp_path: Path, lose_pid_checkpoint: bool, entrypoint: str
) -> None:
    record = tmp_path / "native.json"
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    script = """import json, os, sys, time
from pathlib import Path
from powercontext.service.native_command import observe_native_commands, run_native_command
from powercontext.service.adapters.systemd import SystemdUserAdapter
import subprocess
record, ready, release = map(Path, sys.argv[1:4])
def observe(event):
    if sys.argv[4] == 'lost' and event['phase'] == 'running':
        os._exit(7)
    record.write_text(json.dumps(event))
    if event['phase'] == 'running':
        deadline = time.monotonic() + 15
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        os._exit(7)
child = '''import os, sys, time
from pathlib import Path
Path(sys.argv[1]).write_text(str(os.getpid()))
deadline = time.monotonic() + 30
while not Path(sys.argv[2]).exists() and time.monotonic() < deadline:
    time.sleep(0.05)
'''
command = [sys.argv[5], '-c', child, str(ready), str(release)]
if sys.argv[6] == 'systemd':
    popen = subprocess.Popen
    def systemctl(arguments, *args, **kwargs):
        if arguments == ['systemctl', '--user', 'daemon-reload']:
            arguments = command
        return popen(arguments, *args, **kwargs)
    subprocess.Popen = systemctl
with observe_native_commands(observe):
    if sys.argv[6] == 'systemd':
        SystemdUserAdapter(config_home=record.parent).reload()
    else:
        run_native_command(command, timeout=40)
"""
    parent = subprocess.Popen(
        [
            sys.executable,
            "-c",
            script,
            str(record),
            str(ready),
            str(release),
            "lost" if lose_pid_checkpoint else "saved",
            getattr(sys, "_base_executable", sys.executable),
            entrypoint,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    child_pid = None
    try:
        deadline = time.monotonic() + 60
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists()
        child_pid = int(ready.read_text())
        parent.wait(timeout=10)
        assert parent.returncode == 7
        assert not native_process_exited(child_pid)
        observed = json.loads(record.read_text())
        if lose_pid_checkpoint:
            assert observed == {"phase": "dispatching"}
        else:
            assert observed == {"phase": "running", "pid": child_pid}
        release.touch()
        deadline = time.monotonic() + 10
        while not native_process_exited(child_pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        if os.name != "nt" and not native_process_exited(child_pid):
            pytest.skip("host init has not reaped the orphan; conservative PID observation remains unknown")
        assert native_process_exited(child_pid)
    finally:
        release.touch()
        if parent.poll() is None:
            parent.kill()
        parent.communicate(timeout=10)
