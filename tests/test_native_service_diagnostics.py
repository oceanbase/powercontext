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
import socket
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock

import pytest

from powercontext.service.adapters.launchd import LaunchdUserAdapter
from powercontext.service.model import ProbeState, ServiceError, SupportState
from powercontext.service.probe import probe_server
from tests.native import test_personal_service_lifecycle as lifecycle


def test_native_startup_failure_preserves_diagnostics_after_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = LaunchdUserAdapter(home=tmp_path, identifier="com.oceanbase.powercontext.native.fixture")
    monkeypatch.setattr(adapter, "support", lambda: (SupportState.SUPPORTED, "fixture"))
    monkeypatch.setattr(lifecycle, "_native_adapter", lambda: adapter)
    monkeypatch.setattr(lifecycle, "_environment_file", lambda path: path / "fixture.env")
    controller = Mock()
    controller.install.side_effect = ServiceError("service did not become live")
    monkeypatch.setattr(lifecycle, "ServiceController", lambda adapter: controller)
    logs = tmp_path / "data" / "logs"
    logs.mkdir(parents=True)
    retry_state = logs / "launchd-retry-state.json"
    retry_state.write_text('{"attempts":[1]}', encoding="utf-8")
    token = logs / "launchd-retry.enabled"
    token.write_text("enabled", encoding="utf-8")

    def run_command(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[0] == "/usr/bin/sample":
            Path(command[-1]).write_text("Service process stack sample", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "state = running\npid = 123", "")

    monkeypatch.setattr(lifecycle.subprocess, "run", run_command)

    def cleanup(adapter: LaunchdUserAdapter) -> None:
        retry_state.unlink()
        token.unlink()

    monkeypatch.setattr(lifecycle, "_cleanup", cleanup)
    with pytest.raises(pytest.fail.Exception, match="service did not become live"):
        lifecycle.test_native_personal_service_lifecycle(tmp_path)

    assert not retry_state.exists() and not token.exists()
    snapshot = json.loads((logs / "launchd-failure-snapshot.json").read_text(encoding="utf-8"))
    assert snapshot == {"retry_token_present": True, "retry_state": '{"attempts":[1]}'}
    assert "state = running\npid = 123" in (logs / "launchd-before-cleanup.log").read_text(encoding="utf-8")
    assert (logs / "launchd-process-sample.log").read_text(encoding="utf-8") == "Service process stack sample"


@pytest.mark.parametrize("startup_raises", [False, True], ids=["normal-exit", "failed-start"])
def test_native_startup_fault_drains_probes_and_releases_port(
    monkeypatch: pytest.MonkeyPatch, startup_raises: bool
) -> None:
    port = lifecycle._unused_loopback_port()
    address = ("127.0.0.1", port)
    adapter = Mock()

    def start(*, reload_definition: bool) -> None:
        # The native manager boundary is modeled; port ownership and probes are real.
        with socket.socket() as contender:
            with pytest.raises(OSError) as conflict:
                contender.bind(address)
            assert conflict.value.errno == errno.EADDRINUSE
        if startup_raises:
            raise ServiceError("fixture start failed")  # noqa: TRY003

    adapter.start.side_effect = start
    expected = pytest.raises(ServiceError, match="fixture start failed") if startup_raises else nullcontext()
    with expected, lifecycle._native_upgrade_fault(adapter, port, "startup", monkeypatch):
        adapter.start(reload_definition=False)
        # A controller and retried native launches can probe more often than
        # the listener backlog; every probe must still see a port conflict.
        for _ in range(40):
            assert probe_server(f"http://127.0.0.1:{port}").state is ProbeState.CONFLICT
        with socket.create_connection(address, timeout=2):
            pass

    # Exiting the fault, including a failed adapter.start(), releases the endpoint
    # so the subsequent native recovery can bind it normally.
    with socket.socket() as recovered:
        if sys.platform != "win32":
            recovered.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        recovered.bind(address)
        recovered.listen()
