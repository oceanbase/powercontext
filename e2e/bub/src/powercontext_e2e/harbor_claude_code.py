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

"""Harbor's Claude Code agent with the local PowerContext Claude Code plugin installed and switched per arm."""

from __future__ import annotations

import shlex
from typing import Any, override

from harbor.agents.installed.claude_code import ClaudeCode
from harbor.environments.base import BaseEnvironment

from .harbor_agent import REMOTE_SOURCE

CLAUDE_CODE_VERSION = "2.1.284"
PLUGIN_ID = "powercontext@powercontext"


class PowerContextClaudeCodeAgent(ClaudeCode):
    """Run Claude Code with the local PowerContext Claude Code plugin installed only in the ON arm.

    The OFF arm is Claude Code as a user without PowerContext has it, with nothing of the plugin in its container, so
    its agent cannot find PowerContext and search for it.
    """

    def __init__(self, *, server_url: str, powercontext: bool = True, **kwargs: Any) -> None:
        self._server_url = server_url
        self._powercontext = powercontext
        super().__init__(version=CLAUDE_CODE_VERSION, **kwargs)

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        await super().install(environment)
        if not self._powercontext:
            return
        # The plugin's hook runs `python3` with the standard library only.
        await self.exec_as_agent(
            environment,
            command="command -v python3 >/dev/null || { echo 'The PowerContext plugin hook needs python3' >&2; exit 1; }",
        )

    @override
    def _build_register_mcp_servers_command(self) -> str | None:
        # Harbor points CLAUDE_CONFIG_DIR at the step's log directory, which it clears after every step, so the
        # plugin is installed again before each Claude Code session, after Harbor's own MCP configuration.
        commands = [install_plugin_command(self._server_url)] if self._powercontext else []
        if task_servers := super()._build_register_mcp_servers_command():
            commands.insert(0, task_servers)
        return " && ".join(commands) or None


def install_plugin_command(server_url: str) -> str:
    """Install the plugin from the mounted marketplace, as ``powercontext setup claude-code`` would.

    The plugin's MCP connection reads the Server URL only from its ``server_url`` option; its hook reads
    ``POWERCONTEXT_CLAUDE_SERVER_URL`` first, which the ON arm passes through.
    """

    server_option = shlex.quote("server_url=" + server_url.rstrip("/"))
    steps = [
        'export PATH="$HOME/.local/bin:$PATH"',
        f"claude plugin marketplace add {REMOTE_SOURCE} --scope user >/dev/null",
        f"claude plugin install {PLUGIN_ID} --scope user --config {server_option} >/dev/null",
    ]
    return "(set -e; " + "; ".join(steps) + ")"
