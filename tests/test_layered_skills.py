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
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from integration_guidance_skills import FILE_HOSTS, file_skill  # noqa: E402


@pytest.mark.parametrize("host", FILE_HOSTS)
def test_shipped_skill_entry_can_load_its_referenced_workflows(host: str) -> None:
    skill = file_skill(ROOT / FILE_HOSTS[host])
    assert skill["name"] == "powercontext-project-context"
    for resource, content in skill["resources"].items():
        assert content.strip(), f"{host}: installed resource {resource} is empty"


def test_missing_skill_resource_identifies_entry_and_exact_link(tmp_path: Path) -> None:
    (tmp_path / "SKILL.md").write_text("[Memory](references/memory.md)", encoding="utf-8")
    with pytest.raises(ValueError, match=r"SKILL.md links to missing.*references/memory.md"):
        file_skill(tmp_path)


@pytest.mark.parametrize("host", ["opencode", "openclaw"])
def test_npm_archive_contains_every_reachable_skill_resource(host: str) -> None:
    npm, node = shutil.which("npm"), shutil.which("node")
    if not npm or not node:
        pytest.skip("Node/npm are required for package archive validation")
    cli = Path(npm).resolve()
    if cli.suffix != ".js":
        cli = cli.parent / "node_modules/npm/bin/npm-cli.js"
    if not cli.is_file():
        pytest.skip("npm CLI entrypoint is not available")
    directory = ROOT / FILE_HOSTS[host]
    package = directory.parent.parent
    completed = subprocess.run(
        [node, str(cli), "pack", "--dry-run", "--json", "--ignore-scripts"],
        cwd=package,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    paths = {file["path"] for file in json.loads(completed.stdout)[0]["files"]}
    for resource in file_skill(directory)["resources"]:
        expected = (directory.relative_to(package) / resource).as_posix()
        assert expected in paths, f"{host} package omits Skill resource {expected}"
