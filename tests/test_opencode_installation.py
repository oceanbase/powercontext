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

"""The public installer must not leave mixed versions after a rejected upgrade."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from powercontext.cli import authorization, opencode
from powercontext.cli.guidance import HOST_GUIDANCE
from powercontext.cli.installation import setup as install_plugin
from powercontext.cli.system import SetupError


def _package(root: Path, version: str) -> Path:
    package = root / version
    (package / "lib").mkdir(parents=True)
    (package / "package.json").write_text('{"name":"powercontext-opencode"}', encoding="utf-8")
    for name in ("index.js", "tui.js"):
        (package / "lib" / name).write_text(f"export default {{version: '{version}'}};\n", encoding="utf-8")
    shutil.copytree(
        Path(__file__).parents[1] / HOST_GUIDANCE["agent-plugin"].skill, package / opencode.OPENCODE_SKILL.parent
    )
    entry = package / opencode.OPENCODE_SKILL
    entry.write_text(
        entry.read_text(encoding="utf-8").replace("description: ", f"description: {version} "), encoding="utf-8"
    )
    asset = entry.parent / "assets/example.txt"
    asset.parent.mkdir()
    asset.write_text(f"Example for {version}.\n", encoding="utf-8")
    return package


def _snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.fixture(params=[False, True], ids=["fresh", "upgrade"])
def installation(request, tmp_path: Path, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    (config / "unrelated.json").write_bytes(b'{"keep": true}\r\n')
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(config))
    monkeypatch.setattr(opencode, "opencode_executable", lambda: "opencode")
    monkeypatch.setattr(opencode, "_version", lambda: "1.18.21")
    monkeypatch.setattr(opencode, "opencode_config_dir", lambda: config)
    monkeypatch.setattr(authorization, "setup_authorization_value", lambda _host: None)
    if request.param:
        old = _package(tmp_path, "old")
        install_plugin("opencode", source=str(str(old)), ref="unused")
        skill = config / "skills/powercontext-project-context"
        (skill / "personal.md").write_bytes(b"My personal instructions\r\n")
        entry = skill / "SKILL.md"
        entry.write_bytes(entry.read_bytes() + b"\r\nMy appended notes\r\n")
        (config / "plugins/powercontext-opencode-tui.js").write_bytes(b"old legacy plugin\n")
    tui = config / "tui.json"
    payload = json.loads(tui.read_text(encoding="utf-8")) if tui.exists() else {}
    payload["theme"] = "user-theme"
    payload.setdefault("plugin", []).append("user-plugin")
    tui.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    return config, _package(tmp_path, "new"), request.param


def _assert_success(config: Path, package: Path, upgrade: bool) -> None:
    assert (config / "plugins/powercontext-opencode.js").read_bytes() == (package / "lib/index.js").read_bytes()
    tui = json.loads((config / "tui.json").read_text(encoding="utf-8"))
    assert str((package / "lib/tui.js").resolve()) in tui["plugin"]
    assert "user-plugin" in tui["plugin"]
    assert tui["theme"] == "user-theme"
    skill = config / "skills/powercontext-project-context"
    assert "description: new " in (skill / "SKILL.md").read_text(encoding="utf-8")
    assert (skill / "assets/example.txt").read_text(encoding="utf-8") == "Example for new.\n"
    assert not (config / "plugins/powercontext-opencode-tui.js").exists()
    if upgrade:
        assert (skill / "personal.md").read_bytes() == b"My personal instructions\r\n"
        assert (skill / "SKILL.md").read_bytes().endswith(b"\r\nMy appended notes\r\n")
    assert (config / "unrelated.json").read_bytes() == b'{"keep": true}\r\n'


def test_rejected_package_preserves_whole_installation_and_can_retry(installation) -> None:
    config, package, upgrade = installation
    before = _snapshot(config)
    source = package / opencode.OPENCODE_SKILL.parent
    path = source / "references/work-handoff.md"
    original = path.read_bytes()
    path.unlink()
    with pytest.raises(SetupError):
        install_plugin("opencode", source=str(str(package)), ref="unused")
    assert _snapshot(config) == before
    path.write_bytes(original)
    install_plugin("opencode", source=str(str(package)), ref="unused")
    _assert_success(config, package, upgrade)
    successful = _snapshot(config)
    install_plugin("opencode", source=str(str(package)), ref="unused")
    assert _snapshot(config) == successful


@pytest.mark.parametrize(
    "destination",
    [
        "plugins/.powercontext-opencode.json",
        "plugins/powercontext-opencode.js",
        "tui.json",
        "skills/powercontext-project-context",
    ],
)
def test_commit_failure_preserves_whole_installation_and_can_retry(installation, destination: str, monkeypatch) -> None:
    config, package, upgrade = installation
    before = _snapshot(config)
    real_replace = os.replace
    failed = False

    def fail_once(source, target):
        nonlocal failed
        if Path(target) == config / destination and not failed:
            failed = True
            message = "injected installation write failure"
            raise OSError(message)
        return real_replace(source, target)

    with monkeypatch.context() as fault:
        fault.setattr(os, "replace", fail_once)
        with pytest.raises(SetupError):
            install_plugin("opencode", source=str(str(package)), ref="unused")
    assert failed
    assert _snapshot(config) == before
    install_plugin("opencode", source=str(str(package)), ref="unused")
    _assert_success(config, package, upgrade)
