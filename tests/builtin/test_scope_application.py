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
import re

import pytest
from sqlalchemy.exc import IntegrityError

from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.builtin.scope import (
    ScopeApplication,
    ScopeBindingKey,
    ScopeBindingNotFoundError,
    ScopeDraft,
    ScopeIdempotencyConflictError,
    ScopeMutation,
    ScopeRelationshipError,
    ScopeSelection,
)


def test_scope_bootstrap_creates_one_ordinary_default() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            scopes = ScopeApplication(profile.database)

            first = await scopes.bootstrap_default()
            second = await scopes.bootstrap_default()

            assert first == second
            assert re.fullmatch(r"scp_[0-7][0-9a-hjkmnp-tv-z]{25}", first.scope_id)
            assert first.parent_scope_id is None
            assert first.context_references == ()
            assert await scopes.list() == (first,)

    asyncio.run(scenario())


def test_scope_creation_is_idempotent_but_not_ambiguous() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            scopes = ScopeApplication(profile.database)
            draft = ScopeDraft(title="Feature", summary="Implement search", idempotency_key="feature-search")

            assert await scopes.create(draft) == await scopes.create(draft)
            with pytest.raises(ScopeIdempotencyConflictError):
                await scopes.create(
                    ScopeDraft(title="Feature", summary="Different work", idempotency_key="feature-search")
                )

    asyncio.run(scenario())


