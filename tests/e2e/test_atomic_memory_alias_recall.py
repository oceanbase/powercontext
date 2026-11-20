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

"""Authored fixed-vector cases: historical wording reaches only its live successor."""

from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError

from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryGenerationPipeline,
    atomic_memory_extraction_instructions,
)
from powercontext.builtin.artifacts.atomic_memory.reconciliation import ATOMIC_MEMORY_RECONCILIATION_INSTRUCTIONS
from powercontext.builtin.artifacts.memory.prompts import MemoryExtractionProfile
from powercontext.builtin.artifacts.prompt.service import current_prompt
from powercontext.builtin.inference import EmbeddingResult, character_token_estimator
from powercontext.builtin.persistence.atomic_memory_index import AtomicMemoryIndexFilter, AtomicMemoryRelatedRequest
from powercontext.builtin.persistence.sqlite.atomic_memory_index import SQLiteAtomicMemoryIndex
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import open_builtin_contexts
from tests.e2e.test_atomic_memory_related_embedding import (
    PROFILE,
    _config,
    _Extractor,
    _Reconciler,
    _RecordingCreateReconciler,
)

OLD = "Deploys go out to five percent of traffic first."
OTHER = "Pause the deployment if errors rise."
CURRENT = "Rollout uses staged canary percentages with error monitoring."
UNRELATED = "The office fern needs watering every Tuesday."


class AliasEmbedding:
    profile = PROFILE

    async def embed(self, texts, /):
        return EmbeddingResult(
            vectors=tuple(
                (1.0, 0.0, 0.0) if OLD in text else (0.0, 0.0, 1.0) if UNRELATED in text else (0.0, 1.0, 0.0)
                for text in texts
            )
        )


class RecordingReconciler(_Reconciler):
    def __init__(self):
        super().__init__()
        self.requests = []
        self.actions = []
        self.tokens = []

    async def generate(self, request):
        self.requests.append(request)
        prompt = current_prompt("atomic_memory.reconcile")
        instructions = ATOMIC_MEMORY_RECONCILIATION_INSTRUCTIONS if prompt is None else prompt.compiled_instructions
        self.tokens.append(character_token_estimator().estimate(f"{instructions}\n{request.model_dump_json()}"))
        result = await super().generate(request)
        self.actions.append(result.output.action)
        return result


class RecordingExtractor(_Extractor):
    def __init__(self):
        self.tokens = []

    async def generate(self, request):
        prompt = current_prompt("atomic_memory.extract")
        instructions = (
            atomic_memory_extraction_instructions(MemoryExtractionProfile.CODING)
            if prompt is None
            else prompt.compiled_instructions
        )
        self.tokens.append(character_token_estimator().estimate(f"{instructions}\n{request.model_dump_json()}"))
        return await super().generate(request)


