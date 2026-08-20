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
from pathlib import Path
from time import monotonic

import httpx

from powercontext.builtin.artifacts.experience import ExperienceCandidateInput, ExperienceContent
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import RuntimeConfig
from powercontext.builtin.sources import ContentSource
from powercontext.client import PowerContextClient
from powercontext.http import (
    CandidateFamily,
    CaptureContentSourceRequest,
    ListArtifactCandidatesRequest,
    PrepareContextRequest,
)
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings
from powercontext.sources import Source, SourceRef


class _TaskOutcomePipeline:
    async def incubate(self, sources: tuple[Source, ...], /) -> tuple[ExperienceCandidateInput, ...]:
        return tuple(
            ExperienceCandidateInput(
                proposal=ExperienceContent(
                    situation="A strict configuration fixture failed.",
                    action="Set the configuration mode to strict.",
                    outcome=source.content,
                    lesson="Run the strict fixture after configuration changes.",
                ),
                sources=(SourceRef(source_type="content", source_id=source.name),),
            )
            for source in sources
            if isinstance(source, ContentSource) and source.metadata.get("kind") == "task-outcome"
        )


def _app(database: Path, scheduler: Path):
    return create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"),
            runtime=RuntimeConfig(experience_schedule_seconds=0.02),
            mcp=McpConfig(enabled=False),
        ),
        scheduler_path=scheduler,
        experience_pipeline=_TaskOutcomePipeline(),
    )


async def _pending_experience(client: PowerContextClient, scope_id: str):
    deadline = monotonic() + 3
    while monotonic() < deadline:
        page = await client.list_artifact_candidates(
            ListArtifactCandidatesRequest(
                scope_id=scope_id,
                family=CandidateFamily.EXPERIENCE,
            )
        )
        if page.candidates:
            return page.candidates
        await asyncio.sleep(0.02)
    raise AssertionError("scheduled Experience Candidate did not reach the Review Inbox")  # noqa: TRY003


def test_scheduler_incubates_task_outcome_once_and_preserves_review_gating(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "powercontext.db"
        scheduler = tmp_path / "scheduler.db"
        scope_id = "scheduled-experience-e2e"
        app = _app(database, scheduler)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport)
            captured = await client.capture_content_source(
                CaptureContentSourceRequest(
                    scope_id=scope_id,
                    source_id="task-1",
                    content="python test_config.py passed",
                    metadata={"kind": "task-outcome"},
                )
            )
            candidates = await _pending_experience(client, scope_id)
            prepared = await client.prepare_context(
                PrepareContextRequest(
                    scope_id=scope_id,
                    query="strict fixture configuration",
                )
            )

            assert len(candidates) == 1
            assert candidates[0].source_refs == [captured.source]
            assert candidates[0].result_artifact is None
            assert prepared.status == "empty"

        restored = _app(database, scheduler)
        async with (
            restored.router.lifespan_context(restored),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=restored),
                base_url="http://testserver",
            ) as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport)
            await asyncio.sleep(0.08)
            candidates = await _pending_experience(client, scope_id)
            assert len(candidates) == 1

    asyncio.run(scenario())
