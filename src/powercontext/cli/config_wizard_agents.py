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

"""Agent integration contracts used by the guided configuration wizard."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """One installable Agent and the configuration names it actually consumes."""

    identifier: str
    en: str
    zh: str
    environment_prefix: str | None
    server_setting: str | None
    authorization_setting: str | None
    capture_setting: str | None
    scope_setting: str | None
    context_assembly_setting: str | None
    setup_server_url: bool = False
    executables: tuple[str, ...] = ()

    def environment_name(self, setting: str | None) -> str | None:
        """Return the full environment name for an environment-backed setting."""
        if self.environment_prefix is None or setting is None:
            return None
        return f"{self.environment_prefix}_{setting}"


def agent_specs() -> tuple[AgentSpec, ...]:
    """Read host choices from the same source used by setup and doctor."""
    from powercontext.cli.integration_source import load_rules, resolve_source

    root = resolve_source(fetch=True)
    return load_rules(root, "agents").agent_specs() if root else ()


def preferred_agent(agents: Sequence[AgentSpec], which: Callable[[str], str | None] = shutil.which) -> str:
    """Return the first installed Agent, falling back to Codex."""
    for agent in agents:
        if any(which(command) for command in agent.executables):
            return agent.identifier
    if any(agent.identifier == "codex" for agent in agents):
        return "codex"
    return agents[0].identifier


__all__ = ["AgentSpec", "agent_specs", "preferred_agent"]
