import asyncio
from pathlib import Path

import pytest

from powercontext.builtin.artifacts.generation import ArtifactGenerationInput
from powercontext.builtin.artifacts.skill import (
    CodexSkillRoot,
    ExternalSkillRegistryUnavailableError,
    ExternalSkillResolutionStatus,
    ExternalSkillSnapshotUnavailableError,
    SkillContent,
)
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.review.generation import GenerationCapabilityUnavailableError
from powercontext.builtin.runtime import (
    BuiltinConfig,
    ExternalSkillsConfig,
    ImportExternalSkillRequest,
    ListExternalSkillsRequest,
    ResolveExternalSkillRequest,
    open_builtin_runtime,
)
from powercontext.builtin.sources import ExternalSkillImportMode


def _write_skill(root: Path) -> Path:
    package = root / "friendly-python"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\nname: friendly-python\ndescription: Use when writing Python.\n---\n\nKeep boundaries explicit.\n",
        encoding="utf-8",
    )
    return package


def test_runtime_exposes_configured_external_skill_registry_without_a_model(tmp_path: Path) -> None:
    async def exercise() -> None:
        root = tmp_path / ".agents" / "skills"
        package = _write_skill(root)
        database = tmp_path / "powercontext.db"
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"),
            external_skills=ExternalSkillsConfig(
                host_id="workstation-1",
                codex_roots=(
                    CodexSkillRoot(
                        root_id="repository",
                        installation_scope="project",
                        path=root,
                    ),
                ),
            ),
        )
        async with open_builtin_runtime(config) as runtime:
            capabilities = await runtime.capabilities()
            assert capabilities.external_skill_registry is True
            assert capabilities.experience_generation is False
            assert capabilities.managed_skill_generation is False

            scoped = runtime.external_skills.for_scope("project:example")
            registration = (await scoped.scan()).registrations[0]
            available = await scoped.list(ListExternalSkillsRequest())
            assert len(available) == 1
            assert available[0].status is ExternalSkillResolutionStatus.AVAILABLE
            exact = await scoped.resolve(
                ResolveExternalSkillRequest(
                    external_skill_id=registration.external_skill_id,
                    fingerprint=registration.fingerprint,
                )
            )
            assert exact.entrypoint == str(package / "SKILL.md")

    asyncio.run(exercise())


def test_runtime_reports_unconfigured_external_skill_registry(tmp_path: Path) -> None:
    async def exercise() -> None:
        database = tmp_path / "powercontext.db"
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"))
        async with open_builtin_runtime(config) as runtime:
            assert (await runtime.capabilities()).external_skill_registry is False
            with pytest.raises(ExternalSkillRegistryUnavailableError):
                await runtime.external_skills.for_scope("project:example").scan()

    asyncio.run(exercise())


class _SkillGenerator:
    def __init__(self) -> None:
        self.inputs: list[ArtifactGenerationInput] = []

    async def generate(self, value: ArtifactGenerationInput, /) -> SkillContent:
        self.inputs.append(value)
        return SkillContent(
            name="friendly-python-managed",
            description="Use when writing reviewed Python changes.",
            instructions="Keep boundaries explicit and validate observable behavior.",
            validation=("Focused behavior tests pass.",),
        )


def test_explicit_external_skill_import_captures_exact_snapshot_and_enters_review(tmp_path: Path) -> None:
    async def exercise() -> None:
        root = tmp_path / ".agents" / "skills"
        _write_skill(root)
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'powercontext.db'}"),
            external_skills=ExternalSkillsConfig(
                host_id="workstation-1",
                codex_roots=(CodexSkillRoot(root_id="repository", installation_scope="project", path=root),),
            ),
        )
        generator = _SkillGenerator()
        async with open_builtin_runtime(config, skill_generator=generator) as runtime:
            scoped = runtime.external_skills.for_scope("project:example")
            registration = (await scoped.scan()).registrations[0]

            result = await scoped.import_managed(
                ImportExternalSkillRequest(
                    external_skill_id=registration.external_skill_id,
                    fingerprint=registration.fingerprint,
                    mode=ExternalSkillImportMode.IMPORT,
                    reason="Govern an explicitly selected local package.",
                )
            )

            assert result.candidate is not None
            assert result.candidate.family == "skill"
            assert result.candidate.sources[0].source_type == "external-skill-snapshot"
            assert result.candidate.artifacts == ()
            assert registration.fingerprint in generator.inputs[0].evidence[0].content
            assert "Keep boundaries explicit." in generator.inputs[0].evidence[0].content
            assert (await scoped.list(ListExternalSkillsRequest()))[0].registration == registration

    asyncio.run(exercise())


def test_external_skill_import_requires_generation_model_before_snapshot(tmp_path: Path) -> None:
    async def exercise() -> None:
        root = tmp_path / ".agents" / "skills"
        _write_skill(root)
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'powercontext.db'}"),
            external_skills=ExternalSkillsConfig(
                host_id="workstation-1",
                codex_roots=(CodexSkillRoot(root_id="repository", installation_scope="project", path=root),),
            ),
        )
        async with open_builtin_runtime(config) as runtime:
            scoped = runtime.external_skills.for_scope("project:example")
            registration = (await scoped.scan()).registrations[0]

            with pytest.raises(GenerationCapabilityUnavailableError):
                await scoped.import_managed(
                    ImportExternalSkillRequest(
                        external_skill_id=registration.external_skill_id,
                        fingerprint=registration.fingerprint,
                        mode=ExternalSkillImportMode.FORK,
                    )
                )

    asyncio.run(exercise())


def test_external_skill_import_rejects_package_drift_before_generation(tmp_path: Path) -> None:
    async def exercise() -> None:
        root = tmp_path / ".agents" / "skills"
        package = _write_skill(root)
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'powercontext.db'}"),
            external_skills=ExternalSkillsConfig(
                host_id="workstation-1",
                codex_roots=(CodexSkillRoot(root_id="repository", installation_scope="project", path=root),),
            ),
        )
        generator = _SkillGenerator()
        async with open_builtin_runtime(config, skill_generator=generator) as runtime:
            scoped = runtime.external_skills.for_scope("project:example")
            registration = (await scoped.scan()).registrations[0]
            manifest = package / "SKILL.md"
            manifest.write_text(manifest.read_text(encoding="utf-8") + "\nChanged.\n", encoding="utf-8")

            with pytest.raises(ExternalSkillSnapshotUnavailableError):
                await scoped.import_managed(
                    ImportExternalSkillRequest(
                        external_skill_id=registration.external_skill_id,
                        fingerprint=registration.fingerprint,
                        mode=ExternalSkillImportMode.IMPORT,
                    )
                )

            assert generator.inputs == []

    asyncio.run(exercise())
