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

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from integration_guidance_skills import FILE_HOSTS, ROOT, file_skill, refresh_generated_guidance

from powercontext.cli.guidance import END, HOST_GUIDANCE, START, GuidanceError, merge_guidance, refresh_skill


@pytest.fixture
def guidance_tree(tmp_path: Path) -> Path:
    shutil.copytree(ROOT / "scripts/guidance_templates", tmp_path / "scripts/guidance_templates")
    for host, relative in FILE_HOSTS.items():
        shutil.copytree(ROOT / relative, tmp_path / relative)
        descriptor = HOST_GUIDANCE[host]
        for evidence in descriptor.script_evidence:
            destination = tmp_path / descriptor.plugin / evidence
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / descriptor.plugin / evidence, destination)
    shutil.copy2(ROOT / "integrations/capabilities.toml", tmp_path / "integrations/capabilities.toml")
    return tmp_path


def snapshot(root: Path) -> dict[Path, tuple[bytes, int]]:
    return {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in root.rglob("*") if path.is_file()}


def test_refresh_is_idempotent_and_preserves_outside_bytes(guidance_tree: Path) -> None:
    path = guidance_tree / FILE_HOSTS["claude-code"] / "SKILL.md"
    original = path.read_bytes()
    outside = b"\r\nMy private project notes must survive.\r\n"
    path.write_bytes(original + outside)
    before = snapshot(guidance_tree)
    assert refresh_generated_guidance(guidance_tree) == ()
    assert refresh_generated_guidance(guidance_tree) == ()
    assert snapshot(guidance_tree) == before
    assert path.read_bytes().endswith(outside)


def test_read_only_check_detects_drift_without_fixing_it(guidance_tree: Path) -> None:
    path = guidance_tree / FILE_HOSTS["pi"] / "references/work-handoff.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("# Work Handoff", "# Incorrect guidance"), encoding="utf-8"
    )
    before = snapshot(guidance_tree)
    assert refresh_generated_guidance(guidance_tree, check=True) == (path,)
    assert snapshot(guidance_tree) == before
    assert refresh_generated_guidance(guidance_tree) == (path,)
    assert refresh_generated_guidance(guidance_tree, check=True) == ()


def test_canonical_discovery_description_is_refreshed(guidance_tree: Path) -> None:
    template = guidance_tree / "scripts/guidance_templates/base/SKILL.md.j2"
    template.write_text(
        template.read_text(encoding="utf-8").replace("description: PowerContext", "description: Updated PowerContext"),
        encoding="utf-8",
    )
    refresh_generated_guidance(guidance_tree)
    assert file_skill(guidance_tree / FILE_HOSTS["claude-code"])["description"].startswith("Updated PowerContext")


def test_all_packaged_references_resolve_and_openclaw_keeps_its_limits(guidance_tree: Path) -> None:
    for relative in FILE_HOSTS.values():
        skill = file_skill(guidance_tree / relative)
        assert "references/scope-memory.md" in skill["resources"]
        assert "references/work-handoff.md" in skill["resources"]
    openclaw = file_skill(guidance_tree / FILE_HOSTS["openclaw"])
    assert "Unavailable here." in openclaw["content"]
    assert "`list_memory_entries`" not in openclaw["content"]
    assert "`list_artifact_candidates`" not in openclaw["content"]
    assert "references/review-publication.md" not in openclaw["resources"]


def test_unknown_host_variable_is_rejected_before_any_write(guidance_tree: Path) -> None:
    template = guidance_tree / "scripts/guidance_templates/base/SKILL.md.j2"
    template.write_text(template.read_text(encoding="utf-8") + "${UNKNOWN_ROOT}/script.py\n", encoding="utf-8")
    before = snapshot(guidance_tree)
    with pytest.raises(GuidanceError, match="UNKNOWN_ROOT"):
        refresh_generated_guidance(guidance_tree)
    assert snapshot(guidance_tree) == before


def test_broken_reference_is_rejected_before_refresh(guidance_tree: Path) -> None:
    template = guidance_tree / "scripts/guidance_templates/base/SKILL.md.j2"
    template.write_text(
        template.read_text(encoding="utf-8").replace("references/scope-memory.md", "references/missing.md"),
        encoding="utf-8",
    )
    before = snapshot(guidance_tree)
    with pytest.raises(ValueError, match="missing or out-of-package"):
        refresh_generated_guidance(guidance_tree)
    assert snapshot(guidance_tree) == before


def test_new_skill_directory_cannot_escape_generation_catalog(guidance_tree: Path) -> None:
    path = guidance_tree / "integrations/newhost/skills/powercontext-project-context/SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("new guidance", encoding="utf-8")
    with pytest.raises(GuidanceError, match="catalog drift"):
        refresh_generated_guidance(guidance_tree, check=True)


