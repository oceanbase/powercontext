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

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from powercontext.artifacts import Artifact, ArtifactDraft
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import SHARED_TABLES
from powercontext.sources import (
    Source,
    SourceMaterialization,
)


class CommitInput(BaseModel):
    revision: str


class CommitSource(Source):
    revision: str


class CommitAdapter:
    name = "commit"
    input_class = CommitInput
    source_class = CommitSource

    async def resolve(self, value: CommitInput, /) -> CommitSource:
        return CommitSource(
            name=value.revision,
            materialization=SourceMaterialization.REFERENCED,
            revision=value.revision,
        )

    async def read(self, source: CommitSource, /) -> str:
        return source.revision


class NoteInput(BaseModel):
    note_id: str
    body: str


class NoteSource(Source):
    body: str


class NoteAdapter:
    name = "note"
    input_class = NoteInput
    source_class = NoteSource

    async def resolve(self, value: NoteInput, /) -> NoteSource:
        return NoteSource(
            name=value.note_id,
            materialization=SourceMaterialization.CAPTURED,
            body=value.body,
        )

    async def read(self, source: NoteSource, /) -> str:
        return source.body


class HandoffContent(BaseModel):
    summary: str


class Handoff(Artifact[HandoffContent]):
    family: ClassVar[str] = "handoff"


class HandoffDraft(ArtifactDraft[HandoffContent]):
    family: ClassVar[str] = "handoff"


class ReportContent(BaseModel):
    status: str


class Report(Artifact[ReportContent]):
    family: ClassVar[str] = "report"


class ReportDraft(ArtifactDraft[ReportContent]):
    family: ClassVar[str] = "report"


SOURCE_ADAPTERS = (CommitAdapter(), NoteAdapter())
ARTIFACT_TYPES = (Handoff, Report)


class RepositoryBundle(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    sources: SourceRepository
    artifacts: ArtifactRepository
    cursors: SourceCursorRepository


@asynccontextmanager
async def repository_profile() -> AsyncIterator[tuple[SQLiteProfile, RepositoryBundle]]:
    async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES) as profile:
        yield (
            profile,
            RepositoryBundle(
                sources=SourceRepository(SOURCE_ADAPTERS),
                artifacts=ArtifactRepository(ARTIFACT_TYPES),
                cursors=SourceCursorRepository(),
            ),
        )
