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

"""Harbor's Codex agent with the local PowerContext Codex plugin installed and switched per arm."""

from __future__ import annotations

import shlex
from typing import Any, ClassVar, override

from harbor.agents.installed.base import CliFlag
from harbor.agents.installed.codex import Codex
from harbor.environments.base import BaseEnvironment

from .harbor_agent import REMOTE_SOURCE

CODEX_VERSION = "0.153.4"
PLUGIN_UV_VERSION = "0.10.12"
REMOTE_MARKETPLACE = f"{REMOTE_SOURCE}/integrations/codex"
REMOTE_PLUGIN_PROJECT = f"{REMOTE_MARKETPLACE}/plugins/powercontext"
REMOTE_PLUGIN_ENV = "/installed-agent/codex-plugin-env"
REMOTE_UV_PYTHON = "/installed-agent/uv-python"
MARKETPLACE_NAME = "powercontext-local"
PLUGIN_ID = f"powercontext@{MARKETPLACE_NAME}"
# Run with the plugin's Python as `python -c MCP_URL_REWRITE <.mcp.json> <MCP URL>`.
MCP_URL_REWRITE = (
    "import json, sys; path, url = sys.argv[1:]; "
    "config = json.load(open(path, encoding='utf-8')); "
    "config['mcpServers']['powercontext']['url'] = url; "
    "json.dump(config, open(path, 'w', encoding='utf-8'), indent=2)"
)


class PowerContextCodexAgent(Codex):
    """Run Codex with the local PowerContext Codex plugin installed and enabled only in the ON arm.

    The OFF arm is Codex as a user without PowerContext has it, with nothing of the plugin in its container, so its
    agent cannot find PowerContext and search for it. The ON arm's hooks run without interactive trust, as an
    unattended run cannot grant it.
    """

    CLI_FLAGS: ClassVar[list[CliFlag]] = [
        *Codex.CLI_FLAGS,
        CliFlag("plugins", cli="--enable", type="enum", choices=["enable", "disable"], format="--{value} plugins"),
        CliFlag("bypass_hook_trust", cli="--dangerously-bypass-hook-trust", type="bool"),
    ]

    def __init__(self, *, server_url: str, powercontext: bool = True, **kwargs: Any) -> None:
        self._server_url = server_url
        self._powercontext = powercontext
        super().__init__(
            version=CODEX_VERSION,
            plugins="enable" if powercontext else None,
            bypass_hook_trust=powercontext,
            **kwargs,
        )

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        await super().install(environment)
        if not self._powercontext:
            return
        await self.exec_as_root(environment, command=_install_plugin_runtime_command())
        agent_user = shlex.quote(str(environment.default_user or "root"))
        await self.exec_as_root(environment, command=f"chown -R {agent_user} {REMOTE_PLUGIN_ENV} {REMOTE_UV_PYTHON}")

    @override
    def _build_register_mcp_servers_command(self) -> str | None:
        # Harbor removes CODEX_HOME after every step, so the plugin is added again before each Codex session.
        commands = [install_plugin_command(self._server_url)] if self._powercontext else []
        if task_servers := super()._build_register_mcp_servers_command():
            commands.append(task_servers)
        return "\n".join(commands) or None


def _install_plugin_runtime_command() -> str:
    """Install uv and the plugin's Python dependencies outside CODEX_HOME, where they survive between steps."""

    uv_installer = f"https://astral.sh/uv/{PLUGIN_UV_VERSION}/install.sh"
    return (
        "set -eu; "
        f"if ! uv --version 2>/dev/null | grep -q '^uv {PLUGIN_UV_VERSION} '; then "
        f"curl -LsSf {uv_installer} | env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh; "
        "fi; "
        f"UV_PROJECT_ENVIRONMENT={REMOTE_PLUGIN_ENV} UV_PYTHON_INSTALL_DIR={REMOTE_UV_PYTHON} "
        f"uv sync --quiet --frozen --no-install-project --project {REMOTE_PLUGIN_PROJECT}; "
        f"mkdir -p {REMOTE_UV_PYTHON}"
    )


def plugin_mcp_url(server_url: str) -> str:
    """Return the MCP endpoint that ``powercontext setup codex --server-url`` writes for a Server URL."""

    return server_url.rstrip("/") + "/mcp/"


def install_plugin_command(server_url: str) -> str:
    """Add the plugin to CODEX_HOME and point it at the Server, as ``powercontext setup codex`` would.

    The plugin reads its Server URL only from the installed ``.mcp.json``, which its MCP connection also uses. Its
    hooks run ``uv run --project`` in the installed copy, so that copy's environment links to the synced one.
    """

    mcp_url = plugin_mcp_url(server_url)
    return (
        "if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi\n"
        "set -eu\n"
        f"codex plugin marketplace add {REMOTE_MARKETPLACE} >/dev/null\n"
        f"codex plugin add {PLUGIN_ID} >/dev/null\n"
        f'for plugin_root in "$CODEX_HOME"/plugins/cache/{MARKETPLACE_NAME}/powercontext/*; do\n'
        '  rm -rf "$plugin_root/.venv"\n'
        f'  ln -s {REMOTE_PLUGIN_ENV} "$plugin_root/.venv"\n'
        f'  {REMOTE_PLUGIN_ENV}/bin/python -c {shlex.quote(MCP_URL_REWRITE)} "$plugin_root/.mcp.json" {shlex.quote(mcp_url)}\n'
        "done"
    )