def test_capability_removal_rejects_stale_host_guidance(guidance_tree: Path) -> None:
    manifest = guidance_tree / "integrations/capabilities.toml"
    content = manifest.read_text(encoding="utf-8")
    marker = 'id = "claude-code"'
    before, after = content.split(marker, 1)
    after = after.replace('"candidate_review", ', "", 1)
    manifest.write_text(before + marker + after, encoding="utf-8")
    with pytest.raises(GuidanceError, match="undeclared capabilities"):
        refresh_generated_guidance(guidance_tree)


@pytest.mark.parametrize("content", [START, END, END + START, START + END + START + END])
def test_malformed_markers_never_overwrite_user_content(content: str) -> None:
    with pytest.raises(GuidanceError, match="marker pair"):
        merge_guidance(content, START + "\nnew\n" + END)


def test_only_pristine_legacy_guidance_is_automatically_migrated() -> None:
    legacy = "original\n"
    digest = hashlib.sha256(legacy.encode()).hexdigest()
    desired = START + "\nnew\n" + END
    assert merge_guidance(legacy, desired, legacy_sha256=digest) == desired
    with pytest.raises(GuidanceError, match="unrecognized content"):
        merge_guidance(legacy + "user edits\n", desired, legacy_sha256=digest)


def test_installed_skill_refresh_preserves_notes_extra_files_and_mtimes(tmp_path: Path) -> None:
    source = ROOT / FILE_HOSTS["opencode"]
    target = tmp_path / "installed"
    refresh_skill(source, target)
    skill = target / "SKILL.md"
    skill.write_bytes(skill.read_bytes() + b"\nUser-specific guidance\n")
    (target / "private-notes.md").write_text("keep me", encoding="utf-8")
    before = snapshot(target)
    refresh_skill(source, target)
    refresh_skill(source, target)
    assert snapshot(target) == before


@pytest.mark.parametrize("host", ["workbuddy", "opencode"])
def test_real_skill_installers_preserve_local_notes_on_repeat(tmp_path: Path, host: str) -> None:
    from powercontext.cli.opencode import _install_skill
    from powercontext.cli.workbuddy import _install_workbuddy_skill

    target = tmp_path / "skills/powercontext-project-context"

    def install() -> None:
        if host == "workbuddy":
            _install_workbuddy_skill(ROOT / HOST_GUIDANCE[host].plugin, target.parent, tmp_path / "hooks")
        else:
            _install_skill(ROOT / FILE_HOSTS[host], target)

    install()
    skill = target / "SKILL.md"
    skill.write_bytes(skill.read_bytes() + b"\nUser instructions outside the owned region.\n")
    (target / "my-notes.md").write_text("retain this file", encoding="utf-8")
    before = snapshot(target)
    install()
    assert snapshot(target) == before
    if host == "workbuddy":
        scope = (target / "references/scope-memory.md").read_text(encoding="utf-8")
        assert "${POWERCONTEXT_" not in scope


@pytest.mark.parametrize("host", ["hermes", "zcode"])
def test_staged_plugin_upgrade_keeps_user_guidance(tmp_path: Path, host: str) -> None:
    from powercontext.cli.guidance import SKILL_DIRECTORY, preserve_installed_guidance

    previous = tmp_path / "previous"
    staged = tmp_path / "staged"
    for destination in (previous, staged):
        shutil.copytree(ROOT / FILE_HOSTS[host], destination / SKILL_DIRECTORY)
    skill = previous / SKILL_DIRECTORY / "SKILL.md"
    skill.write_bytes(skill.read_bytes() + b"\nKeep these local instructions.\n")
    before = snapshot(previous)
    preserve_installed_guidance(previous, staged)
    assert (staged / SKILL_DIRECTORY / "SKILL.md").read_bytes() == skill.read_bytes()
    assert snapshot(previous) == before


@pytest.fixture
def pre_marker_packages(tmp_path: Path) -> dict[str, Path]:
    """Real pre-marker Skill bytes; unrelated plugin files supply the installation layout."""
    fixture = ROOT / "tests/fixtures/integration-guidance/pre-marker-skills.json"
    skills = json.loads(fixture.read_text(encoding="utf-8"))["skills"]
    packages = {}
    for host, files in skills.items():
        plugin = tmp_path / "old-packages" / host
        shutil.copytree(
            ROOT / HOST_GUIDANCE[host].plugin, plugin, ignore=shutil.ignore_patterns("skills", "__pycache__")
        )
        for name, content in files.items():
            destination = plugin / "skills/powercontext-project-context" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content.encode("utf-8"))
        packages[host] = plugin
    return packages


