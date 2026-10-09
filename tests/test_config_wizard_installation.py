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

"""Generated Agent installation instructions retain the installed package's source."""

import importlib.metadata
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import powercontext.cli.config as config_cli
import powercontext.cli.config_wizard as wizard
from powercontext.cli.config_wizard_ui import WizardUI


def _installed_package(tmp_path: Path, monkeypatch, direct_url: object | None) -> Path:
    module = tmp_path / "tool/lib/python3.12/site-packages/powercontext/cli/config_wizard.py"
    module.parent.mkdir(parents=True)
    module.touch()
    dist_info = module.parents[2] / "powercontext-0.1.dist-info"
    dist_info.mkdir()
    if direct_url is not None:
        (dist_info / "direct_url.json").write_text(json.dumps(direct_url))
    distribution = importlib.metadata.Distribution.at(dist_info)
    monkeypatch.setattr(importlib.metadata, "distribution", lambda name: distribution)
    monkeypatch.setattr(wizard, "__file__", str(module))
    return module


def _instructions() -> str:
    state = wizard.Wizard(WizardUI("en", interactive=False), {}, {})
    state.agents = ("codex", "claude-code")
    state.agent_addresses = dict.fromkeys(state.agents, "http://127.0.0.1:8000")
    return "\n".join(wizard._agent_installation_steps(state))


def test_wheel_installation_instructions_keep_the_fork_and_branch(tmp_path, monkeypatch) -> None:
    _installed_package(
        tmp_path,
        monkeypatch,
        {
            "url": "https://github.com/example/powercontext.git",
            "vcs_info": {"vcs": "git", "requested_revision": "codex/guided-config", "commit_id": "a" * 40},
        },
    )

    instructions = _instructions()

    for agent in ("codex", "claude-code"):
        assert (
            f"powercontext setup {agent} --source https://github.com/example/powercontext.git --ref codex/guided-config"
        ) in instructions
    assert "--server-url http://127.0.0.1:8000" in instructions
    assert str(tmp_path / "tool") not in instructions


def test_checkout_instructions_keep_a_verified_local_source(tmp_path, monkeypatch) -> None:
    checkout = tmp_path / "powercontext checkout"
    module = checkout / "src/powercontext/cli/config_wizard.py"
    module.parent.mkdir(parents=True)
    module.touch()
    for marker in (".agents/plugins/marketplace.json", ".claude-plugin/marketplace.json"):
        target = checkout / marker
        target.parent.mkdir(parents=True)
        target.write_text("{}")
    for agent in ("codex", "claude-code"):
        (checkout / f"integrations/{agent}/plugins/powercontext").mkdir(parents=True)
    monkeypatch.setattr(wizard, "__file__", str(module))

    instructions = _instructions()

    assert f"--source '{checkout}'" in instructions
    assert "--ref" not in instructions


@pytest.mark.parametrize("installed", ["0.2.0", "1.0.0rc1", "1.0.0", "1.1.0"])
def test_index_release_instructions_pin_every_agent_and_the_selected_file(tmp_path, monkeypatch, installed) -> None:
    module = _installed_package(tmp_path, monkeypatch, None)
    metadata = module.parents[2] / "powercontext-0.1.dist-info" / "METADATA"
    metadata.write_text(f"Metadata-Version: 2.1\nName: powercontext\nVersion: {installed}\n")
    monkeypatch.setattr(config_cli, "__file__", str(module))
    environment = tmp_path / "server.env"

    result = CliRunner().invoke(config_cli.app, ["init", "--template", "--output", str(environment)], input="\n")

    assert result.exit_code == 0, result.output
    for agent in config_cli.AGENTS:
        assert (
            f"powercontext setup --env-file {environment} {agent} --source oceanbase/powercontext "
            f"--ref powercontext-v{installed}"
        ) in result.output
    instructions = _instructions()
    for agent in ("codex", "claude-code"):
        assert (
            f"powercontext setup {agent} --source oceanbase/powercontext --ref powercontext-v{installed}"
            in instructions
        )
    assert "--ref master" not in instructions


def test_unreleased_index_package_requires_an_explicit_matching_source(tmp_path, monkeypatch) -> None:
    module = _installed_package(tmp_path, monkeypatch, None)
    metadata = module.parents[2] / "powercontext-0.1.dist-info" / "METADATA"
    metadata.write_text("Metadata-Version: 2.1\nName: powercontext\nVersion: 0.2.1.dev1+g1234567\n")
    assert "powercontext setup codex --help" in _instructions()
    assert "--ref master" not in _instructions()


@pytest.mark.parametrize(
    "direct_url",
    [
        None,
        {"url": "https://github.com/example/powercontext.git", "vcs_info": {"vcs": "git", "commit_id": "a" * 40}},
        {"url": "https://github.com/example/powercontext/archive/main.zip", "archive_info": {}},
        {
            "url": "https://secret@github.com/example/powercontext.git",
            "vcs_info": {"vcs": "git", "requested_revision": "main"},
        },
    ],
)
def test_unknown_wheel_source_requires_a_matching_repository_and_ref(tmp_path, monkeypatch, direct_url) -> None:
    _installed_package(tmp_path, monkeypatch, direct_url)

    instructions = _instructions()

    assert "matching repository and branch or tag" in instructions
    assert "--source" not in instructions
    assert "secret@" not in instructions
    assert "powercontext setup codex --help" in instructions
