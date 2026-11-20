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

"""Alias channel lifecycle, endpoint filtering and failure behavior on real SQLite."""

from __future__ import annotations

import asyncio
from itertools import pairwise

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError

from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryGenerationPipeline
from powercontext.builtin.authz import AccessAction, AccessUnavailableError, PrincipalRef, ResourceRef
from powercontext.builtin.inference import character_token_estimator
from powercontext.builtin.persistence.atomic_memory_index import (
    AtomicMemoryIndexError,
    AtomicMemoryIndexFilter,
    AtomicMemoryProjectionPublisher,
    AtomicMemoryRelatedRequest,
)
from powercontext.builtin.persistence.sqlite.atomic_memory_index import SQLiteAtomicMemoryIndex
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import open_builtin_contexts
from powercontext.builtin.tags import ArtifactTagTarget, TagFilter
from tests.e2e.test_atomic_memory_alias_recall import (
    CURRENT,
    OLD,
    OTHER,
    AliasEmbedding,
    RecordingReconciler,
    seed_alias,
)
from tests.e2e.test_atomic_memory_related_embedding import (
    PROFILE,
    _config,
    _configured_flush_context,
    _Extractor,
    _RecordingCreateReconciler,
)


def _request(*, mode="vector", filters=None):
    return AtomicMemoryRelatedRequest(
        OLD,
        filters or AtomicMemoryIndexFilter(),
        mode=mode,
        query_vector=(1.0, 0.0, 0.0),
        embedding_profile=PROFILE,
        max_distance=0.1,
    )


async def _recall(contexts, index, request=None, *, scope="project"):
    async with contexts.database.transaction(consistent_snapshot=True) as connection:
        return await index.enumerate_aliased_related(connection, scope, request or _request())


@pytest.mark.parametrize("mode", ["fts", "vector", "hybrid"])
def test_alias_modes_return_current_body_and_scope_local_authoritative_heads(tmp_path, mode):
    async def scenario():
        async with open_builtin_contexts(_config(tmp_path), embedding_model=AliasEmbedding()) as contexts:
            inputs, successor = await seed_alias(contexts)
            index = SQLiteAtomicMemoryIndex(PROFILE)
            hits, heads = await _recall(contexts, index, _request(mode=mode))
            assert tuple(hit.artifact_ref for hit in hits) == (successor.ref,)
            assert hits[0].text == CURRENT
            assert hits[0].state_version == successor.state.state_version
            assert heads[successor.ref.artifact_id]["revision"] == successor.ref.revision
            assert all(item.ref.artifact_id not in heads for item in inputs)
            assert await _recall(contexts, index, scope="other-scope") == ((), {})

    asyncio.run(scenario())


def test_alias_follows_successive_merges_and_filters_endpoint_kind_and_tags(tmp_path):
    async def scenario():
        async with open_builtin_contexts(_config(tmp_path), embedding_model=AliasEmbedding()) as contexts:
            _, first = await seed_alias(contexts)
            memory = contexts.atomic_memory.for_scope("project")
            created = await contexts.records.create_artifact(
                "project", "atomic-memory", ArtifactWrite(content={"kind": "fact", "text": "Another rollout rule."})
            )
            other = await memory.get(created.artifact_id)
            endpoint = (
                await memory.merge(
                    (first.as_read(), other.as_read()),
                    AtomicMemoryContent(kind="constraint", text="Final rollout policy."),
                )
            ).primary
            target = ArtifactTagTarget(family="atomic-memory", artifact_id=endpoint.ref.artifact_id)
            tags = await contexts.records.get_tags("project", target)
            await contexts.records.replace_tags("project", target, ("canonical",), expected_etag=tags.etag)
            index = SQLiteAtomicMemoryIndex(PROFILE)
            for filters, expected in (
                (AtomicMemoryIndexFilter(kind="constraint"), True),
                (AtomicMemoryIndexFilter(kind="fact"), False),
                (AtomicMemoryIndexFilter(tag_filter=TagFilter(tags=("canonical",))), True),
                (AtomicMemoryIndexFilter(tag_filter=TagFilter(tags=("historical",))), False),
                (AtomicMemoryIndexFilter(tag_filter=TagFilter(tags=("canonical", "missing"), match="any")), True),
                (AtomicMemoryIndexFilter(tag_filter=TagFilter(tags=("canonical", "missing"), match="all")), False),
            ):
                hits, _ = await _recall(contexts, index, _request(filters=filters))
                assert tuple(hit.artifact_ref for hit in hits) == ((endpoint.ref,) if expected else ())

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["forget", "restore_input"])
def test_inactive_endpoint_and_restored_alias_source_supply_no_alias(tmp_path, operation):
    async def scenario():
        async with open_builtin_contexts(_config(tmp_path), embedding_model=AliasEmbedding()) as contexts:
            inputs, successor = await seed_alias(contexts)
            memory = contexts.atomic_memory.for_scope("project")
            index = SQLiteAtomicMemoryIndex(PROFILE)
            if operation == "forget":
                await memory.forget(
                    successor.ref.artifact_id,
                    expected_revision=successor.ref.revision,
                    expected_state_version=successor.state.state_version,
                )
            else:
                await memory.restore(inputs[0].ref.artifact_id)
                assert (await memory.get(successor.ref.artifact_id)).state.state == "retired"
                async with contexts.database.transaction() as connection:
                    assert (
                        await connection.scalar(
                            select(index.alias_table.c.alias_artifact_id).where(
                                index.alias_table.c.scope_id == "project",
                                index.alias_table.c.alias_artifact_id == inputs[0].ref.artifact_id,
                            )
                        )
                        is None
                    )
            assert await _recall(contexts, index) == ((), {})

    asyncio.run(scenario())


