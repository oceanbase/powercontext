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

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

INSTALL_PAGES = (
    "README.md",
    "README_CN.md",
    "README_JP.md",
    "docs/en/docs/get-started/quickstart.md",
    "docs/zh/docs/get-started/quickstart.md",
    "docs/en/docs/get-started/install-and-run.md",
    "docs/zh/docs/get-started/install-and-run.md",
    "integrations/dsh/plugins/powercontext/README.md",
)
HISTORICAL_AND_INDEPENDENT_FILES = (
    "docs/en/docs/operate/artifact-processing-migration.md",
    "docs/zh/docs/operate/artifact-processing-migration.md",
    "website/src/lib/releases.ts",
    "integrations/agent-plugin/powercontext/plugin.json",
    "integrations/openclaw/plugins/memory-powercontext/openclaw.plugin.json",
)


@pytest.fixture
def release_repo(tmp_path: Path) -> Path:
    source = Path(__file__).parents[1]
    for relative in (
        "scripts/release_version.py",
        "openapi/powercontext.yaml",
        *INSTALL_PAGES,
        *HISTORICAL_AND_INDEPENDENT_FILES,
    ):
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, destination)
    return tmp_path


def run_release(repo: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.pop("VERSION", None)
    return subprocess.run(
        [sys.executable, str(repo / "scripts/release_version.py"), *arguments],
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def snapshot(repo: Path) -> dict[Path, bytes]:
    return {path.relative_to(repo): path.read_bytes() for path in repo.rglob("*") if path.is_file()}


def test_release_updates_installation_references_and_preserves_history(release_repo: Path) -> None:
    preserved = {relative: (release_repo / relative).read_bytes() for relative in HISTORICAL_AND_INDEPENDENT_FILES}
    for version in ("1.2.0", "1.3.0a1", "1.3.0b1", "1.3.0rc1", "1.3.0", "1.2.0"):
        result = run_release(release_repo, "--write", "--version", version)
        assert result.returncode == 0, result.stderr
        assert f"  version: {version}\n" in (release_repo / "openapi/powercontext.yaml").read_text(encoding="utf-8")
        for relative in INSTALL_PAGES:
            content = (release_repo / relative).read_text(encoding="utf-8")
            assert set(re.findall(r"powercontext\[[^\]\n]+\]==([^\s\"`]+)", content)) == {version}, relative
            assert set(re.findall(r"powercontext-v([^\s\"`]+)", content)) == {version}, relative
            if relative.startswith(("README", "docs/")):
                assert f"PowerContext {version}" in content, relative
            if re.search(r"(?:a|b|rc)[0-9]+$", version):
                assert "stable release" not in content, relative
                assert f"stable PowerContext {version}" not in content, relative
                assert "正式版本" not in content, relative
                assert "正式リリース" not in content, relative

        english = (release_repo / "docs/en/docs/get-started/install-and-run.md").read_text(encoding="utf-8")
        chinese = (release_repo / "docs/zh/docs/get-started/install-and-run.md").read_text(encoding="utf-8")
        assert f"package `{version}`" in english
        assert f"To upgrade to {version}:" in english
        assert f"Python 包版本为 `{version}`" in chinese
        assert f"升级到 {version}" in chinese
        assert "Version 1.1.0 upgrades legacy" in english
        assert "1.0.0 also need" in english
        assert "1.1.0 会在 Server 启动时升级旧标签表约束" in chinese
        assert "对于 1.0.0 之前的数据库" in chinese
        dsh = (release_repo / "integrations/dsh/plugins/powercontext/README.md").read_text(encoding="utf-8")
        assert "Release 1.1.0 includes direct-operation Scope failure handling" in dsh
        for relative, original in preserved.items():
            assert (release_repo / relative).read_bytes() == original, relative

        before = snapshot(release_repo)
        for arguments in (
            ("--write", "--version", version),
            ("--check",),
            ("--check", "--version", f"powercontext-v{version}"),
            ("--check", "--version", f"v{version}"),
        ):
            result = run_release(release_repo, *arguments)
            assert result.returncode == 0, result.stderr
            assert snapshot(release_repo) == before


def test_release_check_detects_and_write_repairs_one_stale_installation_command(release_repo: Path) -> None:
    result = run_release(release_repo, "--write", "--version", "1.2.0")
    assert result.returncode == 0, result.stderr
    expected = snapshot(release_repo)
    page = release_repo / "docs/zh/docs/get-started/install-and-run.md"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "powercontext[cli,server]==1.2.0", "powercontext[cli,server]==1.1.0", 1
        ),
        encoding="utf-8",
    )
    drifted = snapshot(release_repo)

    result = run_release(release_repo, "--check")
    assert result.returncode == 1
    assert "docs/zh/docs/get-started/install-and-run.md" in result.stderr
    assert snapshot(release_repo) == drifted

    result = run_release(release_repo, "--write", "--version", "powercontext-v1.2.0")
    assert result.returncode == 0, result.stderr
    assert snapshot(release_repo) == expected

    result = run_release(release_repo, "--check", "--version", "powercontext-v1.3.0")
    assert result.returncode == 1
    assert "openapi/powercontext.yaml" in result.stderr
    assert snapshot(release_repo) == expected


@pytest.mark.parametrize("version", ["1.3", "1.3.0-rc.1", "1.3.0+local", "1.3.0.dev1", "powercontext-vv1.2.0"])
def test_invalid_release_version_leaves_all_files_unchanged(release_repo: Path, version: str) -> None:
    before = snapshot(release_repo)
    result = run_release(release_repo, "--write", "--version", version)
    assert result.returncode != 0
    assert "VERSION" in result.stderr
    assert snapshot(release_repo) == before


@pytest.mark.parametrize("version", ["01.1.0", "1.1.0rc01", "1.1.0.post1", "1.1.0.dev1"])
def test_one_malformed_installation_pin_is_rejected_without_partial_updates(release_repo: Path, version: str) -> None:
    result = run_release(release_repo, "--write", "--version", "1.2.0")
    assert result.returncode == 0, result.stderr
    page = release_repo / "docs/zh/docs/get-started/install-and-run.md"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            "powercontext[cli,server]==1.2.0", f"powercontext[cli,server]=={version}", 1
        ),
        encoding="utf-8",
    )
    before = snapshot(release_repo)

    for arguments in (("--check",), ("--write", "--version", "1.3.0")):
        result = run_release(release_repo, *arguments)
        assert result.returncode != 0
        assert snapshot(release_repo) == before


def test_missing_release_reference_prevents_partial_update(release_repo: Path) -> None:
    page = release_repo / "integrations/dsh/plugins/powercontext/README.md"
    page.write_text(page.read_text(encoding="utf-8").replace("powercontext-v", "revision-v"), encoding="utf-8")
    before = snapshot(release_repo)

    result = run_release(release_repo, "--write", "--version", "9.9.9")
    assert result.returncode != 0
    assert "integrations/dsh/plugins/powercontext/README.md" in result.stderr
    assert snapshot(release_repo) == before
