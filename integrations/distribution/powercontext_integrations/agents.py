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

"""Expose host profiles to the installed configuration wizard."""

from powercontext.cli.config_wizard_agents import AgentSpec

from .host import HOST_ADAPTERS


def _agent_spec(host) -> AgentSpec:
    values = {
        "environment_prefix": "POWERCONTEXT_" + host.name.upper().replace("-", "_"),
        "server_setting": "BASE_URL",
        "authorization_setting": "AUTHORIZATION",
        "capture_setting": "CAPTURE_PROMPTS",
        "scope_setting": "SCOPE_ID",
        "context_assembly_setting": "CONTEXT_ASSEMBLY",
        **{key: value or None for key, value in host.target.setup.settings.items()},
    }
    return AgentSpec(
        host.name,
        host.label,
        host.label,
        **values,
        setup_server_url=True,
        executables=(host.target.setup.executable,) if host.target.setup.executable else (),
    )


def agent_specs() -> tuple[AgentSpec, ...]:
    return tuple(_agent_spec(host) for host in HOST_ADAPTERS)
