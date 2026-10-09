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
from functools import partial

import pytest

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import SHARED_TABLES
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, RuntimeConfig, SearchMemoryRequest
from powercontext.builtin.runtime.artifact_processing import (
    ArtifactProcessingBinding,
    SpawnArtifactProcessingWorkerLauncher,
)
from powercontext.builtin.runtime.composition import open_builtin_contexts, open_builtin_runtime
from powercontext.builtin.runtime.family_processing import process_family_invocation
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.triggers import SOURCE_WINDOW_TRIGGER_NAME


class _MemoryPipeline:
    async def extract(self, request):
        return tuple(
            MemoryEntryInput(kind="fact", text=source.content, sources=(source,)) for source in request.sources
        )


def _memory_worker(config, assignment):
    async def run():
        async with open_builtin_contexts(config, candidate_pipeline=_MemoryPipeline()) as contexts:
            return await process_family_invocation(contexts, assignment, config=config)

    return asyncio.run(run())


@pytest.mark.parametrize("mode", ["global", "dedicated"])
def test_custom_memory_worker_reports_configuration_and_persisted_success(tmp_path, mode):
    async def scenario():
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'worker.db'}"),
            runtime=RuntimeConfig(artifact_processing_families=("memory",), artifact_processing_supervisor_mode=mode),
        )
        binding = ArtifactProcessingBinding(
            SOURCE_WINDOW_TRIGGER_NAME,
            "memory",
            SpawnArtifactProcessingWorkerLauncher(partial(_memory_worker, config)),
        )
        async with open_builtin_runtime(
            config, scheduler_path=tmp_path / "scheduler.db", artifact_processing_bindings=(binding,)
        ) as runtime:
            initial = runtime.extraction_status()
            assert initial is not None
            assert initial.background.location == "local"
            assert initial.background.state == "running"
            assert initial.observation.status == "unverified"
            assert (await runtime.capabilities()).memory_extraction is False
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Worker", summary="Custom extraction", idempotency_key="custom-worker")
            )
            text = "The deployment color is amber."
            await runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(source_id="decision", content=text, metadata={})
            )
            async with (
                SQLiteProfile.open(config.database, tables=SHARED_TABLES) as profile,
                profile.database.transaction() as connection,
            ):
                await ArtifactProcessingIntentRepository().request(connection, scope.scope_id, binding.binding_name)
            assert runtime.artifact_processing_supervisor is not None
            runtime.artifact_processing_supervisor.wake(binding.binding_name)
            async with asyncio.timeout(20):
                while True:
                    observed = runtime.extraction_status()
                    assert observed is not None
                    if observed.observation.last_success_at is not None:
                        break
                    await asyncio.sleep(0.01)
            memory = await runtime.memory.for_scope(scope.scope_id).search(SearchMemoryRequest(query="deployment"))
            assert memory.memory_ref is not None and memory.memory_ref.revision == 1
            assert [hit.text for hit in memory.hits] == [text]
            async with (
                SQLiteProfile.open(config.database, tables=SHARED_TABLES) as profile,
                profile.database.transaction() as connection,
            ):
                intent = await ArtifactProcessingIntentRepository().load(
                    connection, scope.scope_id, binding.binding_name
                )
            assert intent is not None and intent.requested_generation == intent.handled_generation == 1
            assert observed.observation.last_failure is None
            assert initial.configuration == observed.configuration == "configured"

    asyncio.run(scenario())


@pytest.mark.parametrize("family", ["experience", "profile"])
def test_other_family_worker_does_not_configure_memory_extraction(tmp_path, family):
    async def scenario():
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'other-worker.db'}"),
            runtime=RuntimeConfig(artifact_processing_families=(family,)),
        )
        binding = ArtifactProcessingBinding(
            f"custom-{family}", family, SpawnArtifactProcessingWorkerLauncher(partial(_memory_worker, config))
        )
        async with open_builtin_runtime(
            config, scheduler_path=tmp_path / "scheduler.db", artifact_processing_bindings=(binding,)
        ) as runtime:
            snapshot = runtime.extraction_status()
            assert snapshot is not None
            assert snapshot.configuration == "unconfigured"
            assert snapshot.background.location == "none"
            assert snapshot.observation.status == "unverified"

    asyncio.run(scenario())
