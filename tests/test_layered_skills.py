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

import asyncio
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from integration_guidance_skills import FILE_HOSTS, SkillReadingModel, file_skill  # noqa: E402


@pytest.mark.parametrize("host", FILE_HOSTS)
def test_shipped_skill_entry_can_load_its_referenced_workflows(host: str) -> None:
    skill = file_skill(ROOT / FILE_HOSTS[host])
    assert skill["resources"]["SKILL.md"] == skill["content"]
    assert any(key.startswith("references/") for key in skill["resources"])
    assert skill["description"].isascii() is False


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


def test_model_reads_only_selected_detail_and_missing_resources_fail_precisely() -> None:
    class Model:
        async def complete(self, messages, tools):
            if len(messages) == 1:
                return {
                    "tool_calls": [
                        {
                            "id": "read",
                            "function": {
                                "name": "read_skill_resource",
                                "arguments": '{"resource":"memory"}',
                            },
                        }
                    ]
                }
            assert "memory procedure" in json.dumps(messages)
            assert "unrelated handoff procedure" not in json.dumps(messages)
            return {"content": "Read the requested memory workflow."}

    model = SkillReadingModel(
        Model(), {"memory": "memory procedure", "handoff": "unrelated handoff procedure"}, available=True
    )
    result = asyncio.run(model.complete([{"role": "user", "content": "Read memory guidance"}], []))
    assert result["content"]
    assert [step["result"]["content"] for step in model.steps] == ["memory procedure"]
    missing = SkillReadingModel(Model(), {}, available=True)
    with pytest.raises(ValueError, match="Skill read 'memory': no such packaged or registered resource"):
        asyncio.run(missing.complete([{"role": "user", "content": "Read memory guidance"}], []))


@pytest.mark.parametrize("content", ["No metadata", "---\n- list\n---\nBody", "---\nname: [\n---\nBody"])
def test_invalid_skill_metadata_identifies_installed_entry(tmp_path: Path, content: str) -> None:
    (tmp_path / "SKILL.md").write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=f"Skill {tmp_path.name}: SKILL.md"):
        file_skill(tmp_path)


def test_rejected_skill_read_preserves_attempt_and_specific_cause() -> None:
    class Model:
        async def complete(self, messages, tools):
            return {
                "tool_calls": [
                    {"id": "read", "function": {"name": "read_skill_resource", "arguments": '{"resource":"memory"}'}},
                    {"id": "search", "function": {"name": "pc_search", "arguments": '{"query":"Aurora"}'}},
                ]
            }

    reader = SkillReadingModel(Model(), {"memory": "Memory workflow"}, available=True)
    with pytest.raises(ValueError, match=r"read_skill_resource: batched with \['pc_search'\]") as caught:
        asyncio.run(reader.complete([], []))
    record: dict[str, Any] = {"error": str(caught.value)}
    reader.record_into(record, "skill_search")
    assert "batched with" in record["error"]
    assert record["skill_reads"] == []
    assert record["skill_read_attempts"][0]["calls"][1]["function"]["name"] == "pc_search"
