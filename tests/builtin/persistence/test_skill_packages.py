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
import mimetypes

import pytest
from sqlalchemy import update

from powercontext.builtin.artifacts.skill import SkillContent, build_instruction_skill_package
from powercontext.builtin.persistence import InvalidStoredPayloadError, RepositoryNotFoundError, SkillPackageRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES, SKILL_PACKAGES_TABLE


def _snapshot():
    return build_instruction_skill_package(
        SkillContent(
            name="release-check",
            description="Verify a release before publishing it.",
            instructions="Run the release verification.",
            validation=("The report passes.",),
        )
    )


def test_sqlite_skill_package_round_trip_is_idempotent_and_scope_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    async def exercise() -> None:
        repository = SkillPackageRepository()
        snapshot = _snapshot()
        async with (
            SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile,
            profile.database.transaction() as connection,
        ):
            assert await repository.add(connection, "project:one", snapshot) == snapshot.reference
            assert await repository.add(connection, "project:one", snapshot) == snapshot.reference
            monkeypatch.setattr(mimetypes, "guess_type", lambda *_args, **_kwargs: ("application/x-host-local", None))
            restored = await repository.get(connection, "project:one", snapshot.reference)
            assert restored == snapshot
            with pytest.raises(RepositoryNotFoundError):
                await repository.get(connection, "project:two", snapshot.reference)

    asyncio.run(exercise())


def test_skill_package_read_detects_stored_archive_corruption() -> None:
    async def exercise() -> None:
        repository = SkillPackageRepository()
        snapshot = _snapshot()
        async with (
            SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile,
            profile.database.transaction() as connection,
        ):
            await repository.add(connection, "project:one", snapshot)
            await connection.execute(
                update(SKILL_PACKAGES_TABLE)
                .where(SKILL_PACKAGES_TABLE.c.scope_id == "project:one")
                .values(archive_bytes=b"not-a-zip")
            )
            with pytest.raises(InvalidStoredPayloadError, match="canonical archive is invalid"):
                await repository.get(connection, "project:one", snapshot.reference)

    asyncio.run(exercise())
