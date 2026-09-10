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

import powercontext.cli.config_wizard_agents as wizard_agents
from powercontext.cli.config_wizard_agents import AGENT_SPECS
from powercontext.cli.hosts import FIRST_CLASS_HOSTS


def test_wizard_catalog_covers_first_class_hosts() -> None:
    assert tuple(spec.identifier for spec in AGENT_SPECS) == tuple(host.name for host in FIRST_CLASS_HOSTS)


def test_openclaw_keeps_its_plugin_configuration_contract() -> None:
    spec = next(spec for spec in AGENT_SPECS if spec.identifier == "openclaw")
    assert spec.environment_prefix is None
    assert spec.capture_setting == "autoCapture"
    assert spec.scope_setting == "scopeId"


def test_preferred_agent_uses_an_installed_agent_then_falls_back_to_codex() -> None:
    installed: Callable[[str], str | None] = lambda command: "/bin/claude" if command == "claude" else None

    assert wizard_agents.preferred_agent(AGENT_SPECS, which=installed) == "claude-code"
    assert wizard_agents.preferred_agent(AGENT_SPECS, which=lambda command: None) == "codex"


def test_agent_executables_match_real_launch_commands() -> None:
    assert {spec.identifier: spec.executables for spec in AGENT_SPECS} == {
        "codex": ("codex",),
        "claude-code": ("claude",),
        "dsh": ("dsh",),
        "openclaw": ("openclaw",),
        "opencode": ("opencode",),
        "pi": ("pi",),
        "hermes": ("hermes",),
        "workbuddy": (),
    }