@pytest.mark.parametrize("host", ["workbuddy", "opencode", "hermes", "zcode"])
def test_pre_marker_downgrade_rejects_before_losing_user_content(
    tmp_path: Path, monkeypatch, pre_marker_packages: dict[str, Path], host: str
) -> None:
    from powercontext.cli.guidance import SKILL_DIRECTORY, preserve_installed_guidance
    from powercontext.cli.opencode import _install_skill
    from powercontext.cli.system import SetupError
    from powercontext.cli.workbuddy import install_workbuddy_plugin

    home = tmp_path / "installed"
    target = home / SKILL_DIRECTORY
    monkeypatch.setenv("WORKBUDDY_HOME", str(home))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    if host == "workbuddy":
        install_workbuddy_plugin(source=str(ROOT), ref="master")
    elif host == "opencode":
        _install_skill(ROOT / FILE_HOSTS[host], target)
    else:
        shutil.copytree(ROOT / FILE_HOSTS[host], target)
    skill = target / "SKILL.md"
    skill.write_bytes(skill.read_bytes() + b"\r\nUser notes must survive a downgrade.\r\n")
    (target / "my-notes.md").write_text("Keep my additional file.\n", encoding="utf-8")
    before = {path: data for path, (data, _) in snapshot(home).items()}
    with pytest.raises((GuidanceError, SetupError, OSError), match=r"unmarked|pre-marker"):
        if host == "workbuddy":
            install_workbuddy_plugin(source=str(pre_marker_packages[host]), ref="master")
        elif host == "opencode":
            _install_skill(pre_marker_packages[host] / SKILL_DIRECTORY, target)
        else:
            preserve_installed_guidance(home, pre_marker_packages[host])
    assert {path: data for path, (data, _) in snapshot(home).items()} == before


@pytest.mark.parametrize("edited", [False, True])
def test_workbuddy_legacy_upgrade_uses_previous_interpreter_without_accepting_edits(
    tmp_path: Path, monkeypatch, pre_marker_packages: dict[str, Path], edited: bool
) -> None:
    import powercontext.cli.workbuddy as workbuddy
    from powercontext.cli.system import SetupError

    home = tmp_path / "workbuddy home"
    monkeypatch.setenv("WORKBUDDY_HOME", str(home))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    old_python = (tmp_path / "old environment/python").as_posix()
    new_python = (tmp_path / "new environment/python3").as_posix()
    monkeypatch.setattr(workbuddy, "_python_executable", lambda: old_python)
    workbuddy.install_workbuddy_plugin(source=str(pre_marker_packages["workbuddy"]), ref="master")
    scope = home / "skills/powercontext-project-context/references/scope-memory.md"
    assert old_python in scope.read_text(encoding="utf-8")
    if edited:
        scope.write_bytes(scope.read_bytes() + b"\nRetain my unmarked edits.\n")
    before = {path: data for path, (data, _) in snapshot(home).items()}
    monkeypatch.setattr(workbuddy, "_python_executable", lambda: new_python)
    if edited:
        with pytest.raises(SetupError, match="unrecognized content"):
            workbuddy.install_workbuddy_plugin(source=str(ROOT), ref="master")
        assert {path: data for path, (data, _) in snapshot(home).items()} == before
    else:
        workbuddy.install_workbuddy_plugin(source=str(ROOT), ref="master")
        updated = scope.read_text(encoding="utf-8")
        assert new_python in updated
        assert old_python not in updated
        assert START in updated and END in updated


def test_git_crlf_checkout_passes_generation_without_rewriting_user_bytes(guidance_tree: Path) -> None:
    shutil.copy2(ROOT / ".gitattributes", guidance_tree / ".gitattributes")
    git = shutil.which("git")
    assert git is not None, "Git is required to exercise checkout conversion"
    for args in (
        ["init", "--quiet"],
        ["add", "."],
        ["checkout-index", "--all", "--prefix=checkout/"],
    ):
        subprocess.run(
            [git, "-c", "core.autocrlf=true", "-c", "core.safecrlf=false", *args],
            cwd=guidance_tree,
            check=True,
            capture_output=True,
        )
    checkout = guidance_tree / "checkout"
    skill = checkout / FILE_HOSTS["codex"] / "SKILL.md"
    skill.write_bytes(skill.read_bytes() + b"\r\nLocal notes retain CRLF.\r\n")
    before = snapshot(checkout)
    assert refresh_generated_guidance(checkout, check=True) == ()
    assert refresh_generated_guidance(checkout) == ()
    assert snapshot(checkout) == before


@pytest.mark.parametrize("host", ["hermes", "zcode"])
def test_unchanged_pre_marker_staging_keeps_additional_user_files(
    tmp_path: Path, pre_marker_packages: dict[str, Path], host: str
) -> None:
    from powercontext.cli.guidance import SKILL_DIRECTORY, preserve_installed_guidance

    staged = pre_marker_packages[host]
    previous = tmp_path / "previous"
    shutil.copytree(staged / SKILL_DIRECTORY, previous / SKILL_DIRECTORY)
    note = previous / SKILL_DIRECTORY / "personal/notes.md"
    note.parent.mkdir()
    note.write_bytes(b"Keep this extra file.\r\n")
    preserve_installed_guidance(previous, staged)
    assert (staged / SKILL_DIRECTORY / "personal/notes.md").read_bytes() == note.read_bytes()
