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

import os
from pathlib import Path

import pytest

from powercontext.builtin.artifacts.skill import (
    AgentSkillProvider,
    AgentSkillTarget,
    CodexSkillProvider,
    CodexSkillRoot,
    ExternalSkillResolutionStatus,
)


def _write_skill(root: Path, name: str = "friendly-python") -> Path:
    package = root / name
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: Use when writing or refactoring Python code.\n"
        "---\n\n"
        "# Instructions\n\n"
        "Prefer explicit, readable boundaries.\n",
        encoding="utf-8",
    )
    return package


def _provider(root: Path) -> CodexSkillProvider:
    return CodexSkillProvider(
        host_id="workstation-1",
        roots=(
            CodexSkillRoot(
                root_id="repository",
                installation_scope="project",
                path=root,
            ),
        ),
    )


def test_codex_provider_discovers_and_exactly_resolves_a_local_package(tmp_path: Path) -> None:
    package = _write_skill(tmp_path / ".agents" / "skills")
    provider = _provider(package.parent)

    scan = provider.scan()

    assert scan.skipped == 0
    assert len(scan.registrations) == 1
    registration = scan.registrations[0]
    assert registration.external_skill_id == "codex:project:repository/friendly-python"
    assert registration.locator == str(package)
    assert registration.name == "friendly-python"
    assert len(registration.fingerprint) == 64
    resolution = provider.resolve(registration)
    assert resolution.status is ExternalSkillResolutionStatus.AVAILABLE
    assert resolution.entrypoint == str(package / "SKILL.md")


def test_exact_resolution_rejects_content_drift_until_rescan(tmp_path: Path) -> None:
    package = _write_skill(tmp_path / ".agents" / "skills")
    provider = _provider(package.parent)
    registration = provider.scan().registrations[0]
    (package / "references").mkdir()
    (package / "references" / "review.md").write_text("New package content.\n", encoding="utf-8")

    stale = provider.resolve(registration)
    refreshed = provider.scan().registrations[0]

    assert stale.status is ExternalSkillResolutionStatus.UNAVAILABLE
    assert stale.entrypoint is None
    assert refreshed.external_skill_id == registration.external_skill_id
    assert refreshed.fingerprint != registration.fingerprint
    assert provider.resolve(refreshed).status is ExternalSkillResolutionStatus.AVAILABLE


def test_resolution_is_bound_to_the_configured_host_and_root(tmp_path: Path) -> None:
    package = _write_skill(tmp_path / ".agents" / "skills")
    provider = _provider(package.parent)
    registration = provider.scan().registrations[0]

    assert (
        provider.resolve(registration.model_copy(update={"host_id": "workstation-2"})).status
        is ExternalSkillResolutionStatus.UNAVAILABLE
    )
    assert (
        provider.resolve(registration.model_copy(update={"locator": str(tmp_path)})).status
        is ExternalSkillResolutionStatus.UNAVAILABLE
    )


def test_scan_skips_invalid_or_symlinked_packages(tmp_path: Path) -> None:
    root = tmp_path / ".agents" / "skills"
    package = _write_skill(root)
    invalid = root / "missing-frontmatter"
    invalid.mkdir()
    (invalid / "SKILL.md").write_text("# Missing frontmatter\n", encoding="utf-8")
    symlink_created = False
    try:
        (package / "linked").symlink_to(invalid, target_is_directory=True)
    except OSError as error:
        # Windows requires SeCreateSymbolicLinkPrivilege (WinError 1314) for this test fixture.
        # Enable Windows Developer Mode or run the test as Administrator to gain this privilege.

        if os.name != "nt" or getattr(error, "winerror", None) != 1314:
            raise
    else:
        symlink_created = True

    scan = _provider(root).scan()

    expected_skipped = 2 if symlink_created else 1
    assert scan.skipped == expected_skipped
    if symlink_created:
        assert scan.registrations == ()
    else:
        assert [registration.name for registration in scan.registrations] == ["friendly-python"]


def test_codex_provider_requires_unique_stable_root_ids(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="target IDs"):
        CodexSkillProvider(
            host_id="workstation-1",
            roots=(
                CodexSkillRoot(root_id="repository", installation_scope="project", path=tmp_path),
                CodexSkillRoot(root_id="repository", installation_scope="user", path=tmp_path),
            ),
        )


def test_agent_provider_discovers_codex_and_claude_code_targets(tmp_path: Path) -> None:
    codex_package = _write_skill(tmp_path / ".agents" / "skills", "codex-review")
    claude_package = tmp_path / ".claude" / "skills" / "claude-review"
    claude_package.mkdir(parents=True)
    (claude_package / "SKILL.md").write_text(
        "---\ndescription: Review a change with Claude Code.\n---\n\nReview the change.\n",
        encoding="utf-8",
    )
    provider = AgentSkillProvider(
        host_id="workstation-1",
        targets=(
            AgentSkillTarget(
                target_id="codex-project",
                agent_kind="codex",
                installation_scope="project",
                path=codex_package.parent,
            ),
            AgentSkillTarget(
                target_id="claude-project",
                agent_kind="claude_code",
                installation_scope="project",
                path=claude_package.parent,
            ),
        ),
    )

    scan = provider.scan()

    assert scan.skipped == 0
    assert [registration.external_skill_id for registration in scan.registrations] == [
        "codex:project:codex-project/codex-review",
        "claude_code:project:claude-project/claude-review",
    ]
    assert scan.registrations[1].name == "claude-review"
    assert scan.registrations[1].provider == "claude_code"
    assert provider.resolve(scan.registrations[1]).entrypoint == str(claude_package / "SKILL.md")
