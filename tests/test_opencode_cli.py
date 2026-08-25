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

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from powercontext.cli.app import create_cli
from powercontext.cli.system import SetupError, doctor_app, setup_app


def _write_plugin(root: Path, *, built: bool = True) -> Path:
    plugin = root / "integrations" / "opencode" / "plugins" / "powercontext"
    (plugin / "skills" / "project-context").mkdir(parents=True)
    (plugin / "package.json").write_text('{"name": "powercontext-opencode"}', encoding="utf-8")
    (plugin / "skills" / "project-context" / "SKILL.md").write_text(
        "---\nname: project-context\ndescription: test\n---\n",
        encoding="utf-8",
    )
    if built:
        (plugin / "lib").mkdir()
        (plugin / "lib" / "index.js").write_text("export default {}\n", encoding="utf-8")
    return plugin


def _fake_opencode(plugin: Path, config: Path):
    def run(*arguments: str, env: dict[str, str] | None = None) -> str:
        if arguments == ("--version",):
            return "1.18.21\n"
        if arguments == ("debug", "paths"):
            return f"config     {config}\n"
        if arguments == ("debug", "config"):
            if env is not None:
                Path(env["POWERCONTEXT_OPENCODE_ACTIVATION_PROBE_PATH"]).write_text(
                    env["POWERCONTEXT_OPENCODE_ACTIVATION_PROBE_NONCE"],
                    encoding="utf-8",
                )
            return json.dumps({"plugin": [plugin.as_uri()]})
        if arguments[0] == "plugin":
            return ""
        raise AssertionError(arguments)

    return run


def test_setup_opencode_installs_plugin_and_owned_skill(tmp_path: Path, monkeypatch) -> None:
    import powercontext.cli.opencode as opencode_cli

    checkout = tmp_path / "checkout"
    plugin = _write_plugin(checkout)
    config = tmp_path / "config"
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(opencode_cli, "which", lambda _name: "/usr/bin/opencode")
    monkeypatch.setattr(opencode_cli, "_run_opencode", _fake_opencode(plugin, config))

    result = CliRunner().invoke(create_cli([setup_app]), ["setup", "opencode", "--source", str(checkout)])

    skill = config / "skills" / "project-context"
    assert result.exit_code == 0
    assert "PowerContext OpenCode setup complete." in result.output
    assert (skill / "SKILL.md").is_file()
    assert json.loads((skill / ".powercontext.json").read_text(encoding="utf-8"))["owner"] == "powercontext"


def test_remote_checkout_cache_is_scoped_by_source_and_resolved_commit(tmp_path: Path, monkeypatch) -> None:
    import powercontext.cli.opencode as opencode_cli

    commits = iter(["a" * 40, "b" * 40, "a" * 40])

    def clone(_source: str, _ref: str, target: Path) -> None:
        _write_plugin(target)

    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(opencode_cli, "clone_github_source", clone)
    monkeypatch.setattr(opencode_cli, "_checkout_commit", lambda _target: next(commits), raising=False)

    first = opencode_cli._materialize_remote_checkout("owner-a/repo", "master")
    refreshed = opencode_cli._materialize_remote_checkout("owner-a/repo", "master")
    other_source = opencode_cli._materialize_remote_checkout("owner-b/repo", "master")

    assert first != refreshed
    assert first != other_source
    assert first.name == "a" * 40
    assert refreshed.name == "b" * 40
    assert other_source.name == "a" * 40
    assert opencode_cli.checkout_target("owner-a/repo", "master", "a" * 40) == opencode_cli.checkout_target(
        "git@github.com:OWNER-A/REPO.git", "master", "a" * 40
    )


def test_remote_checkout_refresh_failure_keeps_previous_commit(tmp_path: Path, monkeypatch) -> None:
    import powercontext.cli.opencode as opencode_cli

    attempts = 0

    def clone(_source: str, _ref: str, target: Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise SetupError.git_clone_failed()
        _write_plugin(target)

    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(opencode_cli, "clone_github_source", clone)
    monkeypatch.setattr(opencode_cli, "_checkout_commit", lambda _target: "a" * 40)

    current = opencode_cli._materialize_remote_checkout("owner/repo", "master")
    with pytest.raises(SetupError):
        opencode_cli._materialize_remote_checkout("owner/repo", "master")

    assert (current / "integrations" / "opencode" / "plugins" / "powercontext" / "package.json").is_file()
    assert not list(current.parent.glob(".checkout-*"))


def test_opencode_skill_refresh_replaces_only_an_owned_installation(tmp_path: Path) -> None:
    import powercontext.cli.opencode as opencode_cli

    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "SKILL.md").write_text("first\n", encoding="utf-8")
    (second / "SKILL.md").write_text("second\n", encoding="utf-8")
    target = tmp_path / "config" / "skills" / "project-context"

    opencode_cli._install_skill(first, target)
    opencode_cli._install_skill(second, target)

    assert (target / "SKILL.md").read_text(encoding="utf-8") == "second\n"
    assert not list(target.parent.glob(".project-context.*"))


