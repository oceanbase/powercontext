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

import asyncio
from pathlib import Path

import pytest

from powercontext.builtin.artifacts.skill import (
    AgentSkillProvider,
    AgentSkillTarget,
    CodexSkillProvider,
    CodexSkillRoot,
    ExternalSkillNotFoundError,
    ExternalSkillResolutionStatus,
)
from powercontext.builtin.artifacts.skill.registry import ExternalSkillRegistryService
from powercontext.builtin.persistence.external_skills import ExternalSkillRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES


def _write_skill(root: Path) -> Path:
    package = root / "friendly-python"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\nname: friendly-python\ndescription: Use when writing Python.\n---\n\nKeep boundaries explicit.\n",
        encoding="utf-8",
    )
    return package


def test_registry_refreshes_projection_and_checks_live_availability(tmp_path: Path) -> None:
    async def exercise() -> None:
        root = tmp_path / ".agents" / "skills"
        package = _write_skill(root)
        provider = CodexSkillProvider(
            host_id="workstation-1",
            roots=(CodexSkillRoot(root_id="repository", installation_scope="project", path=root),),
        )
        database_path = tmp_path / "powercontext.db"
        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{database_path}"),
            tables=BUILTIN_TABLES,
        ) as profile:
            service = ExternalSkillRegistryService(
                database=profile.database,
                scope_id="project:example",
                repository=ExternalSkillRepository(),
                provider=provider,
            )
            snapshot = await service.scan()
            registration = snapshot.registrations[0]
            assert len(await service.list()) == 1

            (package / "SKILL.md").write_text(
                (package / "SKILL.md").read_text(encoding="utf-8") + "Changed.\n",
                encoding="utf-8",
            )

            assert await service.list() == ()
            audit = await service.list(include_unavailable=True)
            assert audit[0].status is ExternalSkillResolutionStatus.UNAVAILABLE
            exact = await service.resolve(registration.external_skill_id, registration.fingerprint)
            assert exact.status is ExternalSkillResolutionStatus.UNAVAILABLE

            refreshed = await service.scan()
            current = refreshed.registrations[0]
            assert current.fingerprint != registration.fingerprint
            assert (
                await service.resolve(current.external_skill_id, current.fingerprint)
            ).status is ExternalSkillResolutionStatus.AVAILABLE

    asyncio.run(exercise())


def test_registry_is_scope_isolated(tmp_path: Path) -> None:
    async def exercise() -> None:
        root = tmp_path / ".agents" / "skills"
        _write_skill(root)
        provider = CodexSkillProvider(
            host_id="workstation-1",
            roots=(CodexSkillRoot(root_id="repository", installation_scope="project", path=root),),
        )
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            repository = ExternalSkillRepository()
            first = ExternalSkillRegistryService(
                database=profile.database,
                scope_id="project:one",
                repository=repository,
                provider=provider,
            )
            second = ExternalSkillRegistryService(
                database=profile.database,
                scope_id="project:two",
                repository=repository,
                provider=provider,
            )
            registration = (await first.scan()).registrations[0]
            with pytest.raises(ExternalSkillNotFoundError):
                await second.resolve(registration.external_skill_id, registration.fingerprint)

    asyncio.run(exercise())


def test_agent_registry_removes_a_provider_when_its_targets_are_unconfigured(tmp_path: Path) -> None:
    async def exercise() -> None:
        codex_root = tmp_path / ".agents" / "skills"
        claude_root = tmp_path / ".claude" / "skills"
        _write_skill(codex_root)
        _write_skill(claude_root)
        codex_target = AgentSkillTarget(
            target_id="codex-project",
            agent_kind="codex",
            installation_scope="project",
            path=codex_root,
        )
        mixed = AgentSkillProvider(
            host_id="workstation-1",
            targets=(
                codex_target,
                AgentSkillTarget(
                    target_id="claude-project",
                    agent_kind="claude_code",
                    installation_scope="project",
                    path=claude_root,
                ),
            ),
        )
        codex_only = AgentSkillProvider(host_id="workstation-1", targets=(codex_target,))
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            repository = ExternalSkillRepository()
            mixed_service = ExternalSkillRegistryService(
                database=profile.database,
                scope_id="project:example",
                repository=repository,
                provider=mixed,
            )
            codex_service = ExternalSkillRegistryService(
                database=profile.database,
                scope_id="project:example",
                repository=repository,
                provider=codex_only,
            )

            await mixed_service.scan()
            assert {skill.registration.provider for skill in await mixed_service.list()} == {"codex", "claude_code"}

            await codex_service.scan()
            remaining = await codex_service.list(include_unavailable=True)
            assert [skill.registration.provider for skill in remaining] == ["codex"]

    asyncio.run(exercise())
