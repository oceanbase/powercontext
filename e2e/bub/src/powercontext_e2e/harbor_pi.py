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

"""Harbor's Pi agent with the local PowerContext Pi package installed and switched per arm."""

from __future__ import annotations

from typing import Any, override

from harbor.agents.installed.pi import Pi
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from .harbor_agent import REMOTE_SOURCE

# The version the PowerContext Pi package's lockfile and CI test against.
PI_VERSION = "0.82.1"
PI_PACKAGE = "@earendil-works/pi-coding-agent"
REMOTE_PLUGIN = f"{REMOTE_SOURCE}/integrations/pi/plugins/powercontext"
# Pi's bash tool keeps the full output of a command it truncated, over 2,000 lines or 50 KB, in its temporary
# directory, and nothing else clears it between the steps of a trial. Node's os.tmpdir() reads TMPDIR, then TMP, then
# TEMP.
PI_TOOL_OUTPUT = '"${TMPDIR:-${TMP:-${TEMP:-/tmp}}}"/pi-bash-*.log'


class PowerContextPiAgent(Pi):
    """Install the PowerContext Pi package only in the ON arm, and start every session without Pi's earlier output.

    As on the other hosts, the OFF arm runs without the package. The package reads its Server URL, Scope, consent, and
    Server token from its own environment, which only the ON arm receives. Harbor runs Pi without a saved session, and
    each session starts without the tool output an earlier session saved, so neither arm can read an earlier session
    from Pi's own files.
    """

    def __init__(
        self,
        *,
        server_url: str,
        powercontext: bool = True,
        reasoning_effort: str | None = None,
        **kwargs: Any,
    ) -> None:
        del server_url  # The ON arm passes it to the package through the package's environment.
        self._powercontext = powercontext
        super().__init__(version=PI_VERSION, thinking=reasoning_effort, **kwargs)

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        # Harbor installs Pi from its deprecated npm name, which stopped before the versions this package supports,
        # so Harbor 0.16's steps install it from the current name. Compare them when upgrading Harbor.
        await self.exec_as_root(
            environment,
            command="apt-get update && apt-get install -y curl",
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                "curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.2/install.sh | bash && "
                'export NVM_DIR="$HOME/.nvm" && '
                '\\. "$NVM_DIR/nvm.sh" || true && '
                "command -v nvm &>/dev/null || { echo 'Error: NVM failed to load' >&2; exit 1; } && "
                "nvm install 22 && "
                f"npm install -g {PI_PACKAGE}@{self._version} && "
                "pi --version"
            ),
        )
        if self._powercontext:
            # As `powercontext setup pi` does; Pi reads the package in place from the read-only mount.
            await self.exec_as_agent(environment, command=install_plugin_command())

    @override
    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        await self.exec_as_agent(environment, command=f"rm -f {PI_TOOL_OUTPUT}")
        await super().run(instruction, environment, context)


def install_plugin_command() -> str:
    """Install the mounted PowerContext Pi package into Pi."""

    # Harbor's Pi agent loads Node the same way before each run.
    return f". ~/.nvm/nvm.sh; pi install {REMOTE_PLUGIN}"