def test_repeat_retirement_preserves_original_alias_and_vector_bytes(tmp_path):
    async def scenario():
        async with open_builtin_contexts(_config(tmp_path), embedding_model=AliasEmbedding()) as contexts:
            inputs, _ = await seed_alias(contexts)
            index = SQLiteAtomicMemoryIndex(PROFILE)
            publisher = AtomicMemoryProjectionPublisher(index, embedding_model=AliasEmbedding())
            merged = await contexts.atomic_memory.for_scope("project").get(inputs[0].ref.artifact_id)
            async with contexts.database.transaction() as connection:
                predicate = (index.alias_table.c.scope_id == "project") & (
                    index.alias_table.c.alias_artifact_id == merged.ref.artifact_id
                )
                before = (await connection.execute(select(index.alias_table).where(predicate))).mappings().one()
                await publisher.retire(connection, "project", merged)
                after = (await connection.execute(select(index.alias_table).where(predicate))).mappings().one()
                assert dict(after) == dict(before)
                assert after["embedding"] == b"\x00\x00\x80?\x00\x00\x00\x00\x00\x00\x00\x00"

    asyncio.run(scenario())


@pytest.mark.parametrize("corruption", ["cycle", "missing", "cross_scope", "depth"])
def test_invalid_alias_chains_fail_without_guessing_endpoint(tmp_path, corruption):
    async def scenario():
        async with open_builtin_contexts(_config(tmp_path), embedding_model=AliasEmbedding()) as contexts:
            inputs, successor = await seed_alias(contexts)
            index = SQLiteAtomicMemoryIndex(PROFILE)
            root = inputs[0].ref.artifact_id
            heads = ARTIFACT_HEADS_TABLE
            extra_ids = []
            foreign_id = None
            if corruption == "depth":
                for number in range(65):
                    created = await contexts.records.create_artifact(
                        "project",
                        "atomic-memory",
                        ArtifactWrite(content={"kind": "fact", "text": f"Depth fixture {number}."}),
                    )
                    extra_ids.append(created.artifact_id)
            if corruption == "cross_scope":
                await contexts.get("another-scope")
                foreign = await contexts.records.create_artifact(
                    "another-scope",
                    "atomic-memory",
                    ArtifactWrite(content={"kind": "fact", "text": "Foreign endpoint."}),
                )
                foreign_id = foreign.artifact_id
            async with contexts.database.transaction() as connection:
                predicate = (heads.c.scope_id == "project") & (heads.c.artifact_id == successor.ref.artifact_id)
                if corruption == "cycle":
                    await connection.execute(
                        update(heads).where(predicate).values(lifecycle_state="deprecated", merged_into_id=root)
                    )
                elif corruption == "missing":
                    await connection.execute(heads.delete().where(predicate))
                elif corruption == "cross_scope":
                    assert foreign_id is not None
                    await connection.execute(
                        update(heads)
                        .where(heads.c.scope_id == "project", heads.c.artifact_id == root)
                        .values(merged_into_id=foreign_id)
                    )
                    await connection.execute(
                        update(index.alias_table)
                        .where(index.alias_table.c.scope_id == "project", index.alias_table.c.alias_artifact_id == root)
                        .values(target_artifact_id=foreign_id)
                    )
                else:
                    for number, identity in enumerate(extra_ids[:-1]):
                        await connection.execute(
                            update(heads)
                            .where(heads.c.scope_id == "project", heads.c.artifact_id == identity)
                            .values(lifecycle_state="deprecated", merged_into_id=extra_ids[number + 1])
                        )
                    await connection.execute(
                        update(heads).where(predicate).values(lifecycle_state="deprecated", merged_into_id=extra_ids[0])
                    )
            with pytest.raises(AtomicMemoryIndexError) as error:
                await _recall(contexts, index)
            assert error.value.code == "alias-chain"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "failure",
    [
        OperationalError(None, None, RuntimeError("alias unavailable")),
        AtomicMemoryIndexError("alias-chain", "Alias chain has missing in-Scope authority"),
        asyncio.CancelledError(),
    ],
)
@pytest.mark.parametrize("enabled", [False, True])
def test_alias_failure_fallback_flag_off_and_cancellation(tmp_path, monkeypatch, failure, enabled):
    async def failed(*args, **kwargs):
        raise failure

    async def scenario():
        config = _config(tmp_path)
        config = config.model_copy(
            update={"runtime": config.runtime.model_copy(update={"atomic_memory_alias_recall": enabled})}
        )
        recorder = RecordingReconciler()
        pipeline = AtomicMemoryGenerationPipeline(
            extractor=_Extractor(), reconciler=recorder, estimator=character_token_estimator()
        )
        async with open_builtin_contexts(
            config, embedding_model=AliasEmbedding(), candidate_pipeline=pipeline
        ) as contexts:
            await seed_alias(contexts)
            context = await contexts.get("project")
            await context.triggers.flush(limit=200)
            cursor = await context.triggers.cursor()
            monkeypatch.setattr(SQLiteAtomicMemoryIndex, "enumerate_aliased_related", failed)
            await contexts.records.create_source("project", "content", OTHER)
            if enabled and isinstance(failure, (AtomicMemoryIndexError, asyncio.CancelledError)):
                with pytest.raises(type(failure)):
                    await contexts.process_memory("project", 1)
                assert await context.triggers.cursor() == cursor
                assert not recorder.requests
            else:
                await contexts.process_memory("project", 1)
                assert [item.text for request in recorder.requests for item in request.related] == [CURRENT]
                assert recorder.actions == ["noop"]

    asyncio.run(scenario())


