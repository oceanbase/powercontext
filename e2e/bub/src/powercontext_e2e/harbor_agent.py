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

"""Thin Harbor ACP adapter for Bub's native plugin environment."""

from __future__ import annotations

import shlex
from importlib.metadata import version
from pathlib import Path
from typing import Any, override

from harbor.agents.installed import acp as harbor_acp
from harbor.environments.base import BaseEnvironment

AGENT_ID = "powercontext-bub-acp"
REMOTE_BIN_DIR = "/installed-agent/bin"
REMOTE_BUB_HOME = "/installed-agent/bub-home"
REMOTE_BUB_PROJECT = "/installed-agent/bub-project"
REMOTE_CODEX_AUTH = "/run/powercontext/codex-auth.json"
REMOTE_CODEX_HOME = "/installed-agent/codex"
REMOTE_SOURCE = "/opt/powercontext/source"
REMOTE_TOOL_DIR = "/installed-agent/tools"
BUB_VERSION = version("bub")
POWERCONTEXT_VERSION = version("powercontext")
BUB_ACP_SERVER_VERSION = "0.0.2"


class PowerContextBubAcpAgent(harbor_acp.AcpAgent):
    """Install Bub through its supported uv tool and plugin commands."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            registry_entry={
                "id": AGENT_ID,
                "name": "PowerContext Bub ACP",
                "version": f"bub-{BUB_VERSION}",
                "description": "Bub with the local PowerContext integration",
                "distribution": {"uvx": {"package": f"bub-acp-server=={BUB_ACP_SERVER_VERSION}"}},
            },
            distribution_preference=["uvx"],
            auth_policy="disabled",
            permission_mode="allow",
            **kwargs,
        )

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        await self.exec_as_root(
            environment,
            command=self._build_dependencies_command("uvx"),
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
        await self.exec_as_root(environment, command=_install_bub_command())
        await self.exec_as_root(environment, command=_install_acp_server_command())
        agent_user = shlex.quote(str(environment.default_user or "root"))
        await self.exec_as_root(
            environment,
            command=(
                f"chown -R {agent_user} {REMOTE_BIN_DIR} {REMOTE_BUB_HOME} {REMOTE_BUB_PROJECT} "
                f"{REMOTE_CODEX_HOME} {REMOTE_TOOL_DIR}"
            ),
        )

        launcher_path = self.logs_dir / "acp-launch.sh"
        launcher_path.write_text(
            "#!/usr/bin/env sh\n"
            "set -eu\n"
            f'bub_bin=$(dirname "$(readlink -f {REMOTE_BIN_DIR}/bub)")\n'
            'exec "$bub_bin/bub-acp-server" "$@"\n',
            encoding="utf-8",
        )
        await environment.upload_file(source_path=launcher_path, target_path=self._LAUNCHER_REMOTE_PATH)
        runner_path = Path(harbor_acp.__file__).with_name("acp_runner.py")
        await environment.upload_file(source_path=runner_path, target_path=self._RUNNER_REMOTE_PATH)
        await environment.exec(
            command=f"chmod a+rx {self._LAUNCHER_REMOTE_PATH} {self._RUNNER_REMOTE_PATH}",
            user="root",
        )
        self._selected_distribution_kind = "uvx"


def _tool_environment() -> str:
    return f"UV_TOOL_BIN_DIR={shlex.quote(REMOTE_BIN_DIR)} UV_TOOL_DIR={shlex.quote(REMOTE_TOOL_DIR)}"


def _install_bub_command() -> str:
    uv = f"{harbor_acp.AcpAgent._RUNNER_VENV_PATH}/bin/uv"
    return (
        "set -eu; "
        f"mkdir -p {REMOTE_BIN_DIR} {REMOTE_BUB_HOME} {REMOTE_BUB_PROJECT} {REMOTE_CODEX_HOME}; "
        f"if [ -f {REMOTE_CODEX_AUTH} ]; then "
        f"cp {REMOTE_CODEX_AUTH} {REMOTE_CODEX_HOME}/auth.json; "
        f"chmod 600 {REMOTE_CODEX_HOME}/auth.json; "
        "fi; "
        f"SETUPTOOLS_SCM_PRETEND_VERSION={shlex.quote(POWERCONTEXT_VERSION)} {_tool_environment()} "
        f"{uv} tool install --force "
        f"--with {REMOTE_SOURCE} --with {REMOTE_SOURCE}/integrations/bub "
        f"{shlex.quote(f'bub=={BUB_VERSION}')}"
    )


def _install_acp_server_command() -> str:
    runner_bin = f"{harbor_acp.AcpAgent._RUNNER_VENV_PATH}/bin"
    return (
        "set -eu; "
        f"PATH={runner_bin}:$PATH BUB_HOME={REMOTE_BUB_HOME} CODEX_HOME={REMOTE_CODEX_HOME} "
        f"BUB_PROJECT={REMOTE_BUB_PROJECT} "
        f"{_tool_environment()} {REMOTE_BIN_DIR}/bub install "
        f"{shlex.quote(f'bub-acp-server=={BUB_ACP_SERVER_VERSION}')}"
    )
