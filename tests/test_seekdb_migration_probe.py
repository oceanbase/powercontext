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

"""A skipped test or retained engine must not certify native recovery."""

import json
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from scripts import probe_seekdb_migration_lock as probe
from scripts.probe_seekdb_migration_lock import restart_evidence, successful_report
from tests.seekdb_support import RestartingInstance


@pytest.mark.parametrize("outcome", ["", "<skipped/>", "<failure/>", "<error/>"])
def test_native_acceptance_requires_one_passing_case(tmp_path: Path, outcome: str) -> None:
    report = tmp_path / "junit.xml"
    report.write_text(f"<testsuite><testcase>{outcome}</testcase></testsuite>")
    assert successful_report(report, 0) is (outcome == "")
    assert not successful_report(report, 1)
    report.write_text("<testsuite/>")
    assert not successful_report(report, 0)


@pytest.mark.parametrize("pids, accepted", [([], False), ([101], False), ([101, 101], False), ([101, 102], True)])
def test_restart_acceptance_rejects_an_engine_kept_alive(tmp_path: Path, pids: list[int], accepted: bool) -> None:
    events = [
        {"event": event, "path": "/synthetic/seekdb", "native_pid": pid}
        for pid in pids
        for event in ("opened", "native_exited")
    ]
    (tmp_path / "lifecycle.jsonl").write_text("\n".join(json.dumps(event) for event in events))
    assert restart_evidence(tmp_path)["passed"] is accepted


@pytest.mark.parametrize("exit_before_reopen", [False, True])
def test_different_pids_cannot_certify_restart_without_confirmed_exit(tmp_path: Path, exit_before_reopen: bool) -> None:
    events = [{"event": "opened", "path": "/synthetic/seekdb", "native_pid": 101}]
    exited = {"event": "native_exited", "path": "/synthetic/seekdb", "native_pid": 101}
    if exit_before_reopen:
        events.append(exited)
    events.append({"event": "opened", "path": "/synthetic/seekdb", "native_pid": 102})
    if not exit_before_reopen:
        events.append(exited)
        events.append({"event": "native_exited", "path": "/synthetic/seekdb", "native_pid": 102})
    (tmp_path / "lifecycle.jsonl").write_text("\n".join(json.dumps(event) for event in events))
    assert not restart_evidence(tmp_path)["passed"]


def test_probe_rejects_a_binding_without_a_module_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    binding = ModuleType("pylibseekdb")
    binding.__file__ = None
    monkeypatch.setattr(probe, "import_module", lambda _name: binding)
    monkeypatch.setattr(sys, "argv", ["probe", "--output", str(tmp_path / "probe")])
    with pytest.raises(RuntimeError, match="no module file"):
        probe.main()


@pytest.fixture
def owned_process() -> Iterator[subprocess.Popen[bytes]]:
    with subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read()"], stdin=subprocess.PIPE
    ) as process:
        try:
            yield process
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)


@pytest.mark.skipif(sys.platform != "linux", reason="native restart acceptance uses Linux pidfd")
def test_last_client_close_waits_for_process_exit(owned_process: subprocess.Popen[bytes]) -> None:
    assert owned_process.stdin is not None
    instance = RestartingInstance(SimpleNamespace(close=owned_process.stdin.close), owned_process.pid)
    instance.close()
    assert owned_process.poll() == 0
    instance.close()


@pytest.mark.skipif(sys.platform != "linux", reason="native restart acceptance uses Linux pidfd")
def test_last_client_close_rejects_a_process_that_stays_alive(owned_process: subprocess.Popen[bytes]) -> None:
    instance = RestartingInstance(SimpleNamespace(close=lambda: None), owned_process.pid, timeout=0)
    with pytest.raises(TimeoutError, match="did not exit"):
        instance.close()
    assert owned_process.poll() is None
