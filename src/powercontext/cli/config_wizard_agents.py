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

from powercontext.cli.hosts import FIRST_CLASS_HOSTS


@dataclass(frozen=True, slots=True)
class AgentSpec:
    """One installable Agent and the configuration names it actually consumes."""

    identifier: str
    en: str
    zh: str
    environment_prefix: str | None
    server_setting: str | None
    authorization_setting: str | None
    capture_setting: str
    scope_setting: str
    context_assembly_setting: str
    setup_server_url: bool = False
    executables: tuple[str, ...] = ()

    def environment_name(self, setting: str | None) -> str | None:
        """Return the full environment name for an environment-backed setting."""
        if self.environment_prefix is None or setting is None:
            return None
        return f"{self.environment_prefix}_{setting}"


_HOST_METADATA = {
    "codex": AgentSpec(
        "codex",
        "Codex",
        "Codex",
        "POWERCONTEXT_CODEX",
        None,
        "AUTHORIZATION",
        "CAPTURE_PROMPTS",
        "SCOPE_ID",
        "CONTEXT_ASSEMBLY",
        executables=("codex",),
    ),
    "claude-code": AgentSpec(
        "claude-code",
        "Claude Code",
        "Claude Code",
        "POWERCONTEXT_CLAUDE",
        "SERVER_URL",
        "AUTHORIZATION",
        "CAPTURE_PROMPTS",
        "SCOPE_ID",
        "CONTEXT_ASSEMBLY",
        setup_server_url=True,
        executables=("claude",),
    ),
    "dsh": AgentSpec(
        "dsh",
        "DeepSeek Harness",
        "DeepSeek Harness",
        "POWERCONTEXT_DSH",
        "BASE_URL",
        "AUTHORIZATION",
        "CAPTURE_PROMPTS",
        "SCOPE_ID",
        "CONTEXT_ASSEMBLY",
        executables=("dsh",),
    ),
    "openclaw": AgentSpec(
        "openclaw",
        "OpenClaw",
        "OpenClaw",
        None,
        "endpoint",
        None,
        "autoCapture",
        "scopeId",
        "contextAssembly",
        setup_server_url=True,
        executables=("openclaw",),
    ),
    "opencode": AgentSpec(
        "opencode",
        "OpenCode",
        "OpenCode",
        "POWERCONTEXT_OPENCODE",
        "BASE_URL",
        "AUTHORIZATION",
        "CAPTURE_PROMPTS",
        "SCOPE_ID",
        "CONTEXT_ASSEMBLY",
        executables=("opencode",),
    ),
    "pi": AgentSpec(
        "pi",
        "Pi",
        "Pi",
        "POWERCONTEXT_PI",
        "BASE_URL",
        "AUTHORIZATION",
        "CAPTURE_PROMPTS",
        "SCOPE_ID",
        "CONTEXT_ASSEMBLY",
        executables=("pi",),
    ),
    "hermes": AgentSpec(
        "hermes",
        "Hermes",
        "Hermes",
        "POWERCONTEXT_HERMES",
        "BASE_URL",
        "AUTHORIZATION",
        "CAPTURE_TURNS",
        "SCOPE_ID",
        "CONTEXT_ASSEMBLY",
        executables=("hermes",),
    ),
    "workbuddy": AgentSpec(
        "workbuddy",
        "WorkBuddy",
        "WorkBuddy",
        "POWERCONTEXT_WORKBUDDY",
        "SERVER_URL",
        "AUTHORIZATION",
        "CAPTURE_PROMPTS",
        "SCOPE_ID",
        "CONTEXT_ASSEMBLY",
    ),
}

AGENT_SPECS: tuple[AgentSpec, ...] = tuple(_HOST_METADATA[host.name] for host in FIRST_CLASS_HOSTS)
AGENT_SPEC_BY_ID = {spec.identifier: spec for spec in AGENT_SPECS}


def preferred_agent(agents: Sequence[AgentSpec], which: Callable[[str], str | None] = shutil.which) -> str:
    """Return the first installed Agent, falling back to Codex."""
    for agent in agents:
        if any(which(command) for command in agent.executables):
            return agent.identifier
    if any(agent.identifier == "codex" for agent in agents):
        return "codex"
    return agents[0].identifier


__all__ = ["AGENT_SPECS", "AGENT_SPEC_BY_ID", "AgentSpec", "preferred_agent"]
