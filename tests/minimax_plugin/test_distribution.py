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

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from build_minimax_plugin import ROOT, build_distribution, write_or_check


def test_checked_in_distribution_is_complete_and_current() -> None:
    files = build_distribution()
    write_or_check(ROOT / "integrations/minimax/plugins/powercontext", files, check=True)
    manifest = json.loads(files[".minimax-plugin/plugin.json"])
    for path in [manifest["icon"], *manifest["skills"], *manifest["mcpServers"], *manifest["apps"]]:
        assert path in files
    assert manifest["name"] == "powercontext"
    assert manifest["skills"] == ["skills/powercontext-project-context/SKILL.md"]
    assert manifest["apps"] == []
    assert not {"hooks", "darkIcon", "deliveryTargets", "installationPolicy", "listed"} & manifest.keys()


def test_check_reports_changed_and_unexpected_package_files(tmp_path: Path) -> None:
    files = build_distribution()
    write_or_check(tmp_path, files, check=False)
    path = tmp_path / "README.md"
    path.write_text("Local modification\n", encoding="utf-8")
    with pytest.raises(ValueError, match="drifted"):
        write_or_check(tmp_path, files, check=True)
    assert path.read_text(encoding="utf-8") == "Local modification\n"
    write_or_check(tmp_path, files, check=False)
    (tmp_path / "unexpected.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unexpected"):
        write_or_check(tmp_path, files, check=True)


@pytest.fixture
def plugin_sources(tmp_path: Path) -> Path:
    profile_path = Path("integrations/minimax/target.json")
    profile = json.loads((ROOT / profile_path).read_text(encoding="utf-8"))
    shutil.copytree(ROOT / profile["source"], tmp_path / profile["source"])
    for source in [profile_path, *(Path(value) for value in profile["files"].values())]:
        target = tmp_path / source
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / source, target)
    return tmp_path


def test_minimax_release_version_is_independent_of_portable_release(plugin_sources: Path) -> None:
    path = plugin_sources / "integrations/minimax/target.json"
    profile = json.loads(path.read_text(encoding="utf-8"))
    portable_manifest = plugin_sources / profile["source"] / "plugin.json"
    original = portable_manifest.read_bytes()
    profile["version"] = "2.3.4"
    path.write_text(json.dumps(profile), encoding="utf-8")
    files = build_distribution(plugin_sources)
    assert json.loads(files[".minimax-plugin/plugin.json"])["version"] == "2.3.4"
    assert portable_manifest.read_bytes() == original


def test_projection_rejects_credentials_from_portable_mcp(plugin_sources: Path) -> None:
    path = plugin_sources / "integrations/agent-plugin/powercontext/mcp.json"
    configuration = json.loads(path.read_text(encoding="utf-8"))
    configuration["mcpServers"]["powercontext"]["headers"] = {"Authorization": "Bearer local-test-only"}
    path.write_text(json.dumps(configuration), encoding="utf-8")
    with pytest.raises(ValueError, match="unauthenticated"):
        build_distribution(plugin_sources)


def test_real_minimax_host_discovers_marketplace_package(tmp_path: Path) -> None:
    executable = shutil.which("mcode")
    if executable is None:
        pytest.skip("MiniMax Code is not installed")
    data = tmp_path / "minimax"
    write_or_check(data / "plugins/powercontext", build_distribution(), check=False)
    result = subprocess.run(
        [executable, "plugin", "list", "--marketplace", "local", "--json"],
        env={**os.environ, "MINIMAX_DATA_DIR": str(data)},
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=True,
    )
    plugin = next(item for item in json.loads(result.stdout)["installed"] if item["name"] == "powercontext")
    assert plugin["enabled"]
    assert plugin["capabilities"] == {"appCount": 0, "mcpServerCount": 1, "skillCount": 1}
