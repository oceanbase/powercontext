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
# OpenCode keeps its sessions, and output derived from them, in its data and temporary directories, and nothing else
# clears them between the steps of a trial.
OPENCODE_DATA = '"${XDG_DATA_HOME:-$HOME/.local/share}/opencode"'
# Node's os.tmpdir() reads TMPDIR, then TMP, then TEMP.
OPENCODE_TMP = '"${TMPDIR:-${TMP:-${TEMP:-/tmp}}}/opencode"'
# Stored credentials are the only part of the data directory kept across sessions.
OPENCODE_DATA_KEPT = ("auth.json", "mcp-auth.json")


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
    """Remove everything an earlier session left in OpenCode's data and temporary directories.

    Besides the session database, OpenCode keeps oversized tool results, plans, snapshots, worktrees, and logs in its
    data directory, and the agent may write to its temporary directory, so only stored credentials are kept. OpenCode
    creates the rest again on start. The command then asks OpenCode for its sessions and fails if any remain,
    so a session database at another location stops the run instead of leaving an earlier session readable.
    """

    kept = " ".join(f"! -name {name}" for name in OPENCODE_DATA_KEPT)
    return (
        "if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi; "
        f"mkdir -p {OPENCODE_DATA} || exit 1; "
        # The trailing /. makes find descend into the directory when it is a symlink.
        f"find {OPENCODE_DATA}/. -mindepth 1 -maxdepth 1 {kept} -exec rm -rf {{}} + || exit 1; "
        f"rm -rf {OPENCODE_TMP} || exit 1; "
        "sessions=$(opencode session list --format json) || exit 1; "
        # OpenCode prints nothing, or an empty JSON list, when it has no sessions.
        'case "$(printf %s "$sessions" | tr -d \'[:space:][]\')" in '
        "'') ;; "
        "*) echo 'OpenCode still lists an earlier session after clearing its data directory' >&2; exit 1 ;; "
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
