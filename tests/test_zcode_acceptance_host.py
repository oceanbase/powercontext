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

"""Real subprocess regressions for safe acceptance-host shutdown evidence."""

import json
import os
import sys
import time
from types import SimpleNamespace

import pytest

from tests.e2e.zcode_acceptance.host import NativeHost


def make_host(tmp_path, source: str) -> NativeHost:
    cli = tmp_path / "host.py"
    cli.write_text(source, encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return NativeHost(
        SimpleNamespace(
            root=tmp_path,
            # Windows venv redirectors retain pipe handles while their CPython child runs.
            node=getattr(sys, "_base_executable", sys.executable),
            cli=cli,
            workspace=workspace,
            environment=dict(os.environ),
            budget=time.monotonic() + 30,
        )
    )


def test_stdout_eof_does_not_imply_process_exit_and_retains_only_safe_diagnostics(tmp_path):
    host = make_host(
        tmp_path,
        """import json, os, sys
sys.stdin.readline()
diagnostic = {
    'version': 1, 'kind': 'unhandledRejection', 'origin': 'unhandledRejection',
    'name': 'private-error-name', 'message': 'private-provider-token',
    'stack': 'private-profile-path', 'errorId': 'private-id', 'occurredAt': 42,
}
sys.stderr.write('[zcode-process-exception] ' + json.dumps(diagnostic) + '\\n')
sys.stderr.flush()
os.close(1)
sys.stdin.read()
""",
    )
    try:
        with pytest.raises(AssertionError, match=r"^host_rpc_process_exited$"):
            host.request("session/events", {"sessionId": "private-session"}, timeout=5)
        assert host.process.poll() is None
        assert host.process.stdin is not None and not host.process.stdin.closed
    finally:
        host.close()
    files = list((tmp_path / "evidence").glob("host-rpc-*.json"))
    assert len(files) == 1
    serialized = files[0].read_text(encoding="utf-8")
    evidence = json.loads(serialized)
    assert evidence["unexpected_stdout_eof"] is True
    assert evidence["returncode_at_stdout_eof"] is None
    assert evidence["returncode_before_cleanup"] is None
    assert evidence["returncode_final"] == 0
    assert evidence["client_terminated"] is False
    assert evidence["last_rpc_method"] == "session/events" and evidence["last_rpc_id"] == 1
    assert evidence["stdout_reader_finished"] is True and evidence["stderr_reader_finished"] is True
    assert evidence["process_exception"] == {"kind": "unhandledRejection", "origin": "unhandledRejection"}
    assert "private" not in serialized


def test_nonzero_exit_records_unknown_method_without_unsafe_or_oversized_stderr(tmp_path):
    host = make_host(
        tmp_path,
        """import json, sys
request = json.loads(sys.stdin.readline())
print(json.dumps({'id': request['id'], 'result': {}}), flush=True)
sys.stdin.read()
sys.stderr.write('[zcode-process-exception] ' + json.dumps({
    'version': 1, 'kind': 'uncaughtException', 'origin': 'uncaughtException',
    'message': 'private-' + 'x' * (128 * 1024),
}) + '\\n')
sys.stderr.write('[zcode-process-exception] ' + json.dumps({
    'version': 1, 'kind': 'private-kind', 'origin': 'private-origin',
}) + '\\n')
sys.stderr.write('private-unstructured-stderr\\n')
sys.exit(7)
""",
    )
    try:
        assert host.request("private-method", {}) == {}
    finally:
        host.close()
    files = list((tmp_path / "evidence").glob("host-rpc-*.json"))
    assert len(files) == 1
    serialized = files[0].read_text(encoding="utf-8")
    evidence = json.loads(serialized)
    assert evidence["unexpected_stdout_eof"] is False
    assert evidence["returncode_final"] == 7
    assert evidence["client_terminated"] is False
    assert evidence["last_rpc_method"] == "other" and evidence["last_rpc_id"] == 1
    assert evidence["process_exception"] is None
    assert evidence["stderr_reader_finished"] is True
    assert "private" not in serialized


def test_normal_shutdown_does_not_emit_host_failure_evidence(tmp_path):
    host = make_host(tmp_path, "import sys; sys.stdin.read()\n")
    host.close()
    assert host.process.returncode == 0
    assert not (tmp_path / "evidence").exists()
