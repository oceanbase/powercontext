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
from tests.e2e.zcode_acceptance.runner import AcceptanceRun, serve


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


@pytest.mark.parametrize(
    ("native_assertion", "result_emitted", "truncated"),
    [
        ("libuv_async_handle_closing", False, True),
        ("libuv_async_handle_closing", True, False),
        ("libuv_fs_event_path_mismatch", False, False),
        (None, True, False),
    ],
)
def test_nonzero_cli_exit_stays_failed_and_retains_only_safe_diagnostics(
    tmp_path, native_assertion, result_emitted, truncated
):
    cli = tmp_path / "host.py"
    diagnostic = "private-provider-token private-profile-path\n"
    if native_assertion == "libuv_async_handle_closing":
        diagnostic += "Assertion failed: !(handle->flags & UV_HANDLE_CLOSING), file src\\win\\async.c, line 76\n"
    elif native_assertion == "libuv_fs_event_path_mismatch":
        diagnostic += "Assertion failed: !_wcsnicmp(filename, dir, dirlen), file src\\win\\fs-event.c, line 72\n"
    cli.write_text(
        "import json, sys\n"
        f"sys.stderr.write({diagnostic!r})\n"
        + (
            "print(json.dumps({'type': 'result', 'sessionId': 'private-session', 'response': 'private-response'}))\n"
            if result_emitted
            else ""
        )
        + ("print('{\"type\":')\n" if truncated else "")
        + "sys.exit(7)\n",
        encoding="utf-8",
    )
    run = AcceptanceRun(tmp_path / "runs", live=False, model_config=None, generation_env=None)
    run.workspace.mkdir(parents=True)
    run.node = getattr(sys, "_base_executable", sys.executable)
    run.cli = cli
    run.model = None

    with pytest.raises(AssertionError, match=r"^host_process_exit_7$"):
        run.invoke("private-prompt")

    assert run.summary["host_process_exit"] == {
        "returncode": 7,
        "result_event_count": int(result_emitted),
        "native_assertion": native_assertion,
    }
    assert "private" not in json.dumps(run.summary)


@pytest.mark.parametrize("exit_code", [0, 7])
def test_fixture_prerequisite_failure_survives_cli_exit(tmp_path, exit_code):
    run = AcceptanceRun(tmp_path / "runs", live=False, model_config=None, generation_env=None)
    run.workspace.mkdir(parents=True)
    run.node = getattr(sys, "_base_executable", sys.executable)
    run.cli = tmp_path / "host.py"
    assert run.model is not None

    def missing_result():
        raise AssertionError("receiver_action_result_missing")

    with serve(run.model.app) as endpoint:
        run.cli.write_text(
            "import json, sys, urllib.error, urllib.request\n"
            "body = {'model': 'fixture', 'messages': [], "
            "'tools': [{'type': 'function', 'function': {'name': 'Bash'}}]}\n"
            f"request = urllib.request.Request({endpoint + '/v1/chat/completions'!r}, "
            "data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})\n"
            "try:\n"
            "    urllib.request.urlopen(request, timeout=5)\n"
            "except urllib.error.HTTPError as error:\n"
            "    assert error.code == 400\n"
            "print(json.dumps({'type': 'result', 'sessionId': 'fixture-session'}))\n"
            f"sys.exit({exit_code})\n",
            encoding="utf-8",
        )
        with pytest.raises(AssertionError, match=r"^receiver_action_result_missing$"), run.scenario(1):
            run.invoke("Verify the fixture.", actions=[("Bash", missing_result)])

    assert run.summary["scenarios"][0]["status"] == "failed"
    assert run.summary["scenarios"][0]["stable_error_code"] == "receiver_action_result_missing"
