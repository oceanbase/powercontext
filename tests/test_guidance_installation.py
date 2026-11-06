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

"""Check complete filesystem installations, including side effects outside the Skill."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from powercontext.cli import authorization, hermes
from powercontext.cli.guidance import HOST_GUIDANCE, SKILL_DIRECTORY
from powercontext.cli.installation import setup as install_plugin
from powercontext.cli.system import SetupError


@pytest.mark.parametrize("host", ["hermes", "workbuddy"])
def test_rejected_upgrade_preserves_user_content_and_retry_succeeds(
    host: str, tmp_path: Path, monkeypatch, hermes_activation
) -> None:
    repo = Path(__file__).parents[1]
    descriptor = HOST_GUIDANCE[host]
    checkout = tmp_path / "source"
    source = checkout / descriptor.plugin
    shutil.copytree(repo / descriptor.plugin, source, ignore=shutil.ignore_patterns("node_modules", "__pycache__"))
    home = tmp_path / "home"
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.setenv(f"{host.upper()}_HOME", str(home))
    monkeypatch.setattr(authorization, "setup_authorization_value", lambda _host: None)
    if host == "hermes":
        companion = Path(descriptor.plugin).with_name("powercontext-command")
        shutil.copytree(repo / companion, checkout / companion)
        monkeypatch.setattr(hermes, "hermes_executable", lambda: "hermes")
        monkeypatch.setattr(hermes, "get_hermes_version", lambda _executable: "0.20.4")
        installed = home / "plugins/powercontext"
        payload = Path("__init__.py")
    else:
        installed = home
        payload = Path("hooks/workbuddy_powercontext_hook.py")
    install_plugin(host, source=str(checkout), ref="unused")
    note = home / SKILL_DIRECTORY / "personal.md"
    note.write_bytes(b"Keep my notes\r\n")
    skill = home / SKILL_DIRECTORY / "SKILL.md"
    instructions = b"\r\nKeep my personal instructions.\r\n"
    skill.write_bytes(skill.read_bytes() + instructions)
    before = {p.relative_to(home): p.read_bytes() for p in home.rglob("*") if p.is_file()}
    resource = source / SKILL_DIRECTORY / "references/work-handoff.md"
    original = resource.read_bytes()
    resource.unlink()
    updated_payload = (source / payload).read_bytes() + b"\n# updated package\n"
    (source / payload).write_bytes(updated_payload)
    with pytest.raises(SetupError):
        install_plugin(host, source=str(checkout), ref="unused")
    assert {p.relative_to(home): p.read_bytes() for p in home.rglob("*") if p.is_file()} == before
    resource.write_bytes(original)
    install_plugin(host, source=str(checkout), ref="unused")
    install_plugin(host, source=str(checkout), ref="unused")
    assert (installed / payload).read_bytes() == updated_payload
    assert note.read_bytes() == b"Keep my notes\r\n"
    assert skill.read_bytes().endswith(instructions)
