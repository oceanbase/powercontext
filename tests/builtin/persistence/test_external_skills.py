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

from powercontext.builtin.artifacts.skill import ExternalSkillRegistration
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.external_skills import ExternalSkillRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES


def _registration(name: str, fingerprint: str = "a" * 64) -> ExternalSkillRegistration:
    return ExternalSkillRegistration(
        external_skill_id=f"codex:project:repository/{name}",
        host_id="workstation-1",
        installation_scope="project",
        locator=f"/workspace/.agents/skills/{name}",
        fingerprint=fingerprint,
        name=name,
        description=f"Use {name} for a bounded task.",
    )


def test_external_skill_projection_is_replaced_per_provider_host(tmp_path: Path) -> None:
    async def exercise() -> tuple[ExternalSkillRegistration, ...]:
        repository = ExternalSkillRepository()
        database = tmp_path / "powercontext.db"
        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"),
            tables=BUILTIN_TABLES,
        ) as profile:
            async with profile.database.transaction() as connection:
                await repository.replace(
                    connection,
                    "project:example",
                    "codex",
                    "workstation-1",
                    (_registration("friendly-python"), _registration("piglet")),
                )
            async with profile.database.transaction() as connection:
                assert [value.name for value in await repository.list(connection, "project:example")] == [
                    "friendly-python",
                    "piglet",
                ]
                await repository.replace(
                    connection,
                    "project:example",
                    "codex",
                    "workstation-1",
                    (_registration("friendly-python", "b" * 64),),
                )
            async with profile.database.transaction() as connection:
                return await repository.list(connection, "project:example")

    refreshed = asyncio.run(exercise())

    assert len(refreshed) == 1
    assert refreshed[0].fingerprint == "b" * 64


def test_external_skill_projection_is_scope_isolated(tmp_path: Path) -> None:
    async def exercise() -> None:
        repository = ExternalSkillRepository()
        database = tmp_path / "powercontext.db"
        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"),
            tables=BUILTIN_TABLES,
        ) as profile:
            async with profile.database.transaction() as connection:
                await repository.replace(
                    connection,
                    "project:one",
                    "codex",
                    "workstation-1",
                    (_registration("friendly-python"),),
                )
            async with profile.database.transaction() as connection:
                with pytest.raises(RepositoryNotFoundError):
                    await repository.get(
                        connection,
                        "project:two",
                        "codex:project:repository/friendly-python",
                    )

    asyncio.run(exercise())
