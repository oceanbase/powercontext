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

"""Harbor's OpenCode agent with the local PowerContext OpenCode plugin installed in the ON arm."""

from __future__ import annotations

from typing import Any, override

from harbor.agents.installed.opencode import OpenCode
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from .harbor_agent import REMOTE_SOURCE

OPENCODE_VERSION = "1.18.33"
REMOTE_PLUGIN = f"{REMOTE_SOURCE}/integrations/opencode/plugins/powercontext"
# OpenCode keeps its sessions in its data directory, and nothing else clears them between the steps of a trial.
OPENCODE_DATA = '"${XDG_DATA_HOME:-$HOME/.local/share}/opencode"'


class PowerContextOpenCodeAgent(OpenCode):
    """Install the PowerContext OpenCode plugin only in the ON arm, and start every session without OpenCode history.

    As on the other hosts, the OFF arm runs without the plugin. The plugin reads its Server URL, Scope, consent, and
    Server token from its own environment, which only the ON arm receives. Each session starts without OpenCode's
    earlier sessions, as sessions do on the other hosts, so neither arm can read an earlier session from OpenCode's
    own database.
    """

    def __init__(
        self,
        *,
        server_url: str,
        powercontext: bool = True,
        reasoning_effort: str | None = None,
        extra_env: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        del server_url  # The ON arm passes it to the plugin through the plugin's environment.
        self._powercontext = powercontext
        super().__init__(
            version=OPENCODE_VERSION,
            # OpenCode selects a provider's reasoning effort with its model variant.
            variant=reasoning_effort,
            # Keep the pinned OpenCode version for the whole run.
            extra_env={"OPENCODE_DISABLE_AUTOUPDATE": "true", **(extra_env or {})},
            **kwargs,
        )

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        await super().install(environment)
        if self._powercontext:
            await self.exec_as_agent(environment, command=install_plugin_command())

    @override
    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        await self.exec_as_agent(environment, command=clear_sessions_command())
        await super().run(instruction, environment, context)


def clear_sessions_command() -> str:
    """Remove OpenCode's session store, keeping the binaries and logs in the same data directory.

    The command then asks OpenCode itself for its sessions and fails if any remain, so a store at another location
    stops the run instead of leaving an earlier session readable.
    """

    # opencode.db holds the sessions; storage/ held them before OpenCode moved to SQLite.
    stores = ("opencode.db", "opencode.db-wal", "opencode.db-shm", "storage")
    return (
        "if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi; "
        "rm -rf " + " ".join(f"{OPENCODE_DATA}/{store}" for store in stores) + "; "
        "sessions=$(opencode session list --format json) || exit 1; "
        # OpenCode prints nothing, or an empty JSON list, when it has no sessions.
        'case "$(printf %s "$sessions" | tr -d \'[:space:][]\')" in '
        "'') ;; "
        "*) echo 'OpenCode still lists an earlier session after clearing its session store' >&2; exit 1 ;; "
        "esac"
    )


def install_plugin_command() -> str:
    """Copy the plugin and its Skill to where ``powercontext setup opencode`` puts them.

    Setup also registers a TUI plugin, which ``opencode run`` does not load.
    """

    config = '"${XDG_CONFIG_HOME:-$HOME/.config}/opencode"'
    return (
        "set -eu; "
        f"mkdir -p {config}/plugins {config}/skills; "
        f"cp {REMOTE_PLUGIN}/lib/index.js {config}/plugins/powercontext-opencode.js; "
        f"rm -rf {config}/skills/powercontext-project-context; "
        f"cp -R {REMOTE_PLUGIN}/skills/powercontext-project-context {config}/skills/"
    )
