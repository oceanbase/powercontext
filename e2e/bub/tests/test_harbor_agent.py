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

import importlib
import os
import shlex
import shutil
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

from powercontext_e2e.harbor_agent import (
    ACP_RUNNER_SDK_REQUIREMENT,
    ACP_SDK_REQUIREMENT,
    BUB_ACP_SERVER_VERSION,
    REMOTE_BIN_DIR,
    REMOTE_SOURCE_OVERRIDE,
    _install_acp_server_command,
    _install_bub_command,
    _pin_runner_acp_sdk_command,
)
from powercontext_e2e.harbor_codex import MCP_URL_REWRITE, plugin_mcp_url

_SOURCE_OVERRIDE = Path(__file__).resolve().parents[1] / "source-overrides.txt"
_CODEX_PLUGIN = Path(__file__).resolve().parents[3] / "integrations" / "codex" / "plugins" / "powercontext"


def test_install_bub_command_provides_powercontext_version() -> None:
    version_assignment = f"SETUPTOOLS_SCM_PRETEND_VERSION={shlex.quote(version('powercontext'))}"

    assert version_assignment in _install_bub_command().split()


def test_install_bub_command_overrides_release_floor_for_mounted_source() -> None:
    command = shlex.split(_install_bub_command())
    override_index = command.index("--overrides")

    assert command[override_index + 1] == REMOTE_SOURCE_OVERRIDE
    assert _SOURCE_OVERRIDE.read_text(encoding="utf-8").splitlines()[-1] == (
        "powercontext[client] @ file:///opt/powercontext/source"
    )


def test_off_arm_installs_bub_without_the_powercontext_plugin() -> None:
    command = _install_bub_command(powercontext=False)

    assert "integrations/bub" not in command
    assert f"bub[app]=={version('bub')}" in command


def test_acp_server_install_names_the_pre_release_sdk_and_keeps_the_harness_bub() -> None:
    # uv resolves the server's pre-release ACP SDK only as a direct requirement, and Bub's plugin project would
    # otherwise let the install upgrade the host.
    command = shlex.split(_install_acp_server_command())
    install_index = command.index(f"{REMOTE_BIN_DIR}/bub") + 1

    assert command[install_index] == "install"
    assert set(command[install_index + 1 :]) == {
        f"bub-acp-server=={BUB_ACP_SERVER_VERSION}",
        ACP_SDK_REQUIREMENT,
        f"bub[app]=={version('bub')}",
    }


def test_runner_venv_is_pinned_to_the_last_sdk_release_with_the_runner_symbols() -> None:
    # Harbor installs the SDK in its runner venv without a version, so the reorganized 1.x releases resolve there
    # and the runner dies on ImportError before the agent starts. The runner pin must name a release that still
    # exports the runner's symbols, which is not the pre-release the ACP server pins in its own environment.
    command = _pin_runner_acp_sdk_command()

    assert f"pip install --disable-pip-version-check --no-input {ACP_RUNNER_SDK_REQUIREMENT}" in command
    assert ACP_SDK_REQUIREMENT not in command
    assert "'text_block'" in command
    # The probe must fail the setup when a runner symbol is still missing, not merely print it.
    assert "raise SystemExit(bool(missing))" in command


def test_codex_plugin_accepts_the_server_url_the_harness_writes(monkeypatch, tmp_path: Path) -> None:
    # The plugin validates its .mcp.json strictly, so load the rewritten copy with the plugin's own settings.
    for name in ("settings.py", "powercontext_client_config.py", ".mcp.json"):
        shutil.copy2(_CODEX_PLUGIN / name, tmp_path / name)
    server_url = "http://host-gateway:8000"
    subprocess.run(  # noqa: S603 - fixed interpreter and arguments
        [sys.executable, "-c", MCP_URL_REWRITE, str(tmp_path / ".mcp.json"), plugin_mcp_url(server_url)],
        check=True,
    )
    for name in tuple(os.environ):
        if name.startswith("POWERCONTEXT_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(tmp_path / "clients.json"))
    monkeypatch.setenv("POWERCONTEXT_CODEX_ALLOW_INSECURE_HTTP", "true")
    monkeypatch.syspath_prepend(str(tmp_path))
    for module in ("settings", "powercontext_client_config"):
        monkeypatch.delitem(sys.modules, module, raising=False)

    plugin_settings = importlib.import_module("settings")

    assert plugin_settings.CodexPluginSettings().server_url == server_url
