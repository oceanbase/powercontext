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

"""Distribution ownership, reproducibility, and native host contract tests."""

import json
import re
import shutil
from pathlib import PurePosixPath

import pytest
from agent_distribution import ROOT, assemble, require_runners, write_package
from powercontext_integrations.resources import install_resources, render_resources
from powercontext_integrations.targets import load_targets
from typer.testing import CliRunner

from powercontext.cli.app import create_cli


def test_all_first_wave_targets_build_and_minimax_keeps_its_native_loader(tmp_path) -> None:
    targets = load_targets()
    integration_directories = {
        path.name
        for path in (ROOT / "integrations").iterdir()
        if path.is_dir() and path.name not in {"distribution", "shared"}
    }
    assert {item.target for item in targets} == integration_directories
    artifacts = {}
    for target in targets:
        files = assemble(target)
        assert files == assemble(target)
        if target.hook_file:
            assert files[target.hook_file] == (ROOT / target.source / target.hook_file).read_bytes()
        if target.hook_manifest:
            assert files[target.hook_manifest] == (ROOT / target.source / target.hook_manifest).read_bytes()
        assert "runtime.json" not in files
        assert not any(name.startswith("runtime/") for name in files)
        assert not any("tests" in name.split("/") or name.endswith((".test.ts", ".spec.ts")) for name in files)
        for name, content in files.items():
            if name.endswith("/SKILL.md"):
                for reference in re.findall(r"\]\((references/[^)]+)\)", content.decode()):
                    assert str(PurePosixPath(name).parent / reference) in files
        destination = tmp_path / target.target
        write_package(destination, files)
        write_package(destination, files, check=True)
        artifacts[target.target] = files
    minimax = artifacts["minimax"]
    manifest = json.loads(minimax[".minimax-plugin/plugin.json"])
    assert manifest["mcpServers"] == ["powercontext.mcp.json"]
    assert all(name in minimax for name in manifest["skills"])
    assert manifest["hooks"] == ["hooks/hooks.json"]
    assert "UserPromptSubmit" in json.loads(minimax["hooks/hooks.json"])["hooks"]
    assert all("+" not in name for name in minimax)
    assert (
        minimax["skills/powercontext-project-context/SKILL.md"]
        == artifacts["agent-plugin"]["skills/powercontext-project-context/SKILL.md"]
    )
    codex = json.loads(artifacts["codex"]["hooks/hooks.json"])["hooks"]
    assert codex["PreToolUse"][0]["matcher"] == "mcp__powercontext__.*"
    assert codex["UserPromptSubmit"][0]["hooks"][0]["command"] == (
        'powercontext-hook "--script" "${PLUGIN_ROOT}/hooks/recall.py"'
    )


def test_modified_or_foreign_output_is_preserved_and_stale_owned_files_are_removed(tmp_path) -> None:
    target = next(item for item in load_targets() if item.target == "minimax")
    files = assemble(target)
    output = tmp_path / "output"
    write_package(output, files)
    manifest = json.loads(files["distribution.json"])
    reduced = {name: data for name, data in files.items() if name != "icon.png"}
    manifest["files"].pop("icon.png")
    reduced["distribution.json"] = json.dumps(manifest).encode()
    write_package(output, reduced)
    assert not (output / "icon.png").exists()
    (output / "README.md").write_text("User changes")
    with pytest.raises(ValueError, match="modified"):
        write_package(output, files)
    assert (output / "README.md").read_text() == "User changes"


def test_symlink_output_is_rejected_before_writing(tmp_path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    output = tmp_path / "output"
    output.symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        write_package(output, {"new.txt": b"new"})
    assert not (external / "new.txt").exists()


def test_setup_regenerates_resources_and_preserves_private_mcp_configuration(tmp_path) -> None:
    path = tmp_path / ".mcp.json"
    config = {
        "mcpServers": {
            "powercontext": {
                "type": "http",
                "url": "https://private.example/mcp/",
                "headers": {"Authorization": "private-value"},
            },
            "other": {"command": "other-server"},
        }
    }
    path.write_text(json.dumps(config))
    install_resources("codex", tmp_path)
    assert json.loads(path.read_text()) == config
    for name, content in render_resources("codex").items():
        if name != ".mcp.json":
            assert (tmp_path / name).read_bytes() == content
    install_resources("codex", tmp_path, server_url="https://new.example")
    config["mcpServers"]["powercontext"]["url"] = "https://new.example/mcp/"
    assert json.loads(path.read_text()) == config


@pytest.mark.parametrize("missing", ["npx", "powercontext-hook"])
def test_missing_build_prerequisite_is_actionable(monkeypatch, missing) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: None if name == missing else f"/bin/{name}")
    with pytest.raises(RuntimeError, match=missing):
        require_runners()


def test_setup_loads_a_new_target_from_source_and_doctor_reuses_it(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source"
    shutil.copytree(ROOT / "integrations/distribution", source / "integrations/distribution")
    shutil.copytree(ROOT / "integrations/agent-plugin", source / "integrations/agent-plugin")
    assets = source / "integrations/distribution/powercontext_integrations/assets"
    profile = (
        (assets / "targets/agent-plugin.toml")
        .read_text()
        .replace('target = "agent-plugin"', 'target = "evaluation-probe"')
    )
    (assets / "targets/evaluation-probe.toml").write_text(profile)
    resources = json.loads((assets / "resources.json").read_text())
    resources["evaluation-probe"] = resources["agent-plugin"]
    (assets / "resources.json").write_text(json.dumps(resources))
    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(tmp_path / "clients.json"))
    monkeypatch.delenv("POWERCONTEXT_INTEGRATIONS_SOURCE", raising=False)
    env_file = tmp_path / "selected.env"
    env_file.write_text("POWERCONTEXT_CLIENT_SERVER_URL=https://memory.example/proxy\n")
    app = create_cli()
    runner = CliRunner()

    setup = runner.invoke(
        app,
        [
            "setup",
            "--env-file",
            str(env_file),
            "evaluation-probe",
            "--source",
            str(source),
            "--destination",
            str(tmp_path / "plugin"),
            "--json",
        ],
    )
    assert setup.exit_code == 0, setup.output
    installed_mcp = json.loads((tmp_path / "plugin/mcp.json").read_text())
    assert installed_mcp["mcpServers"]["powercontext"]["url"] == "https://memory.example/proxy/mcp"
    doctor = runner.invoke(app, ["doctor", "evaluation-probe", "--json"])
    assert doctor.exit_code == 0, doctor.output
    assert json.loads(doctor.output)["ok"] is True