def test_direct_and_alias_overlap_is_compared_once(tmp_path):
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
            await (await contexts.get("project")).triggers.flush(limit=200)
            await contexts.records.create_source("project", "content", OTHER)
            await contexts.process_memory("project", 1)
            assert [item.text for request in recorder.requests for item in request.related] == [CURRENT]
            assert recorder.actions == ["noop"]
            assert tuple(item.ref for item in (await contexts.atomic_memory.for_scope("project").list()).items) == (
                successor.ref,
            )

    asyncio.run(scenario())


def test_incompatible_alias_vector_is_not_queried_under_current_profile(tmp_path):
    async def scenario():
        async with open_builtin_contexts(_config(tmp_path), embedding_model=AliasEmbedding()) as contexts:
            inputs, successor = await seed_alias(contexts)
            index = SQLiteAtomicMemoryIndex(PROFILE)
            async with contexts.database.transaction() as connection:
                await connection.execute(
                    update(index.alias_table)
                    .where(
                        index.alias_table.c.scope_id == "project",
                        index.alias_table.c.alias_artifact_id == inputs[0].ref.artifact_id,
                    )
                    .values(embedding=b"\x00\x00\x80?", profile_fingerprint="0" * 64)
                )
            assert await _recall(contexts, index) == ((), {})
            hits, _ = await _recall(contexts, index, _request(mode="fts"))
            assert tuple(hit.artifact_ref for hit in hits) == (successor.ref,)

    asyncio.run(scenario())


