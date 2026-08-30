# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from pathlib import Path

import pytest
from pydantic import SecretStr

import powercontext.client.receiver_service as service_module
from powercontext.client.receiver_service import (
    ReceiverServiceError,
    install_systemd_user_service,
    uninstall_systemd_user_service,
)
from powercontext.client.skill_receiver import RemoteSkillReceiverConfig


def _config(tmp_path: Path) -> RemoteSkillReceiverConfig:
    return RemoteSkillReceiverConfig(
        server_url="https://powercontext.example.com",
        target_id="codex-a",
        credential=SecretStr("pct_target.super-secret-value"),
        agent_kind="codex",
        workspace=tmp_path / "project with space",
    )


def test_systemd_user_service_is_secret_free_target_scoped_and_reversible(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config = _config(tmp_path)
    config_file = tmp_path / "project with space/.powercontext/remote-skill-target.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("credential stays here", encoding="utf-8")
    config_file.chmod(0o600)
    commands: list[tuple[str, ...]] = []
    executables = {
        "powercontext": Path("/opt/power context/bin/powercontext"),
        "systemctl": Path("/usr/bin/systemctl"),
    }
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(service_module, "_required_executable", executables.__getitem__)
    monkeypatch.setattr(
        service_module,
        "_run_systemctl",
        lambda _systemctl, *arguments: commands.append(arguments),
    )

    installation = install_systemd_user_service(config_file, config, interval_seconds=3)
    contents = installation.unit_path.read_text(encoding="utf-8")

    assert installation.unit_name == "powercontext-skill-receiver-codex-a.service"
    assert "remote-watch" in contents
    assert '"--interval" "3"' in contents
    assert str(config_file) in contents
    assert "WorkingDirectory=" not in contents
    assert config.credential.get_secret_value() not in contents
    assert commands == [("daemon-reload",), ("enable", "--now", installation.unit_name)]

    removed = uninstall_systemd_user_service(config.target_id)

    assert removed == installation
    assert not installation.unit_path.exists()
    assert commands[-2:] == [("disable", "--now", installation.unit_name), ("daemon-reload",)]


def test_systemd_user_service_uninstall_does_not_claim_an_absent_unit_was_stopped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(service_module, "_required_executable", lambda _name: Path("/usr/bin/systemctl"))

    with pytest.raises(ReceiverServiceError, match="does not exist"):
        uninstall_systemd_user_service("codex-a")


def test_systemd_user_service_uses_the_invoked_venv_entrypoint_when_it_is_not_on_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    config_file = tmp_path / "project with space/.powercontext/remote-skill-target.json"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("credential stays here", encoding="utf-8")
    entrypoint = tmp_path / "venv/bin/powercontext"
    entrypoint.parent.mkdir(parents=True)
    entrypoint.write_text("#!/bin/sh\n", encoding="utf-8")
    entrypoint.chmod(0o755)
    monkeypatch.setattr(service_module.sys, "argv", [str(entrypoint), "skill", "remote-service-install"])
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(
        service_module,
        "_required_executable",
        lambda name: (
            Path("/usr/bin/systemctl")
            if name == "systemctl"
            else pytest.fail("the invoked venv entrypoint should not require a PATH lookup")
        ),
    )
    monkeypatch.setattr(service_module, "_run_systemctl", lambda *_args: None)

    installation = install_systemd_user_service(config_file, config, interval_seconds=3)

    contents = installation.unit_path.read_text(encoding="utf-8")
    assert str(entrypoint) in contents