def test_concurrent_scope_creation_returns_one_scope(tmp_path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
        async with SQLiteProfile.open(config, tables=BUILTIN_TABLES) as profile:
            scopes = tuple(ScopeApplication(profile.database) for _ in range(8))
            draft = ScopeDraft(title="Feature", summary="Concurrent creation", idempotency_key="concurrent")

            created = await asyncio.gather(*(application.create(draft) for application in scopes))

            assert len({scope.scope_id for scope in created}) == 1

    asyncio.run(scenario())


def test_concurrent_scope_creation_rejects_a_different_request(tmp_path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
        async with SQLiteProfile.open(config, tables=BUILTIN_TABLES) as profile:
            left = ScopeApplication(profile.database)
            right = ScopeApplication(profile.database)

            results = await asyncio.gather(
                left.create(ScopeDraft(title="Feature", summary="Left", idempotency_key="concurrent")),
                right.create(ScopeDraft(title="Feature", summary="Right", idempotency_key="concurrent")),
                return_exceptions=True,
            )

            assert sum(isinstance(result, ScopeIdempotencyConflictError) for result in results) == 1
            assert sum(not isinstance(result, BaseException) for result in results) == 1

    asyncio.run(scenario())


def test_scope_creation_does_not_hide_an_unrelated_integrity_error() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            scope_id = "scp_00000000000000000000000000"
            left = ScopeApplication(profile.database, id_factory=lambda: scope_id)
            right = ScopeApplication(profile.database, id_factory=lambda: scope_id)
            await left.create(ScopeDraft(title="Left", summary="Left", idempotency_key="left"))

            with pytest.raises(IntegrityError):
                await right.create(ScopeDraft(title="Right", summary="Right", idempotency_key="right"))

    asyncio.run(scenario())


def test_concurrent_default_bootstrap_returns_one_scope(tmp_path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
        async with SQLiteProfile.open(config, tables=BUILTIN_TABLES) as profile:
            scopes = tuple(ScopeApplication(profile.database) for _ in range(8))

            defaults = await asyncio.gather(*(application.bootstrap_default() for application in scopes))

            assert len({scope.scope_id for scope in defaults}) == 1
            assert await scopes[0].default_scope() == defaults[0]

    asyncio.run(scenario())


def test_concurrent_default_and_binding_writes_remain_valid(tmp_path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
        async with SQLiteProfile.open(config, tables=BUILTIN_TABLES) as profile:
            applications = tuple(ScopeApplication(profile.database) for _ in range(8))
            targets = []
            for index in range(len(applications)):
                targets.append(
                    await applications[0].create(
                        ScopeDraft(title=f"Target {index}", summary="Target", idempotency_key=f"target-{index}")
                    )
                )
            target_scopes = tuple(targets)
            key = ScopeBindingKey(integration="codex", kind="workspace", external_id="shared")

            defaults = await asyncio.gather(
                *(
                    application.set_default(target.scope_id)
                    for application, target in zip(applications, target_scopes, strict=True)
                )
            )
            bindings = await asyncio.gather(
                *(
                    application.bind(key, target.scope_id)
                    for application, target in zip(applications, target_scopes, strict=True)
                )
            )

            target_ids = {target.scope_id for target in target_scopes}
            persisted_default = await applications[0].default_scope()
            persisted_binding = await applications[0].binding(key)
            assert tuple(defaults) == target_scopes
            assert {binding.scope_id for binding in bindings} == target_ids
            assert persisted_default is not None
            assert persisted_default.scope_id in target_ids
            assert persisted_binding is not None
            assert persisted_binding.scope_id in target_ids

    asyncio.run(scenario())


def test_parent_organizes_without_sharing_and_cannot_form_a_cycle() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            scopes = ScopeApplication(profile.database)
            root = await scopes.create(ScopeDraft(title="Root", summary="Root result", idempotency_key="root"))
            child = await scopes.create(
                ScopeDraft(
                    title="Child",
                    summary="Independent result",
                    parent_scope_id=root.scope_id,
                    idempotency_key="child",
                )
            )

            assert child.context_references == ()
            with pytest.raises(ScopeRelationshipError, match="acyclic"):
                await scopes.update(
                    root.scope_id,
                    ScopeMutation(
                        expected_version=root.version,
                        title=root.title,
                        summary=root.summary,
                        parent_scope_id=child.scope_id,
                    ),
                )

    asyncio.run(scenario())


def test_concurrent_parent_updates_cannot_form_a_cycle(tmp_path) -> None:
    async def scenario() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
        async with SQLiteProfile.open(config, tables=BUILTIN_TABLES) as profile:
            left_application = ScopeApplication(profile.database)
            right_application = ScopeApplication(profile.database)
            left = await left_application.create(ScopeDraft(title="Left", summary="Left", idempotency_key="left"))
            right = await left_application.create(ScopeDraft(title="Right", summary="Right", idempotency_key="right"))

            updates = await asyncio.gather(
                left_application.update(
                    left.scope_id,
                    ScopeMutation(
                        expected_version=left.version,
                        title=left.title,
                        summary=left.summary,
                        parent_scope_id=right.scope_id,
                    ),
                ),
                right_application.update(
                    right.scope_id,
                    ScopeMutation(
                        expected_version=right.version,
                        title=right.title,
                        summary=right.summary,
                        parent_scope_id=left.scope_id,
                    ),
                ),
                return_exceptions=True,
            )

            assert sum(isinstance(result, ScopeRelationshipError) for result in updates) == 1
            assert sum(not isinstance(result, BaseException) for result in updates) == 1
            persisted = {scope.scope_id: scope for scope in await left_application.list()}
            assert not (
                persisted[left.scope_id].parent_scope_id == right.scope_id
                and persisted[right.scope_id].parent_scope_id == left.scope_id
            )

    asyncio.run(scenario())


def test_context_reference_order_does_not_change_scope_creation() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            scopes = ScopeApplication(profile.database)
            shared = await scopes.create(ScopeDraft(title="Shared", summary="Shared facts", idempotency_key="shared"))
            middle = await scopes.create(
                ScopeDraft(
                    title="Middle",
                    summary="Direct reader",
                    context_references=(shared.scope_id,),
                    idempotency_key="middle",
                )
            )
            current = await scopes.create(
                ScopeDraft(
                    title="Current",
                    summary="Reads only Middle",
                    context_references=(middle.scope_id, shared.scope_id),
                    idempotency_key="current",
                )
            )

            assert current.context_references == tuple(sorted((middle.scope_id, shared.scope_id)))
            assert (
                await scopes.create(
                    ScopeDraft(
                        title="Current",
                        summary="Reads only Middle",
                        context_references=(shared.scope_id, middle.scope_id),
                        idempotency_key="current",
                    )
                )
                == current
            )

    asyncio.run(scenario())


def test_exact_selection_is_a_canonical_set() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            scopes = ScopeApplication(profile.database)
            left = await scopes.create(ScopeDraft(title="Left", summary="Left", idempotency_key="left"))
            right = await scopes.create(ScopeDraft(title="Right", summary="Right", idempotency_key="right"))

            first = ScopeSelection(mode="exact", scope_ids=(left.scope_id, right.scope_id))
            second = ScopeSelection(mode="exact", scope_ids=(right.scope_id, left.scope_id))

            assert first == second
            assert await scopes.resolve_selection(first) == await scopes.resolve_selection(second)

    asyncio.run(scenario())


def test_binding_resolution_uses_explicit_durable_then_default_precedence() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            scopes = ScopeApplication(profile.database)
            default = await scopes.bootstrap_default()
            durable = await scopes.create(
                ScopeDraft(title="Durable", summary="Durable binding", idempotency_key="durable")
            )
            explicit = await scopes.create(
                ScopeDraft(title="Explicit", summary="Explicit binding", idempotency_key="explicit")
            )
            missing_key = ScopeBindingKey(integration="codex", kind="session", external_id="missing")
            durable_key = ScopeBindingKey(integration="codex", kind="workspace", external_id="workspace")
            await scopes.bind(durable_key, durable.scope_id)

            assert (
                await scopes.resolve_binding(
                    explicit_scope_id=explicit.scope_id,
                    binding_keys=(durable_key,),
                )
                == explicit
            )
            assert await scopes.resolve_binding(binding_keys=(missing_key, durable_key)) == durable
            assert await scopes.resolve_binding(binding_keys=(missing_key,)) == default

            await scopes.clear_binding(durable_key)
            assert await scopes.resolve_binding(binding_keys=(durable_key,)) == default

    asyncio.run(scenario())


def test_binding_resolution_fails_without_any_binding() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile:
            scopes = ScopeApplication(profile.database)

            with pytest.raises(ScopeBindingNotFoundError):
                await scopes.resolve_binding()

    asyncio.run(scenario())
