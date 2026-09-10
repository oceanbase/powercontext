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
import pytest

from powercontext.builtin.artifacts.experience import (
    EXPERIENCE_INCUBATION_CURSOR_NAME,
    ExperienceCandidateInput,
    ExperienceContent,
)
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig, RuntimeConfig
from powercontext.builtin.runtime.composition import open_builtin_contexts
from powercontext.builtin.runtime.family_processing import process_family_invocation
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.builtin.sources import ContentSource
from powercontext.client import PowerContextClient
from powercontext.http import (
    CandidateFamily,
    CaptureContentSourceRequest,
    ListArtifactCandidatesRequest,
    PrepareContextRequest,
)
from powercontext.server.factory import create_server_app
from powercontext.server.processing_security import open_worker_security
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


def _experience_worker(spec, assignment):
    async def run():
        async with (
            open_builtin_contexts(spec.config, experience_pipeline=_TaskOutcomePipeline()) as contexts,
            open_worker_security(spec.worker_security, contexts.database) as security,
        ):
            return await process_family_invocation(contexts, assignment, config=spec.config, security=security)

    return asyncio.run(run())


def _app(database: Path, scheduler: Path):
    return create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"),
            runtime=RuntimeConfig(experience_schedule_seconds=0.02, artifact_processing_families=("experience",)),
            inference=InferenceConfig(generation_model="test"),
            mcp=McpConfig(enabled=False),
        ),
        scheduler_path=scheduler,
    )


async def _pending_experience(client: PowerContextClient, scope_id: str):
    deadline = monotonic() + 30
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


async def _wait_for_handled(app, scope_id: str, minimum_generation: int) -> int:
    contexts = app.state.application._provider
    assert isinstance(contexts, RelationalContexts)
    async with asyncio.timeout(30):
        while True:
            async with contexts.database.transaction() as connection:
                intent = await ArtifactProcessingIntentRepository().load(
                    connection, scope_id, EXPERIENCE_INCUBATION_CURSOR_NAME
                )
            if (
                intent is not None
                and intent.handled_generation >= minimum_generation
                and intent.handled_generation == intent.requested_generation
                and intent.clean_generation == intent.dirty_generation
            ):
                return intent.handled_generation
            await asyncio.sleep(0.02)


def test_scheduler_incubates_task_outcome_once_and_preserves_review_gating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The deterministic adapter is rebuilt in the spawned child. Discovery,
    # the Scope invocation transaction, durable acknowledgement and Inbox are real.
    monkeypatch.setattr("powercontext.builtin.runtime.composition.run_family_worker", _experience_worker)

    async def scenario() -> None:
        database = tmp_path / "powercontext.db"
        scheduler = tmp_path / "scheduler.db"
        app = _app(database, scheduler)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            scope_id = (await client.get_default_scope()).scope_id
            captured = await client.capture_content_source(
                CaptureContentSourceRequest(
                    scope_id=scope_id,
                    source_id="task-1",
                    content="python test_config.py passed",
                    metadata={"kind": "task-outcome"},
                )
            )
            candidates = await _pending_experience(client, scope_id)
            handled_generation = await _wait_for_handled(app, scope_id, 1)
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
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            await client.capture_content_source(
                CaptureContentSourceRequest(
                    scope_id=scope_id,
                    source_id="follow-up",
                    content="The recorded outcome remains available after restarting the Server.",
                )
            )
            await _wait_for_handled(restored, scope_id, handled_generation + 1)
            candidates = await _pending_experience(client, scope_id)
            assert len(candidates) == 1
        assert not scheduler.exists()

    asyncio.run(scenario())
