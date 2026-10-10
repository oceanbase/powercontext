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
import gc
from typing import cast
from weakref import ref

import pytest

from powercontext.builtin.artifacts.experience import ExperienceCandidateInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.builtin.scope import ScopeDraft
from powercontext.limits import MAX_SCOPE_ID_LENGTH
from powercontext.sources import Source


class _EmptyExperiencePipeline:
    async def incubate(self, sources: tuple[Source, ...], /) -> tuple[ExperienceCandidateInput, ...]:
        return ()


def test_get_shares_the_cached_context_between_concurrent_callers() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database)
            first, second = await asyncio.gather(contexts.get("scope-a"), contexts.get("scope-a"))

            assert first is second
            assert await contexts.get("scope-a") is first

            contexts.evict("scope-a")
            replacement, shared = await asyncio.gather(contexts.get("scope-a"), contexts.get("scope-a"))

            assert replacement is shared
            assert replacement is not first

    asyncio.run(scenario())


def test_evict_removes_scope_skill_publication_locks() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database)
            services = [
                contexts.skill_publications("scope-a", "target-1", "skill-1"),
                contexts.skill_publications("scope-a", "target-2", "skill-1"),
                contexts.skill_publications("scope-a", "target-1", "skill-2"),
            ]
            other = contexts.skill_publications("scope-b", "target-1", "skill-1")

            assert all(isinstance(service._lock, asyncio.Lock) for service in services)
            assert len({id(service._lock) for service in services}) == 3
            assert all(service._lock is not other._lock for service in services)
            assert contexts.skill_publications("scope-a", "target-1", "skill-1")._lock is services[0]._lock
            lock_refs = tuple(ref(service._lock) for service in services)
            del services

            contexts.evict("scope-a")
            gc.collect()

            assert all(lock_ref() is None for lock_ref in lock_refs)
            assert contexts.skill_publications("scope-b", "target-1", "skill-1")._lock is other._lock
            replacement = contexts.skill_publications("scope-a", "target-1", "skill-1")
            assert isinstance(replacement._lock, asyncio.Lock)
            assert contexts.skill_publications("scope-a", "target-1", "skill-1")._lock is replacement._lock

    asyncio.run(scenario())


def test_evict_releases_scope_composition_and_all_serialization_locks() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database, experience_pipeline=_EmptyExperiencePipeline())
            scope = (
                await contexts.scopes.create(
                    ScopeDraft(title="Evicted", summary="Evicted scope", idempotency_key="evicted")
                )
            ).scope_id
            other_scope = (
                await contexts.scopes.create(
                    ScopeDraft(title="Retained", summary="Retained scope", idempotency_key="retained")
                )
            ).scope_id
            for scope_id in (scope, other_scope):
                await contexts.get(scope_id)
                memory = await contexts.process_memory(scope_id, 1)
                experience = await contexts.incubate_experience(scope_id, 1)
                assert memory.source_count == 0
                assert experience.source_count == 0
                contexts.skill_publications(scope_id, "target-1", "skill-1")

            state = contexts._scope_states[scope]
            assert state.context is not None
            resource_refs = tuple(
                ref(resource)
                for resource in (
                    state.context,
                    *state.scope_locks.values(),
                    *state.skill_publication_locks.values(),
                )
            )
            del state
            other_state = contexts._scope_states[other_scope]
            other_locks = other_state.scope_locks.copy()
            other_context = await contexts.get(other_scope)
            other_publication = contexts.skill_publications(other_scope, "target-1", "skill-1")
            assert all(resource_ref() is not None for resource_ref in resource_refs)

            contexts.evict(scope)
            gc.collect()

            assert all(resource_ref() is None for resource_ref in resource_refs)
            assert contexts._scope_states[other_scope] is other_state
            assert other_state.scope_locks == other_locks
            assert await contexts.get(other_scope) is other_context
            assert contexts.skill_publications(other_scope, "target-1", "skill-1")._lock is other_publication._lock
            replacement = await contexts.get(scope)
            assert await contexts.get(scope) is replacement
            assert (await contexts.process_memory(scope, 1)).source_count == 0
            assert (await contexts.incubate_experience(scope, 1)).source_count == 0

    asyncio.run(scenario())


def test_evict_uncached_and_repeated_scopes_is_a_noop() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database)
            contexts.evict("scope-a")
            assert not contexts._scope_states

            context = await contexts.get("scope-b")
            publication = contexts.skill_publications("scope-b", "target-1", "skill-1")
            contexts.evict("scope-a")
            assert await contexts.get("scope-b") is context
            assert contexts.skill_publications("scope-b", "target-1", "skill-1")._lock is publication._lock

            contexts.evict("scope-b")
            contexts.evict("scope-b")
            assert not contexts._scope_states
            assert await contexts.get("scope-b") is not context
            assert contexts.skill_publications("scope-b", "target-1", "skill-1")._lock is not publication._lock

    asyncio.run(scenario())


@pytest.mark.parametrize("scope_id", ["s", "s" * MAX_SCOPE_ID_LENGTH, " scope-a "])
def test_evict_preserves_valid_scope_identity_and_allows_reconstruction(scope_id: str) -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database)
            context = await contexts.get(scope_id)
            publication = contexts.skill_publications(scope_id, "target-1", "skill-1")
            other = contexts.skill_publications("scope-a", "target-1", "skill-1")

            contexts.evict(scope_id)

            assert await contexts.get(scope_id) is not context
            assert contexts.skill_publications(scope_id, "target-1", "skill-1")._lock is not publication._lock
            assert contexts.skill_publications("scope-a", "target-1", "skill-1")._lock is other._lock

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("scope_id", "message"),
    [
        (None, "scope_id must not be empty"),
        ("", "scope_id must not be empty"),
        (" \t\n", "scope_id must not be empty"),
        ("s" * (MAX_SCOPE_ID_LENGTH + 1), f"scope_id must not exceed {MAX_SCOPE_ID_LENGTH} characters"),
    ],
)
def test_invalid_scopes_are_rejected_without_changing_cached_resources(scope_id: str | None, message: str) -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database)
            context = await contexts.get("scope-a")
            publication = contexts.skill_publications("scope-a", "target-1", "skill-1")
            invalid_scope = cast(str, scope_id)

            with pytest.raises(ValueError, match=message):
                contexts.evict(invalid_scope)
            with pytest.raises(ValueError, match=message):
                contexts.skill_publications(invalid_scope, "target-1", "skill-1")
            with pytest.raises(ValueError, match=message):
                await contexts.get(invalid_scope)
            with pytest.raises(ValueError, match=message):
                await contexts.process_memory(invalid_scope, 1)
            with pytest.raises(ValueError, match=message):
                await contexts.incubate_experience(invalid_scope, 1)

            assert tuple(contexts._scope_states) == ("scope-a",)
            assert await contexts.get("scope-a") is context
            assert contexts.skill_publications("scope-a", "target-1", "skill-1")._lock is publication._lock

    asyncio.run(scenario())


def test_experience_incubation_without_pipeline_keeps_its_error_after_eviction() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database)
            with pytest.raises(RuntimeError, match="Experience incubation pipeline is not configured"):
                await contexts.incubate_experience("scope-a", 1)

            contexts.evict("scope-a")

            with pytest.raises(RuntimeError, match="Experience incubation pipeline is not configured"):
                await contexts.incubate_experience("scope-a", 1)

    asyncio.run(scenario())
