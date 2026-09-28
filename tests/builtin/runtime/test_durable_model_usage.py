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
from datetime import UTC, datetime

import pytest

from powercontext.builtin.artifacts.experience import ExperienceCandidateInput
from powercontext.builtin.artifacts.memory import MemoryCandidateRequest, MemoryEntryInput
from powercontext.builtin.inference import GenerationResult, InferenceUsage
from powercontext.builtin.inference.usage import UsageReportingStructuredGenerator
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.statistics import StatisticsRepository
from powercontext.builtin.persistence.work import WorkRepository, WorkStatus
from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig, open_builtin_contexts
from powercontext.builtin.runtime.config import WorkerConfig
from powercontext.builtin.runtime.work_handlers import (
    ExperienceWorkHandler,
    MemoryWorkHandler,
    experience_work_spec,
    memory_work_spec,
)
from powercontext.builtin.runtime.worker import DurableWorker
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import ContentCapture
from powercontext.builtin.statistics import ModelUsageOperation, ModelUsagePurpose
from powercontext.sources import Source


class _Model:
    async def generate(self, value: str, /) -> GenerationResult[str]:
        return GenerationResult(output=value, usage=InferenceUsage(requests=1, input_tokens=7, output_tokens=3))


class _Pipeline:
    def __init__(self, *, fail_after_generation: bool) -> None:
        self._fail = fail_after_generation
        self._generator = UsageReportingStructuredGenerator(_Model())

    async def _generate(self) -> None:
        async with asyncio.timeout(0.02):
            await self._generator.generate("bounded model request")
        if self._fail:
            raise RuntimeError("generation result rejected")  # noqa: TRY003

    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        await self._generate()
        return ()

    async def incubate(self, sources: tuple[Source, ...], /) -> tuple[ExperienceCandidateInput, ...]:
        await self._generate()
        return ()


@pytest.mark.parametrize("family", ["memory", "experience"])
@pytest.mark.parametrize("fail_after_generation", [False, True])
def test_durable_usage_survives_slow_accounting_and_failed_generation(
    tmp_path, monkeypatch: pytest.MonkeyPatch, family: str, fail_after_generation: bool
) -> None:
    original_record = StatisticsRepository.record

    async def slow_record(repository, connection, *args):
        # This delay exceeds the model deadline and must be owned by the recorder.
        await asyncio.sleep(0.05)
        await original_record(repository, connection, *args)

    monkeypatch.setattr(StatisticsRepository, "record", slow_record)

    async def scenario() -> None:
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'usage.db'}"),
            runtime=RuntimeConfig(model_usage_write_timeout_seconds=2, model_usage_flush_timeout_seconds=1),
        )
        pipeline = _Pipeline(fail_after_generation=fail_after_generation)
        async with open_builtin_contexts(config, candidate_pipeline=pipeline, experience_pipeline=pipeline) as contexts:
            scope = await contexts.scopes.create(
                ScopeDraft(title="Usage", summary="Durable usage accounting", idempotency_key="usage")
            )
            context = await contexts.get(scope.scope_id)
            await context.sources.capture(ContentCapture(source_id="one", content="One durable source."))
            discover = memory_work_spec if family == "memory" else experience_work_spec
            spec = await discover(contexts, scope.scope_id, limit=100, max_attempts=1, payload_version=1)
            assert spec is not None
            repository = WorkRepository()
            async with contexts.database.transaction() as connection:
                queued = await repository.enqueue(connection, spec)
            worker = DurableWorker(
                database=contexts.database,
                worker_id="usage-worker",
                handlers=(MemoryWorkHandler(contexts) if family == "memory" else ExperienceWorkHandler(contexts),),
                config=WorkerConfig(concurrency=1),
            )
            assert await worker.run_once() == 1
            day = datetime.now(UTC).date()
            # Read persisted rows directly: the completed attempt must drain its
            # accepted usage without relying on a statistics read or shutdown.
            async with contexts.database.transaction() as connection:
                work = await repository.get(connection, queued.work.work_id)
                usage = await contexts.repositories.statistics.usage(connection, scope.scope_id, day, day)
            assert work.status is (WorkStatus.FAILED if fail_after_generation else WorkStatus.SUCCEEDED)
            assert len(usage) == 1
            assert usage[0].purpose is (
                ModelUsagePurpose.MEMORY_EXTRACTION if family == "memory" else ModelUsagePurpose.EXPERIENCE_GENERATION
            )
            assert usage[0].operation is ModelUsageOperation.GENERATION
            assert (usage[0].requests, usage[0].input_tokens, usage[0].output_tokens) == (1, 7, 3)

    asyncio.run(scenario())