def test_alias_and_direct_authority_reads_share_consistent_snapshot(tmp_path):
    async def scenario():
        async with open_builtin_contexts(_config(tmp_path), embedding_model=AliasEmbedding()) as contexts:
            _, successor = await seed_alias(contexts)
            index = SQLiteAtomicMemoryIndex(PROFILE)
            request = _request()
            async with contexts.database.transaction(consistent_snapshot=True) as connection:
                assert await index.enumerate_related_with_heads(connection, "project", request) == ((), {})
                revised = await asyncio.wait_for(
                    asyncio.create_task(
                        contexts.records.replace_artifact(
                            "project",
                            "atomic-memory",
                            successor.ref.artifact_id,
                            '"revision:1"',
                            ArtifactWrite(content={"kind": "fact", "text": "Changed rollout policy."}),
                        )
                    ),
                    timeout=15,
                )
                assert revised.revision == 2
                hits, heads = await index.enumerate_aliased_related(connection, "project", request)
                assert hits[0].artifact_ref == successor.ref
                assert hits[0].text == CURRENT
                assert heads[successor.ref.artifact_id]["revision"] == 1
            hits, _ = await _recall(contexts, index)
            assert hits[0].artifact_ref.revision == 2
            assert hits[0].text == "Changed rollout policy."

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["denied", "unavailable"])
def test_alias_only_successor_still_requires_artifact_authority(tmp_path, failure):
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
            context = await contexts.get("project")
            execution, provider = await _configured_flush_context(contexts, PrincipalRef(type="user", id="owner"))
            created = await contexts.records.create_atomic_memories(
                "project",
                tuple({"kind": "fact", "text": text, "description": "Fixture rule"} for text in (OLD, OTHER)),
                execution_context=execution,
            )
            memory = contexts.atomic_memory.for_scope("project")
            inputs = await memory.get_many(tuple(item.artifact_id for item in created), context=execution)
            successor = (
                await memory.merge(
                    tuple(item.as_read() for item in inputs),
                    AtomicMemoryContent(kind="fact", text=CURRENT),
                    context=execution,
                )
            ).primary
            await context.triggers.flush(limit=200)
            authority = (
                AccessAction.ARTIFACT_READ,
                ResourceRef.artifact("project", family="atomic-memory", artifact_id=successor.ref.artifact_id),
            )
            if failure == "denied":
                provider.denied = authority
            else:
                provider.unavailable = authority
            cursor = (await context.triggers.cursor()).sequence
            await contexts.records.create_source("project", "content", OLD)
            if failure == "unavailable":
                with pytest.raises(AccessUnavailableError):
                    await context.triggers.flush(limit=1, atomic_context=execution)
                assert (await context.triggers.cursor()).sequence == cursor
                assert not recorder.requests
            else:
                await context.triggers.flush(limit=1, atomic_context=execution)
                assert not any(request.related for request in recorder.requests)
                assert recorder.actions == ["create"]
                assert all(CURRENT not in request.model_dump_json() for request in recorder.requests)

    asyncio.run(scenario())


def test_alias_candidate_does_not_force_reconciliation_to_merge(tmp_path):
    async def scenario():
        config = _config(tmp_path)
        config = config.model_copy(
            update={"runtime": config.runtime.model_copy(update={"atomic_memory_alias_recall": True})}
        )
        recorder = _RecordingCreateReconciler()
        pipeline = AtomicMemoryGenerationPipeline(
            extractor=_Extractor(), reconciler=recorder, estimator=character_token_estimator()
        )
        async with open_builtin_contexts(
            config, embedding_model=AliasEmbedding(), candidate_pipeline=pipeline
        ) as contexts:
            _, successor = await seed_alias(contexts)
            await (await contexts.get("project")).triggers.flush(limit=200)
            await contexts.records.create_source("project", "content", OLD)
            await contexts.process_memory("project", 1)
            assert [item.text for request in recorder.inputs for item in request.related] == [CURRENT]
            items = (await contexts.atomic_memory.for_scope("project").list()).items
            assert len(items) == 2
            assert successor.ref in tuple(item.ref for item in items)
            assert {item.artifact.content.text for item in items} == {CURRENT, OLD}

    asyncio.run(scenario())


def test_alias_chain_accepts_exactly_64_merge_edges(tmp_path):
    async def scenario():
        async with open_builtin_contexts(_config(tmp_path), embedding_model=AliasEmbedding()) as contexts:
            _, successor = await seed_alias(contexts)
            extra = await contexts.records.create_atomic_memories(
                "project",
                tuple(
                    {"kind": "fact", "text": f"Depth fixture {number}.", "description": "Depth fact"}
                    for number in range(63)
                ),
            )
            ids = [successor.ref.artifact_id, *(item.artifact_id for item in extra)]
            heads = ARTIFACT_HEADS_TABLE
            async with contexts.database.transaction() as connection:
                for identity, target in pairwise(ids):
                    await connection.execute(
                        update(heads)
                        .where(heads.c.scope_id == "project", heads.c.artifact_id == identity)
                        .values(lifecycle_state="deprecated", merged_into_id=target)
                    )
            hits, _ = await _recall(contexts, SQLiteAtomicMemoryIndex(PROFILE))
            assert len(hits) == 1
            assert hits[0].artifact_ref.artifact_id == extra[-1].artifact_id
            assert hits[0].text == "Depth fixture 62."

    asyncio.run(scenario())
