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

"""Generated instructions reuse the same integration source as setup and doctor."""

import shutil
from pathlib import Path

import powercontext.cli.config_wizard as wizard
from powercontext.cli.config_wizard_ui import WizardUI


def test_instructions_use_selected_rules_instead_of_the_client_installation(tmp_path, monkeypatch) -> None:
    repository = Path(__file__).resolve().parents[1]
    source = tmp_path / "selected integration source"
    shutil.copytree(repository / "integrations/distribution", source / "integrations/distribution")
    monkeypatch.setenv("POWERCONTEXT_INTEGRATIONS_SOURCE", str(source))
    state = wizard.Wizard(WizardUI("en", interactive=False), {}, {})
    state.agents = ("codex", "claude-code")
    state.agent_addresses = dict.fromkeys(state.agents, "http://127.0.0.1:8000")

    instructions = "\n".join(wizard._agent_installation_steps(state))

    for agent in state.agents:
        assert f"powercontext setup {agent} --source '{source}' --server-url http://127.0.0.1:8000" in instructions
    assert "--ref" not in instructions
