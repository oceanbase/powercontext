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
import json
import os
import subprocess
import sys
from contextlib import AsyncExitStack
from dataclasses import replace
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select

from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING
from powercontext.builtin.artifacts.topic_memory.generation import TopicMemoryGenerationError, TopicMemoryProbeOutput
from powercontext.builtin.inference import GenerationResult, character_token_estimator
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.processing import ArtifactProcessingPendingRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.supervision import ArtifactProcessingLeaseRepository
from powercontext.builtin.persistence.tables import TOPIC_MEMORY_WORK_BUDGETS_TABLE
from powercontext.builtin.persistence.topic_memory_budget import (
    MAX_TOPIC_MEMORY_WORK_ATTEMPTS,
    MAX_TOPIC_MEMORY_WORK_REQUESTS,
    MAX_TOPIC_MEMORY_WORK_TOKENS,
)
from powercontext.builtin.runtime.artifact_processing import ArtifactProcessingWorkerOutcome
from powercontext.builtin.runtime.composition import (
    BuiltinConfigurationError,
    _artifact_processing_bindings,
    _provider_factory,
    _topic_memory_processing_available,
)
from powercontext.builtin.runtime.config import BuiltinConfig, InferenceConfig, RuntimeConfig
from powercontext.builtin.runtime.topic_memory_processing import (
    MAX_TOPIC_MEMORY_SOURCE_CHARACTERS,
    MAX_TOPIC_MEMORY_SOURCE_DEPTH,
    MAX_TOPIC_MEMORY_SOURCE_NODES,
    TopicMemoryAtomicPublisher,
    TopicMemoryProcessor,
    TopicMemoryWindowSelector,
    TopicMemoryWorkerSpec,
    _open_topic_memory_processor,
)
from powercontext.builtin.sources import CONTENT_SOURCE_ADAPTER, ContentCapture
from tests.builtin.persistence.contract import SOURCE_ADAPTERS
from tests.builtin.runtime.test_topic_memory_processing import _assignment, _repositories, _stages


class _FastProbe:
    def __init__(self, failure=None):
        self.calls = 0
        self.characters = 0
        self.failure = failure

    async def generate(self, value, /):
        self.calls += 1
        self.characters += sum(len(item.content) for item in value.evidence)
        if self.failure == "crash":
            os._exit(73)
        if self.failure == "cancel":
            raise asyncio.CancelledError
        if self.failure:
            raise RuntimeError("provider unavailable")  # noqa: TRY003
        return GenerationResult(output=TopicMemoryProbeOutput())


def _processor(profile, sources, topics, fake, *, limit=100_000, requests=2, reserve=None):
    stages = replace(
        _stages(probe=TopicMemoryProbeOutput(), global_output=None, limit=limit),
        probe=fake,
        max_requests=requests,
        transcript_reserve=reserve,
    )
    return TopicMemoryProcessor(
        database=profile.database,
        sources=sources,
        topics=topics,
        stages=stages,
        publisher=TopicMemoryAtomicPublisher(profile.database, sources, topics),
    )


async def _capture(profile, *, metadata=None, tail=True):
    sources = SourceRepository((*SOURCE_ADAPTERS, CONTENT_SOURCE_ADAPTER))
    async with profile.database.transaction() as connection:
        await sources.add(
            connection,
            "scope-a",
            await CONTENT_SOURCE_ADAPTER.resolve(
                ContentCapture(source_id="first", content="body", metadata=metadata or {})
            ),
        )
        if tail:
            await sources.add(
                connection,
                "scope-a",
                await CONTENT_SOURCE_ADAPTER.resolve(ContentCapture(source_id="tail", content="ordinary tail")),
            )
        await ArtifactProcessingPendingRepository().raise_source(
            connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING, 2 if tail else 1
        )
        term = await ArtifactProcessingLeaseRepository().start_single_process_term(connection, "holder")
    return sources, _assignment(term.fence("single-process"))


