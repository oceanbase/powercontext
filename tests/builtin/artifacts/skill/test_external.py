from pathlib import Path

import pytest

from powercontext.builtin.artifacts.skill import (
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
    (package / "linked").symlink_to(invalid, target_is_directory=True)

    scan = _provider(root).scan()

    assert scan.registrations == ()
    assert scan.skipped == 2


def test_codex_provider_requires_unique_stable_root_ids(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="root IDs"):
        CodexSkillProvider(
            host_id="workstation-1",
            roots=(
                CodexSkillRoot(root_id="repository", installation_scope="project", path=tmp_path),
                CodexSkillRoot(root_id="repository", installation_scope="user", path=tmp_path),
            ),
        )
