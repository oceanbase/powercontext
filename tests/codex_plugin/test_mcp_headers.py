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

"""Exercise the credential helper as the short-lived process launched by Codex."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).resolve().parents[2] / "integrations/codex/plugins/powercontext"


@pytest.fixture
def installed_helper(tmp_path):
    plugin = tmp_path / "installed plugin 中文"
    plugin.mkdir()
    for name in ("settings.py", "powercontext_client_config.py", "mcp_headers.py", ".mcp.json"):
        shutil.copyfile(PLUGIN_ROOT / name, plugin / name)
    credential = tmp_path / "separate codex home" / "powercontext" / "credentials.json"
    credential.parent.mkdir(parents=True)
    credential.write_text(
        json.dumps({
            "version": 1,
            "server_url": "http://127.0.0.1:8000",
            "authorization": "Bearer saved-test-token",
        })
    )
    credential.chmod(0o600)
    return plugin, credential


def run_helper(plugin: Path, credential: Path, cwd: Path) -> subprocess.CompletedProcess[str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("POWERCONTEXT_") and key != "CODEX_HOME"
    }
    return subprocess.run(
        [sys.executable, str(plugin / "mcp_headers.py"), "--credential-file", str(credential)],
        env=environment,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


@pytest.mark.parametrize("outside_plugin", [False, True])
def test_native_helper_reads_saved_credential_without_host_environment(installed_helper, tmp_path, outside_plugin):
    plugin, credential = installed_helper
    result = run_helper(plugin, credential, tmp_path if outside_plugin else plugin)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"Authorization": "Bearer saved-test-token"}
    assert result.stderr == ""


@pytest.mark.parametrize("state", ["absent", "different_url", "different_path", "invalid", "invalid_header"])
def test_native_helper_does_not_forward_unusable_credentials(installed_helper, tmp_path, state):
    plugin, credential = installed_helper
    if state == "absent":
        credential.unlink()
    elif state in {"different_url", "different_path"}:
        config = json.loads((plugin / ".mcp.json").read_text())
        config["mcpServers"]["powercontext"]["url"] = (
            "https://other.example/mcp/" if state == "different_url" else "http://127.0.0.1:8000/other/mcp/"
        )
        (plugin / ".mcp.json").write_text(json.dumps(config))
    elif state == "invalid":
        credential.write_text("[]")
    else:
        record = json.loads(credential.read_text())
        record["authorization"] = "Bearer invalid\x00credential"
        credential.write_text(json.dumps(record))
    result = run_helper(plugin, credential, tmp_path)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {}
    assert result.stderr == ""


@pytest.mark.skipif(os.name == "nt", reason="POSIX credential permissions and symlinks")
@pytest.mark.parametrize("unsafe", ["permissions", "symlink"])
def test_native_helper_rejects_unsafe_credential_files(installed_helper, tmp_path, unsafe):
    plugin, credential = installed_helper
    if unsafe == "permissions":
        credential.chmod(0o644)
    else:
        target = credential.with_name("target.json")
        credential.rename(target)
        credential.symlink_to(target)
    result = run_helper(plugin, credential, tmp_path)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {}
    assert result.stderr == ""


def test_native_helper_observes_rotation_and_clear(installed_helper, tmp_path):
    plugin, credential = installed_helper
    record = json.loads(credential.read_text())
    record["authorization"] = "Bearer rotated-test-token"
    credential.write_text(json.dumps(record))
    assert json.loads(run_helper(plugin, credential, tmp_path).stdout) == {"Authorization": "Bearer rotated-test-token"}
    credential.unlink()
    assert json.loads(run_helper(plugin, credential, tmp_path).stdout) == {}