def test_setup_opencode_refuses_unowned_skill(tmp_path: Path, monkeypatch) -> None:
    import powercontext.cli.opencode as opencode_cli

    checkout = tmp_path / "checkout"
    plugin = _write_plugin(checkout)
    config = tmp_path / "config"
    target = config / "skills" / "project-context"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("user-owned\n", encoding="utf-8")
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(opencode_cli, "which", lambda _name: "/usr/bin/opencode")
    monkeypatch.setattr(opencode_cli, "_run_opencode", _fake_opencode(plugin, config))

    result = CliRunner().invoke(create_cli([setup_app]), ["setup", "opencode", "--source", str(checkout)])

    assert result.exit_code == 1
    assert "is not owned by PowerContext" in result.output
    assert (target / "SKILL.md").read_text(encoding="utf-8") == "user-owned\n"


def test_setup_opencode_requires_built_bundle(tmp_path: Path, monkeypatch) -> None:
    import powercontext.cli.opencode as opencode_cli

    checkout = tmp_path / "checkout"
    plugin = _write_plugin(checkout, built=False)
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(opencode_cli, "which", lambda _name: "/usr/bin/opencode")
    monkeypatch.setattr(opencode_cli, "_run_opencode", _fake_opencode(plugin, tmp_path / "config"))

    result = CliRunner().invoke(create_cli([setup_app]), ["setup", "opencode", "--source", str(checkout)])

    assert result.exit_code == 1
    assert "lib/index.js" in result.output


def test_setup_opencode_rejects_unsupported_version(tmp_path: Path, monkeypatch) -> None:
    import powercontext.cli.opencode as opencode_cli

    checkout = tmp_path / "checkout"
    _write_plugin(checkout)
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.setattr(opencode_cli, "which", lambda _name: "/usr/bin/opencode")
    monkeypatch.setattr(opencode_cli, "_run_opencode", lambda *_arguments: "1.17.9\n")

    result = CliRunner().invoke(create_cli([setup_app]), ["setup", "opencode", "--source", str(checkout)])

    assert result.exit_code == 1
    assert "requires OpenCode v1.18.21" in result.output


def test_doctor_opencode_reports_plugin_and_skill(tmp_path: Path, monkeypatch) -> None:
    import powercontext.cli.opencode as opencode_cli

    plugin = _write_plugin(tmp_path / "checkout")
    config = tmp_path / "config"
    skill = config / "skills" / "project-context"
    opencode_cli._install_skill(plugin / "skills" / "project-context", skill)
    monkeypatch.setattr(opencode_cli, "which", lambda _name: "/usr/bin/opencode")
    monkeypatch.setattr(opencode_cli, "_run_opencode", _fake_opencode(plugin, config))

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "opencode", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["checks"]["plugin"]["status"] == "ok"
    assert payload["checks"]["skill"]["status"] == "ok"


def test_doctor_opencode_rejects_configured_but_inactive_plugin(tmp_path: Path, monkeypatch) -> None:
    import powercontext.cli.opencode as opencode_cli

    plugin = _write_plugin(tmp_path / "checkout")
    config = tmp_path / "config"
    skill = config / "skills" / "project-context"
    opencode_cli._install_skill(plugin / "skills" / "project-context", skill)
    monkeypatch.setattr(opencode_cli, "which", lambda _name: "/usr/bin/opencode")

    def inactive(*arguments: str, env: dict[str, str] | None = None) -> str:
        del env
        if arguments == ("--version",):
            return "1.18.21\n"
        if arguments == ("debug", "paths"):
            return f"config     {config}\n"
        if arguments == ("debug", "config"):
            return json.dumps({"plugin": [plugin.as_uri()]})
        raise AssertionError(arguments)

    monkeypatch.setattr(opencode_cli, "_run_opencode", inactive)

    result = CliRunner().invoke(create_cli([doctor_app]), ["doctor", "opencode", "--json"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["checks"]["plugin"]["status"] == "failed"
    assert "did not activate" in payload["checks"]["plugin"]["detail"]