async def _row(profile):
    async with profile.database.transaction() as connection:
        row = (await connection.execute(select(TOPIC_MEMORY_WORK_BUDGETS_TABLE))).mappings().one_or_none()
        return None if row is None else dict(row)


async def _assert_unpublished(profile, topics):
    async with profile.database.transaction() as connection:
        assert await SourceCursorRepository().load(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING) is None
        assert await topics.browse_current(connection, "scope-a", limit=10) == ()
        pending = await ArtifactProcessingPendingRepository().load(
            connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING
        )
        assert pending is not None and pending.source_through == 2


@pytest.mark.parametrize("character", ["a", "界"])
def test_maximum_canonical_metadata_fast_zero_probe_preserves_complete_source_and_tail(character):
    async def scenario():
        manager, profile, _, topics = await _repositories()
        try:
            overhead = len(json.dumps({"content": "body", "metadata": {"data": ""}}, separators=(",", ":")))
            metadata = {"data": character * (MAX_TOPIC_MEMORY_SOURCE_CHARACTERS - overhead)}
            sources, assignment = await _capture(profile, metadata=metadata)
            fake = _FastProbe()
            processor = _processor(profile, sources, topics, fake)
            selector = TopicMemoryWindowSelector(
                profile.database, sources, character_token_estimator(), context_window_tokens=125_000
            )
            assert await selector.select("scope-a", 0, 2) == 1
            assert (await processor.process(assignment)).outcome is ArtifactProcessingWorkerOutcome.SUCCEEDED
            assert fake.characters == MAX_TOPIC_MEMORY_SOURCE_CHARACTERS
            assert 1 < fake.calls * 2 <= MAX_TOPIC_MEMORY_WORK_REQUESTS
            assert fake.calls * 250_000 <= MAX_TOPIC_MEMORY_WORK_TOKENS
            assert await _row(profile) is None
            async with profile.database.transaction() as connection:
                cursor = await SourceCursorRepository().load(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
                assert cursor is not None and cursor.cursor.sequence == 1
            tail = replace(
                assignment, source_after=1, source_through=2, wave_target=2, cursor_generation=cursor.generation
            )
            assert (await processor.process(tail)).outcome is ArtifactProcessingWorkerOutcome.SUCCEEDED
            async with profile.database.transaction() as connection:
                cursor = await SourceCursorRepository().load(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
                assert cursor is not None and cursor.cursor.sequence == 2
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


@pytest.mark.parametrize("boundary", ["characters", "depth", "nodes"])
def test_metadata_complexity_rejection_is_durable_and_never_skips_tail(boundary):
    async def scenario():
        metadata = {"data": "界" * MAX_TOPIC_MEMORY_SOURCE_CHARACTERS}
        if boundary == "depth":
            metadata = {"data": "ok"}
            for _ in range(MAX_TOPIC_MEMORY_SOURCE_DEPTH):
                metadata = {"next": metadata}
        elif boundary == "nodes":
            metadata = {"data": [0] * MAX_TOPIC_MEMORY_SOURCE_NODES}
        manager, profile, _, topics = await _repositories()
        try:
            sources, assignment = await _capture(profile, metadata=metadata)
            fake = _FastProbe()
            processor = _processor(profile, sources, topics, fake)
            selector = TopicMemoryWindowSelector(
                profile.database, sources, character_token_estimator(), context_window_tokens=125_000
            )
            assert await selector.select("scope-a", 0, 2) == 1
            for _ in range(2):
                with pytest.raises(TopicMemoryGenerationError, match="source_complexity_limit"):
                    await processor.process(assignment)
            row = await _row(profile)
            assert row["attempts"] == 1 and row["requests"] == row["tokens"] == fake.calls == 0
            with pytest.raises(TopicMemoryGenerationError, match="source_complexity_limit"):
                await selector.select("scope-a", 0, 2)
            await _assert_unpublished(profile, topics)
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


@pytest.mark.parametrize("ceiling,limit,requests", [("requests", 1_000, 2), ("tokens", 100_000, 2)])
def test_fast_provider_stops_before_cumulative_request_or_token_ceiling(ceiling, limit, requests):
    async def scenario():
        manager, profile, _, topics = await _repositories()
        try:
            sources, assignment = await _capture(profile, metadata={"data": "界" * 1_000_000})
            fake = _FastProbe()
            reserve = 8_000_000 if ceiling == "tokens" else limit // 4
            capacity = limit + reserve
            processor = _processor(profile, sources, topics, fake, limit=limit, requests=requests, reserve=reserve)
            with pytest.raises(TopicMemoryGenerationError, match="window_provider_budget_exceeded"):
                await processor.process(assignment)
            before = await _row(profile)
            assert before["failure_code"] == "window_provider_budget_exceeded"
            assert before["requests"] == fake.calls * requests <= MAX_TOPIC_MEMORY_WORK_REQUESTS
            assert before["tokens"] == fake.calls * requests * capacity <= MAX_TOPIC_MEMORY_WORK_TOKENS
            allowance = (
                MAX_TOPIC_MEMORY_WORK_REQUESTS // requests
                if ceiling == "requests"
                else MAX_TOPIC_MEMORY_WORK_TOKENS // (requests * capacity)
            )
            assert fake.calls == allowance
            # New processor, wider window, new flush and worker cannot reset it.
            replacement = _processor(profile, sources, topics, fake)
            with pytest.raises(TopicMemoryGenerationError, match="window_provider_budget_exceeded"):
                await replacement.process(
                    replace(
                        assignment,
                        source_through=2,
                        wave_target=2,
                        claimed_flush_generation=99,
                        worker_id="replacement",
                    )
                )
            assert await _row(profile) == before
            await _assert_unpublished(profile, topics)
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def _crash_worker(database_path):
    async def scenario():
        manager, profile, _, topics = await _repositories(SQLiteConfig(url=f"sqlite+aiosqlite:///{database_path}"))
        try:
            sources = SourceRepository((*SOURCE_ADAPTERS, CONTENT_SOURCE_ADAPTER))
            async with profile.database.transaction() as connection:
                term = await ArtifactProcessingLeaseRepository().start_single_process_term(connection, "restarted")
            await _processor(profile, sources, topics, _FastProbe("crash")).process(
                _assignment(term.fence("single-process"))
            )
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_process_death_and_leader_restart_cannot_repeat_the_window_forever(tmp_path):
    database_path = tmp_path / "restart.sqlite3"

    async def setup():
        manager, profile, _, _ = await _repositories(SQLiteConfig(url=f"sqlite+aiosqlite:///{database_path}"))
        try:
            await _capture(profile)
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(setup())
    command = [
        sys.executable,
        "-c",
        "from tests.builtin.runtime.test_topic_memory_work_budget import _crash_worker; import sys; _crash_worker(sys.argv[1])",
        str(database_path),
    ]
    for _ in range(MAX_TOPIC_MEMORY_WORK_ATTEMPTS):
        result = subprocess.run(command, capture_output=True, timeout=30, check=False)
        assert result.returncode == 73, result.stderr.decode()

    async def verify():
        manager, profile, _, topics = await _repositories(SQLiteConfig(url=f"sqlite+aiosqlite:///{database_path}"))
        try:
            before = await _row(profile)
            assert before["attempts"] == MAX_TOPIC_MEMORY_WORK_ATTEMPTS
            assert before["requests"] == MAX_TOPIC_MEMORY_WORK_ATTEMPTS * 2
            assert before["tokens"] == MAX_TOPIC_MEMORY_WORK_ATTEMPTS * 250_000
            sources = SourceRepository((*SOURCE_ADAPTERS, CONTENT_SOURCE_ADAPTER))
            async with profile.database.transaction() as connection:
                term = await ArtifactProcessingLeaseRepository().start_single_process_term(connection, "new-leader")
            fake = _FastProbe()
            processor = _processor(profile, sources, topics, fake)
            with pytest.raises(TopicMemoryGenerationError, match="window_attempt_limit"):
                await processor.process(
                    replace(_assignment(term.fence("single-process"), through=2), claimed_flush_generation=99)
                )
            # Forging a later frontier without Cursor progress also cannot skip.
            assert (
                await processor.process(replace(_assignment(term.fence("single-process"), through=2), source_after=1))
            ).outcome is ArtifactProcessingWorkerOutcome.CURSOR_CONFLICT
            assert fake.calls == 0
            selector = TopicMemoryWindowSelector(
                profile.database, sources, character_token_estimator(), context_window_tokens=125_000
            )
            with pytest.raises(TopicMemoryGenerationError, match="window_attempt_limit"):
                await selector.select("scope-a", 0, 2)
            await _assert_unpublished(profile, topics)
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(verify())


def test_cancelled_reservation_is_not_refunded_and_retry_can_recover_tail():
    async def scenario():
        manager, profile, _, topics = await _repositories()
        try:
            sources, assignment = await _capture(profile)
            fake = _FastProbe("cancel")
            with pytest.raises(asyncio.CancelledError):
                await _processor(profile, sources, topics, fake).process(assignment)
            row = await _row(profile)
            assert row["attempts"] == 1 and row["requests"] == 2 and row["tokens"] == 250_000
            await _assert_unpublished(profile, topics)
            fake.failure = None
            replacement = _processor(profile, sources, topics, fake)
            assert (
                await replacement.process(replace(assignment, source_through=2, wave_target=2))
            ).outcome is ArtifactProcessingWorkerOutcome.SUCCEEDED
            assert await _row(profile) is None
            async with profile.database.transaction() as connection:
                cursor = await SourceCursorRepository().load(connection, "scope-a", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
                assert cursor is not None and cursor.cursor.sequence == 2
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


@pytest.mark.parametrize("provider_name", ["openai", "anthropic"])
@pytest.mark.parametrize("with_headers", [False, True])
def test_topic_provider_disables_sdk_transport_retries(provider_name, with_headers, monkeypatch):
    async def scenario():
        calls = []

        async def fail(request):
            calls.append(request)
            return httpx.Response(
                429, headers={"retry-after": "0"}, json={"error": {"message": "busy", "type": "rate_limit_error"}}
            )

        monkeypatch.setenv("OPENAI_API_KEY", "test")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
        async with AsyncExitStack() as resources:
            provider = _provider_factory(
                None,
                {"x-test": SecretStr("test")} if with_headers else {},
                workload="generation",
                resources=resources,
                disable_retries=True,
            )(provider_name)
            await resources.enter_async_context(provider)
            client = provider.client
            assert client.max_retries == 0
            # Preserve the SDK's real request/retry implementation; substitute transport only.
            transport = await resources.enter_async_context(httpx.AsyncClient(transport=httpx.MockTransport(fail)))
            client._client = transport
            with pytest.raises(Exception, match="busy"):
                if provider_name == "openai":
                    await client.chat.completions.create(
                        model="test", messages=[{"role": "user", "content": "x"}], max_completion_tokens=7
                    )
                else:
                    await client.messages.create(
                        model="test", messages=[{"role": "user", "content": "x"}], max_tokens=7
                    )
            assert len(calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "workload,key,value",
    [
        ("generation", "extra_body", {"max_completion_tokens": 999_999_999}),
        ("embedding", "extra_body", {"input": "hidden"}),
        ("generation", "openai_background", True),
        ("generation", "openai_previous_response_id", "response-id"),
        ("generation", "openai_conversation_id", "conversation-id"),
        ("generation", "openai_native_tools", [{"type": "web_search"}]),
    ],
)
def test_worker_and_binding_reject_unmetered_settings_before_opening_resources(workload, key, value):
    async def scenario():
        settings = {"generation_model": "openai:test"}
        if workload == "embedding":
            settings.update(embedding_model="openai:test", embedding_profile_id="test", embedding_dimension=2)
        settings[workload + "_model_settings"] = {key: value}
        config = BuiltinConfig(
            database=SQLiteConfig(url="sqlite+aiosqlite:///not-opened.sqlite3"),
            inference=InferenceConfig.model_validate(settings),
        )
        bindings = _artifact_processing_bindings(config, cast(Any, SimpleNamespace(database=None)), ())
        assert all(binding.artifact_family != "topic-memory" for binding in bindings)
        assert not _topic_memory_processing_available(config, ())
        api = config.model_copy(update={"runtime": RuntimeConfig(artifact_processing_role="api")})
        assert not _topic_memory_processing_available(api, ())
        scheduled = config.model_copy(update={"runtime": RuntimeConfig(topic_memory_schedule_seconds=60)})
        with pytest.raises(BuiltinConfigurationError):
            _artifact_processing_bindings(scheduled, cast(Any, None), ())
        spec = TopicMemoryWorkerSpec(config=config)
        with pytest.raises(BuiltinConfigurationError):
            async with _open_topic_memory_processor(spec, "scope-a"):
                pytest.fail("unsafe worker opened")

    asyncio.run(scenario())


def test_structured_retries_and_failed_calls_share_durable_reservation():
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel

    from powercontext.builtin.artifacts.topic_memory.generation import TopicMemoryProbeInput
    from powercontext.builtin.inference import InvalidInferenceOutputError
    from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIStructuredGenerator

    async def scenario():
        manager, profile, _, topics = await _repositories()
        observations = []
        valid = False
        try:
            sources, assignment = await _capture(profile)

            async def respond(_messages, _info):
                observations.append(await _row(profile))
                return ModelResponse(parts=[TextPart('{"probes":[]}' if valid else "not JSON")])

            raw = PydanticAIStructuredGenerator(
                model=FunctionModel(respond),
                instructions="Generate probes.",
                input_type=TopicMemoryProbeInput,
                output_type=TopicMemoryProbeOutput,
                limits=InferenceLimits(max_requests=2, max_output_tokens_per_request=512, output_tokens_limit=1024),
            )
            with pytest.raises(InvalidInferenceOutputError):
                await _processor(profile, sources, topics, raw).process(assignment)
            assert len(observations) == 2
            assert all(row["requests"] == 2 and row["tokens"] == 250_000 for row in observations)
            await _assert_unpublished(profile, topics)
            valid = True
            assert (
                await _processor(profile, sources, topics, raw).process(assignment)
            ).outcome is ArtifactProcessingWorkerOutcome.SUCCEEDED
            assert len(observations) == 3  # The unused fourth request stays conservatively charged.
            assert observations[-1]["attempts"] == 2
            assert observations[-1]["requests"] == 4 and observations[-1]["tokens"] == 500_000
            assert await _row(profile) is None
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_suspended_response_cannot_hide_multiple_provider_calls_in_one_request():
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.usage import RequestUsage

    from powercontext.builtin.artifacts.topic_memory.generation import TopicMemoryProbeInput
    from powercontext.builtin.inference import InvalidInferenceOutputError
    from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIStructuredGenerator

    async def scenario():
        manager, profile, _, topics = await _repositories()
        calls = 0
        try:
            sources, assignment = await _capture(profile)

            async def respond(_messages, _info):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return ModelResponse(
                        parts=[], state="suspended", usage=RequestUsage(input_tokens=1, output_tokens=0)
                    )
                return ModelResponse(parts=[TextPart('{"probes":[]}')])

            raw = PydanticAIStructuredGenerator(
                model=FunctionModel(respond),
                instructions="Generate probes.",
                input_type=TopicMemoryProbeInput,
                output_type=TopicMemoryProbeOutput,
                limits=InferenceLimits(
                    max_requests=1, max_output_tokens_per_request=7, output_tokens_limit=7, allow_continuations=False
                ),
            )
            with pytest.raises(InvalidInferenceOutputError, match="continuation"):
                await _processor(profile, sources, topics, raw, requests=1).process(assignment)
            assert calls == 1
            row = await _row(profile)
            assert row["requests"] == 1 and row["attempts"] == 1
            await _assert_unpublished(profile, topics)
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())


def test_unsupported_worker_provider_fails_when_binding_is_assembled():
    config = BuiltinConfig(
        database=SQLiteConfig(url="sqlite+aiosqlite:///not-opened.sqlite3"),
        inference=InferenceConfig(generation_model="google:test"),
        runtime=RuntimeConfig(topic_memory_schedule_seconds=60, artifact_processing_families=("topic-memory",)),
    )
    with pytest.raises(BuiltinConfigurationError, match="bounded stateless"):
        _artifact_processing_bindings(config, cast(Any, None), ())


def test_embedding_shares_exhausted_generation_budget_and_other_scope_can_progress(monkeypatch):
    from powercontext.builtin.artifacts.memory import EmbeddingProfile
    from powercontext.builtin.artifacts.topic_memory.generation import TopicMemoryProbe
    from powercontext.builtin.inference import EmbeddingResult
    from powercontext.builtin.persistence import topic_memory_budget

    async def scenario():
        manager, profile, _, topics = await _repositories()
        try:
            sources, assignment = await _capture(profile)
            # A small allowance reaches the embedding boundary without a long
            # synthetic model run. The default hard ceilings are tested above.
            monkeypatch.setattr(topic_memory_budget, "MAX_TOPIC_MEMORY_WORK_REQUESTS", 2)
            calls = 0

            class Embedding:
                profile = EmbeddingProfile(
                    profile_id="test", model="test", dimension=2, distance="l2", normalization="none"
                )

                async def embed(self, texts, /):
                    nonlocal calls
                    calls += 1
                    return EmbeddingResult(vectors=tuple((1.0, 0.0) for _ in texts))

            stages = _stages(
                probe=TopicMemoryProbeOutput(
                    probes=(TopicMemoryProbe(query="query", evidence_ids=("evidence-0001",)),)
                ),
                global_output=None,
            )
            processor = TopicMemoryProcessor(
                database=profile.database,
                sources=sources,
                topics=topics,
                stages=stages,
                embedding_model=Embedding(),
                publisher=TopicMemoryAtomicPublisher(profile.database, sources, topics),
            )
            with pytest.raises(TopicMemoryGenerationError, match="window_provider_budget_exceeded"):
                await processor.process(assignment)
            assert calls == 0
            row = await _row(profile)
            assert row["requests"] == 2 and row["failure_code"] == "window_provider_budget_exceeded"
            await _assert_unpublished(profile, topics)
            async with profile.database.transaction() as connection:
                await sources.add(
                    connection,
                    "scope-b",
                    await CONTENT_SOURCE_ADAPTER.resolve(
                        ContentCapture(source_id="healthy", content="ordinary source")
                    ),
                )
            healthy = _processor(profile, sources, topics, _FastProbe())
            assert (
                await healthy.process(replace(assignment, scope_id="scope-b"))
            ).outcome is ArtifactProcessingWorkerOutcome.SUCCEEDED
            async with profile.database.transaction() as connection:
                cursor = await SourceCursorRepository().load(connection, "scope-b", TOPIC_MEMORY_SOURCE_WINDOW_BINDING)
                assert cursor is not None and cursor.cursor.sequence == 1
            assert await _row(profile) == row
        finally:
            await manager.__aexit__(None, None, None)

    asyncio.run(scenario())