async def seed_alias(contexts):
    await contexts.get("project")
    memory = contexts.atomic_memory.for_scope("project")
    records = []
    for body in (OLD, OTHER):
        artifact = await contexts.records.create_artifact(
            "project", "atomic-memory", ArtifactWrite(content={"kind": "fact", "text": body})
        )
        records.append(await memory.get(artifact.artifact_id))
    successor = (
        await memory.merge(
            tuple(record.as_read() for record in records), AtomicMemoryContent(kind="fact", text=CURRENT)
        )
    ).primary
    return records, successor


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("observation", [OLD, UNRELATED])
def test_historical_vector_recall_prevents_duplicate_and_preserves_unrelated_create(tmp_path, enabled, observation):
    async def scenario():
        config = _config(tmp_path)
        config = config.model_copy(
            update={"runtime": config.runtime.model_copy(update={"atomic_memory_alias_recall": enabled})}
        )
        recorder = RecordingReconciler()
        extractor = RecordingExtractor()
        pipeline = AtomicMemoryGenerationPipeline(
            extractor=extractor, reconciler=recorder, estimator=character_token_estimator()
        )
        async with open_builtin_contexts(
            config, embedding_model=AliasEmbedding(), candidate_pipeline=pipeline
        ) as contexts:
            _, successor = await seed_alias(contexts)
            await (await contexts.get("project")).triggers.flush(limit=200)
            request = AtomicMemoryRelatedRequest(
                observation,
                AtomicMemoryIndexFilter(),
                mode="vector",
                query_vector=(1.0, 0.0, 0.0) if observation == OLD else (0.0, 0.0, 1.0),
                embedding_profile=PROFILE,
                max_distance=0.1,
            )
            index = SQLiteAtomicMemoryIndex(PROFILE)
            async with contexts.database.transaction(consistent_snapshot=True) as connection:
                direct, _ = await index.enumerate_related_with_heads(connection, "project", request)
                aliases, _ = (
                    await index.enumerate_aliased_related(connection, "project", request) if enabled else ((), {})
                )
            assert direct == ()
            await contexts.records.create_source("project", "content", observation)
            await contexts.process_memory("project", 1)
            items = (await contexts.atomic_memory.for_scope("project").list()).items
            matched = enabled and observation == OLD
            assert len(items) == (1 if matched else 2)
            assert tuple(item.text for req in recorder.requests for item in req.related) == (
                (CURRENT,) if matched else ()
            )
            if matched:
                assert items[0].ref == successor.ref
                assert recorder.actions == ["noop"]
            else:
                assert recorder.actions == ["create"]
            print(
                json.dumps(
                    {
                        "case": "historical" if observation == OLD else "unrelated",
                        "enabled": enabled,
                        "direct_hits": len(direct),
                        "alias_hits": len(aliases),
                        "actions": recorder.actions,
                        "reconciliation_calls": len(recorder.requests),
                        "reconciliation_input_tokens": sum(recorder.tokens),
                        "extraction_input_tokens": sum(extractor.tokens),
                        "active_identities": len(items),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )

    asyncio.run(scenario())


def test_alias_failure_that_destroys_snapshot_does_not_publish(tmp_path, monkeypatch):
    async def scenario():
        config = _config(tmp_path)
        config = config.model_copy(
            update={"runtime": config.runtime.model_copy(update={"atomic_memory_alias_recall": True})}
        )
        recorder = RecordingReconciler()
        pipeline = AtomicMemoryGenerationPipeline(
            extractor=_Extractor(), reconciler=recorder, estimator=character_token_estimator()
        )
        async with open_builtin_contexts(
            config, embedding_model=AliasEmbedding(), candidate_pipeline=pipeline
        ) as contexts:
            _, successor = await seed_alias(contexts)
            context = await contexts.get("project")
            await context.triggers.flush(limit=200)
            cursor = await context.triggers.cursor()

            async def lost_snapshot(connection, scope_id, request):
                await connection.exec_driver_sql("ROLLBACK")
                raise RuntimeError("Alias query lost its transaction")  # noqa: TRY003

            monkeypatch.setattr(contexts.atomic_memory.index, "aliased_related_ranks", lost_snapshot)
            await contexts.records.create_source("project", "content", OLD)
            with pytest.raises(OperationalError, match="savepoint"):
                await context.triggers.flush(limit=1)
            assert await context.triggers.cursor() == cursor
            assert (await contexts.atomic_memory.for_scope("project").list()).items == (successor,)
            assert recorder.requests == []

    asyncio.run(scenario())


def test_existing_unmanaged_database_initializes_empty_alias_table_without_changing_authority(tmp_path):
    async def scenario():
        config = _config(tmp_path)
        async with open_builtin_contexts(config, embedding_model=AliasEmbedding()) as contexts:
            _, successor = await seed_alias(contexts)
            async with contexts.database.transaction() as connection:
                await connection.exec_driver_sql("DROP TABLE pc_atomic_memory_alias")
        async with open_builtin_contexts(config, embedding_model=AliasEmbedding()) as contexts:
            assert (await contexts.atomic_memory.for_scope("project").list()).items == (successor,)
            async with contexts.database.transaction() as connection:
                assert await connection.run_sync(lambda sync: inspect(sync).has_table("pc_atomic_memory_alias"))
                assert await connection.scalar(text("SELECT COUNT(*) FROM pc_atomic_memory_alias")) == 0

    asyncio.run(scenario())


def test_alias_switch_does_not_add_transactions_without_recall_candidates(tmp_path):
    from sqlalchemy import event

    from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryExtractionOutput
    from powercontext.builtin.inference import GenerationResult

    class EmptyExtractor:
        async def generate(self, request):
            return GenerationResult(output=AtomicMemoryExtractionOutput(candidates=()))

    async def process(enabled):
        config = _config(tmp_path / str(enabled))
        config = config.model_copy(
            update={"runtime": config.runtime.model_copy(update={"atomic_memory_alias_recall": enabled})}
        )
        pipeline = AtomicMemoryGenerationPipeline(
            extractor=EmptyExtractor(), reconciler=RecordingReconciler(), estimator=character_token_estimator()
        )
        async with open_builtin_contexts(
            config, embedding_model=AliasEmbedding(), candidate_pipeline=pipeline
        ) as contexts:
            await contexts.get("project")
            await contexts.records.create_source("project", "content", OLD)
            statements = []

            def record(_connection, _cursor, statement, *_args):
                if statement.lstrip().upper().startswith(("BEGIN", "SAVEPOINT")):
                    statements.append(statement)

            engine = contexts.database.engine.sync_engine
            event.listen(engine, "before_cursor_execute", record)
            try:
                await contexts.process_memory("project", 1)
            finally:
                event.remove(engine, "before_cursor_execute", record)
            return len(statements)

    async def scenario():
        baseline = await process(False)
        assert await process(True) == baseline

    asyncio.run(scenario())


def test_recall_fetches_each_page_before_comparing_and_includes_alias_only_successor(tmp_path):
    from sqlalchemy import event

    page_size = 1
    pending_body_ids = []

    class PageReconciler(_RecordingCreateReconciler):
        async def generate(self, request):
            if request.related:
                assert 0 < len(pending_body_ids) <= page_size
                pending_body_ids.clear()
            return await super().generate(request)

    async def scenario():
        config = _config(tmp_path)
        config = config.model_copy(
            update={
                "runtime": config.runtime.model_copy(
                    update={"atomic_memory_alias_recall": True, "atomic_memory_related_page_size": page_size}
                )
            }
        )
        reconciler = PageReconciler()
        pipeline = AtomicMemoryGenerationPipeline(
            extractor=_Extractor(), reconciler=reconciler, estimator=character_token_estimator()
        )
        async with open_builtin_contexts(
            config, embedding_model=AliasEmbedding(), candidate_pipeline=pipeline
        ) as contexts:
            await seed_alias(contexts)
            direct = f"{OLD} Verify the metrics."
            await contexts.records.create_artifact(
                "project", "atomic-memory", ArtifactWrite(content={"kind": "fact", "text": direct})
            )
            context = await contexts.get("project")
            await context.triggers.flush(limit=200)
            reconciler.inputs.clear()
            await contexts.records.create_source("project", "content", OLD)

            def observe_bodies(_conn, _cursor, statement, parameters, _ctx, _many):
                if "current.text AS projected_text" in statement:
                    pending_body_ids.extend(parameters[1:])

            engine = contexts.database.engine.sync_engine
            event.listen(engine, "before_cursor_execute", observe_bodies)
            try:
                await context.triggers.flush(limit=1)
            finally:
                event.remove(engine, "before_cursor_execute", observe_bodies)
            assert [tuple(item.text for item in request.related) for request in reconciler.inputs] == [
                (direct,),
                (CURRENT,),
            ]
            assert not pending_body_ids

    asyncio.run(scenario())
