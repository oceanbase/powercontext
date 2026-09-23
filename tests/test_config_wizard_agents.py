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

"""Wizard-facing Agent integration catalog contracts."""

from collections.abc import Callable

from powercontext_integrations.hosts import HOST_ADAPTERS

import powercontext.cli.config_wizard_agents as wizard_agents
from powercontext.cli.config_wizard_agents import agent_specs


def test_wizard_catalog_covers_first_class_hosts() -> None:
    assert tuple(spec.identifier for spec in agent_specs()) == tuple(host.name for host in HOST_ADAPTERS)


def test_openclaw_keeps_its_plugin_configuration_contract() -> None:
    spec = next(spec for spec in agent_specs() if spec.identifier == "openclaw")
    assert spec.environment_prefix is None
    assert spec.capture_setting == "autoCapture"
    assert spec.scope_setting == "scopeId"


def test_preferred_agent_uses_an_installed_agent_then_falls_back_to_codex() -> None:
    installed: Callable[[str], str | None] = lambda command: "/bin/claude" if command == "claude" else None

    assert wizard_agents.preferred_agent(agent_specs(), which=installed) == "claude-code"
    assert wizard_agents.preferred_agent(agent_specs(), which=lambda command: None) == "codex"
