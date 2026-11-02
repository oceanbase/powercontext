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

"""Compose initialization must finish before a managed remote Server starts."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="The Compose launcher requires a POSIX shell")


def _run(tmp_path, monkeypatch, *, vector=False, code=False, failed_action=""):
    commands = tmp_path / "bin"
    commands.mkdir()
    events = tmp_path / "events.jsonl"
    # Substitute only process boundaries: execute the real shell launcher and
    # its inline maintenance program without Docker or a database service.
    executable = (
        f"#!{sys.executable}\n"
        + r"""
import json
import os
import subprocess
import sys
from pathlib import Path

def record(action):
    from powercontext.server.settings import ServerSettings
    settings = ServerSettings()
    with Path(os.environ["BUB_TEST_EVENTS"]).open("a") as output:
        output.write(json.dumps({"action": action,
            "vector": settings.inference.embedding_model is not None,
            "code": settings.code.enabled}) + "\n")

args = sys.argv[1:]
if Path(sys.argv[0]).name == "docker":
    if "--entrypoint" in args:
        raise SystemExit(subprocess.run([sys.executable, "-c", args[-1]]).returncode)
    if "up" in args and args[-1] == "powercontext":
        record("server-start")
    elif "run" in args and "harness" in args:
        record("harness")
else:
    action = args[2]
    record(action)
    if action == os.environ.get("BUB_TEST_FAILED_ACTION"):
        raise SystemExit(9)
    if action == "apply":
        assert args[args.index("--plan-id") + 1] == "reviewed-plan"
    print(json.dumps({"plan_id": "reviewed-plan", "state": "ready", "server_ready": True}))
"""
    )
    for name in ("docker", "powercontext"):
        path = commands / name
        path.write_text(executable, encoding="utf-8")
        path.chmod(0o755)
    monkeypatch.setenv("PATH", f"{commands}{os.pathsep}{os.defpath}")
    monkeypatch.setenv("PYTHONPATH", str(_ROOT / "src"))
    monkeypatch.setenv("POWERCONTEXT_VERSION", "1.4.1")
    monkeypatch.setenv("GITHUB_SHA", "compose-regression")
    monkeypatch.setenv("POWERCONTEXT_E2E_DATABASE", "oceanbase")
    monkeypatch.setenv("POWERCONTEXT_E2E_OUTPUT", str(tmp_path / "output"))
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", "oceanbase")
    monkeypatch.setenv(
        "POWERCONTEXT_SERVER_DATABASE_URL", "mysql+aoceanbase://test@database.invalid:2881/test?charset=utf8mb4"
    )
    monkeypatch.setenv("POWERCONTEXT_SERVER_CODE", json.dumps({"enabled": code}))
    monkeypatch.setenv("BUB_TEST_EVENTS", str(events))
    monkeypatch.setenv("BUB_TEST_FAILED_ACTION", failed_action)
    if vector:
        monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL", "openai:test-embedding")
        monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID", "test-embedding")
        monkeypatch.setenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION", "3")
    result = subprocess.run(
        ["/bin/sh", str(_ROOT / "e2e/bub/run.sh"), "acceptance"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    return result, [json.loads(line) for line in events.read_text().splitlines()] if events.exists() else []


@pytest.mark.parametrize(("vector", "code"), [(False, False), (True, False), (False, True), (True, True)])
def test_oceanbase_compose_initializes_configured_profile_before_server(tmp_path, monkeypatch, vector, code):
    result, events = _run(tmp_path, monkeypatch, vector=vector, code=code)
    assert result.returncode == 0, result.stderr
    assert [event["action"] for event in events] == ["plan", "apply", "verify", "server-start", "harness"]
    assert all(event["vector"] == vector and event["code"] == code for event in events)


@pytest.mark.parametrize("failed_action", ["plan", "apply", "verify"])
def test_oceanbase_compose_does_not_start_server_after_migration_failure(tmp_path, monkeypatch, failed_action):
    result, events = _run(tmp_path, monkeypatch, vector=True, code=True, failed_action=failed_action)
    assert result.returncode != 0
    actions = [event["action"] for event in events]
    assert failed_action in actions
    assert "server-start" not in actions
    assert "harness" not in actions
