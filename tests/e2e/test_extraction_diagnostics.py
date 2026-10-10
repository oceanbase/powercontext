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

from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryCandidate,
    AtomicMemoryExtractionInput,
    AtomicMemoryExtractionOutput,
    AtomicMemoryGenerationPipeline,
)
from powercontext.builtin.artifacts.atomic_memory.reconciliation import (
    AtomicMemoryReconciliationContent,
    AtomicMemoryReconciliationInput,
    AtomicMemoryReconciliationOutput,
)
from powercontext.builtin.inference import GenerationResult, character_token_estimator
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import SHARED_TABLES
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, RuntimeConfig
from powercontext.builtin.runtime.artifact_processing import (
    ArtifactProcessingBinding,
    SpawnArtifactProcessingWorkerLauncher,
)
from powercontext.builtin.runtime.composition import open_builtin_contexts, open_builtin_runtime
from powercontext.builtin.runtime.family_processing import process_family_invocation
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.triggers import SOURCE_WINDOW_TRIGGER_NAME


class _SourceExtractor:
    async def generate(self, request: AtomicMemoryExtractionInput, /) -> GenerationResult[AtomicMemoryExtractionOutput]:
        candidates = []
        for evidence in request.evidence:
            assert isinstance(evidence.content, dict)
            text = evidence.content["content"]
            assert isinstance(text, str)
            candidates.append(AtomicMemoryCandidate(kind="fact", text=text, evidence_ids=(evidence.evidence_id,)))
        return GenerationResult(output=AtomicMemoryExtractionOutput(candidates=tuple(candidates)))


class _CreatingReconciler:
    async def generate(
        self, request: AtomicMemoryReconciliationInput, /
    ) -> GenerationResult[AtomicMemoryReconciliationOutput]:
        return GenerationResult(
            output=AtomicMemoryReconciliationOutput(
                action="create",
                compared_ids=tuple(item.item_id for item in request.related),
                content=AtomicMemoryReconciliationContent(kind=request.proposal.kind, text=request.proposal.text),
                evidence_ids=request.proposal.evidence_ids,
                reason="Keep the captured decision.",
            )
        )


def _memory_worker(config, assignment):
    async def run():
        async with open_builtin_contexts(
            config,
            candidate_pipeline=AtomicMemoryGenerationPipeline(
                extractor=_SourceExtractor(), reconciler=_CreatingReconciler(), estimator=character_token_estimator()
            ),
        ) as contexts:
            return await process_family_invocation(contexts, assignment, config=config)

    return asyncio.run(run())


@pytest.mark.parametrize("mode", ["global", "dedicated"])
def test_custom_memory_worker_reports_configuration_and_persisted_success(tmp_path, mode):
    async def scenario():
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'worker.db'}")
        config = BuiltinConfig(
            database=database,
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
                SQLiteProfile.open(database, tables=SHARED_TABLES) as profile,
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
            assert runtime.atomic_memory is not None
            memory = await runtime.atomic_memory.for_scope(scope.scope_id).search("deployment", mode="text")
            assert [hit.text for hit in memory.hits] == [text]
            async with (
                SQLiteProfile.open(database, tables=SHARED_TABLES) as profile,
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
