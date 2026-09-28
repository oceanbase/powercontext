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

"""Exercise installed Skill discovery through Hermes' native memory-provider loader."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
NATIVE_EXPORT = """
import json
from hermes_cli.plugins import get_plugin_manager
from plugins.memory import load_memory_provider

provider = load_memory_provider("powercontext", register_skills=False)
assert provider is not None, "Hermes failed to load the installed PowerContext provider"
manager = get_plugin_manager()
assert not manager.list_plugin_skill_metadata(), "Inactive provider exposed a Skill"
provider = load_memory_provider("powercontext")
assert provider is not None, "Hermes failed to load the active PowerContext provider"
metadata = manager.list_plugin_skill_metadata()
assert len(metadata) == 1, f"Expected one PowerContext Skill; native catalog: {metadata!r}"
skill = metadata[0]
path = manager.find_plugin_skill(skill["name"])
assert path is not None, f"Hermes cannot resolve registered Skill {skill['name']!r}"
skill["content"] = path.read_text(encoding="utf-8")
print(json.dumps({"host": "hermes", "guidance": provider.system_prompt_block(),
                  "tools": provider.get_tool_schemas(), "skill": skill}, ensure_ascii=False))
"""


@pytest.mark.parametrize("changed_description", [False, True], ids=["packaged", "updated-frontmatter"])
def test_native_discovery_exposes_installed_skill_description(tmp_path, changed_description):
    source = os.environ.get("POWERCONTEXT_HERMES_SOURCE")
    if not source:
        pytest.skip("Set POWERCONTEXT_HERMES_SOURCE to a Hermes source checkout; CI runs the pinned native host")
    source_path = Path(source).resolve()
    assert (source_path / "hermes_cli/plugins.py").is_file(), f"Not a Hermes source checkout: {source_path}"
    home = tmp_path / "hermes"
    plugin = home / "plugins/powercontext"
    shutil.copytree(
        ROOT / "integrations/hermes/plugins/powercontext", plugin, ignore=shutil.ignore_patterns("__pycache__")
    )
    skill_path = plugin / "skills/powercontext-project-context/SKILL.md"
    content = skill_path.read_text(encoding="utf-8")
    metadata = yaml.safe_load(content.split("---", 2)[1])
    if changed_description:
        # A future metadata edit must propagate without editing Python registration code.
        metadata["description"] = "Find project memory / 搜索项目记忆; hand off work / 交接工作."
        content = "---\n" + yaml.safe_dump(metadata, allow_unicode=True) + "---\n" + content.split("---", 2)[2]
        skill_path.write_text(content, encoding="utf-8")
    (home / "config.yaml").write_text("memory:\n  provider: powercontext\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-c", NATIVE_EXPORT],
        cwd=home,
        env={**os.environ, "HERMES_HOME": str(home), "PYTHONPATH": str(source_path), "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, f"Hermes native discovery failed:\n{result.stdout}\n{result.stderr}"
    catalog = json.loads(result.stdout)
    skill = catalog["skill"]
    assert skill["name"] == f"powercontext:{metadata['name']}"
    assert skill["description"] == metadata["description"], (
        f"{skill_path}: native discovery description differs from installed frontmatter: "
        f"expected {metadata['description']!r}, received {skill['description']!r}"
    )
    assert skill["content"] == content
    if not changed_description and (directory := os.environ.get("POWERCONTEXT_GUIDANCE_EXPORT")):
        (Path(directory) / "hermes.json").write_text(
            json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8"
        )
