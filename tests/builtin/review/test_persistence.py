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

import asyncio

from sqlalchemy import select

from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.persistence.candidates import CandidateRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import ARTIFACT_CANDIDATE_VERSIONS_TABLE, SHARED_TABLES
from powercontext.sources import SourceRef


def _proposal(lesson: str) -> ExperienceContent:
    return ExperienceContent(
        situation="OpenAPI changes.",
        action="Regenerate the Client.",
        outcome="The transport stays aligned.",
        lesson=lesson,
    )


def test_revise_appends_an_immutable_candidate_version() -> None:
    async def scenario() -> None:
        repository = CandidateRepository({"experience": ExperienceContent})
        evidence = (SourceRef(source_type="content", source_id="task-1"),)
        async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES) as profile:
            async with profile.database.transaction() as connection:
                original = await repository.create(
                    connection,
                    "project",
                    "candidate-1",
                    "experience",
                    _proposal("Initial lesson."),
                    sources=evidence,
                    artifacts=(),
                    target=None,
                    reason=None,
                )
            async with profile.database.transaction() as connection:
                revised = await repository.revise(
                    connection,
                    "project",
                    "candidate-1",
                    1,
                    _proposal("Reviewed lesson."),
                    sources=evidence,
                    artifacts=(),
                    target=None,
                    reason=None,
                )
            async with profile.database.transaction() as connection:
                versions = tuple(
                    (
                        await connection.execute(
                            select(
                                ARTIFACT_CANDIDATE_VERSIONS_TABLE.c.version,
                                ARTIFACT_CANDIDATE_VERSIONS_TABLE.c.proposal,
                            )
                            .where(ARTIFACT_CANDIDATE_VERSIONS_TABLE.c.candidate_id == "candidate-1")
                            .order_by(ARTIFACT_CANDIDATE_VERSIONS_TABLE.c.version)
                        )
                    ).all()
                )

            assert original.version == 1
            assert revised.version == 2
            assert tuple(row.version for row in versions) == (1, 2)
            assert versions[0].proposal != versions[1].proposal

    asyncio.run(scenario())
