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

"""Cron qualification must precede admission without changing explicit requests."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from powercontext.builtin.artifacts.profile.models import PROFILE_SOURCE_WINDOW_BINDING as BINDING
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.supervision import ArtifactProcessingBindingStateRepository
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.builtin.runtime import artifact_processing as processing
from powercontext.builtin.runtime.artifact_processing import ArtifactProcessingSupervisor
from powercontext.builtin.runtime.composition import _artifact_processing_bindings
from powercontext.builtin.runtime.config import BuiltinConfig, InferenceConfig, RuntimeConfig
from powercontext.builtin.runtime.family_processing import process_family_invocation
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.builtin.scope import ScopeDraft
from powercontext.server.authz import AccessDeniedError
from powercontext.server.authz.repository import ACCESS_TABLES
from powercontext.server.processing_security import open_worker_security
from tests.builtin.runtime.test_family_processing import security_spec
from tests.builtin.runtime.test_processing_scheduler import _intent, _Launcher


class _Clock:
    def __init__(self):
        self.now = datetime(2026, 9, 9, tzinfo=UTC).replace(tzinfo=None)

    async def read(self, _connection):
        return self.now

    def fire(self):
        self.now += timedelta(minutes=1)


class _ObservedIntents(ArtifactProcessingIntentRepository):
    def __init__(self):
        self.pages = []
        self.admissions = []

    async def scan(self, connection, binding_name, /, **kwargs):
        rows = await super().scan(connection, binding_name, **kwargs)
        if kwargs.get("dirty_only"):
            self.pages.append(tuple(row.scope_id for row in rows))
        return rows

    async def admit(self, connection, scope_id, binding_name, scan_generation, /):
        self.admissions.append((scope_id, scan_generation))
        return await super().admit(connection, scope_id, binding_name, scan_generation)


class _Generator:
    def __init__(self):
        self.inputs = []

    async def generate(self, value):
        self.inputs.append(value)
        return "# Preferences\n\nRetain the original Source until generation is enabled."


def _config(tmp_path):
    return BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'profile-admission.db'}"),
        inference=InferenceConfig(generation_model="test"),
        runtime=RuntimeConfig(
            artifact_processing_families=("profile",),
            profile_schedule_enabled=True,
            profile_cron="* * * * *",
            profile_timezone="UTC",
            profile_max_workers=1,
        ),
    )


async def _scope(contexts, name, *, enabled=None):
    scope = (await contexts.scopes.create(ScopeDraft(title=name, summary=name, idempotency_key=name))).scope_id
    await contexts.records.create_source(scope, "content", f"Original evidence for {name}.")
    if enabled is not None:
        await contexts.profiles.put_policy(scope, generation_enabled=enabled, expected_version=0)
    return scope


async def _dispatch(supervisor, state):
    await supervisor._dispatch(state)
    results = await asyncio.gather(*(worker.task for worker in state.running.values()), return_exceptions=True)
    await supervisor._reap(state)
    return results


async def _scan(supervisor, state):
    for _ in range(10):
        await supervisor._discover_automatic(state)
        await _dispatch(supervisor, state)
        async with supervisor._database.transaction() as connection:
            persisted = await ArtifactProcessingBindingStateRepository().load(connection, BINDING)
        if persisted is not None and not persisted.scan_in_progress:
            return persisted
    pytest.fail("finite automatic scan did not finish")


async def _unconsumed(contexts, scope):
    async with contexts.database.transaction() as connection:
        assert await SourceCursorRepository().load(connection, scope, BINDING) is None
        sources = await contexts.profiles.sources.list(connection, scope, after=0, limit=10)
        assert len(sources) == 1 and sources[0].journal_position == 1
        return sources[0].value


def test_profile_cron_skips_ineligible_prefix_across_fires_and_later_consumes_original_sources(tmp_path, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(processing, "database_utc_now", clock.read)
    monkeypatch.setattr(processing, "_DISCOVERY_PAGE_SIZE", 2)

    async def scenario():
        config = _config(tmp_path)
        assert isinstance(config.database, SQLiteConfig)
        async with SQLiteProfile.open(config.database, tables=BUILTIN_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database)
            generator = _Generator()
            contexts.profiles.generator = generator
            missing = await _scope(contexts, "missing")
            disabled = await _scope(contexts, "disabled", enabled=False)
            enabled = await _scope(contexts, "enabled", enabled=True)
            original = {scope: await _unconsumed(contexts, scope) for scope in (missing, disabled)}
            observed = _ObservedIntents()

            async def work(assignment):
                return await process_family_invocation(contexts, assignment, config=config)

            launcher = _Launcher(profile.database, on_work=work)
            # Use the production registration, including its qualification hook.
            binding = replace(_artifact_processing_bindings(config, contexts, ())[0], launcher=launcher)
            supervisor = ArtifactProcessingSupervisor(
                database=profile.database, bindings=(binding,), lease_mode="single-process", intents=observed
            )
            await supervisor._acquire()
            state = supervisor._families[BINDING]
            try:
                for generation in (1, 2):
                    saved = await _scan(supervisor, state)
                    assert saved.scan_generation == generation
                    assert saved.last_schedule_checkpoint_at == clock.now
                    assert state.retries == {}
                    for scope in (missing, disabled):
                        intent = await _intent(profile.database, BINDING, scope)
                        assert intent.requested_generation == intent.handled_generation == 0
                        assert intent.last_auto_scan_generation == 0
                        assert intent.dirty_generation > intent.clean_generation
                        assert await _unconsumed(contexts, scope) == original[scope]
                    clock.fire()
                assert observed.pages[0] == (missing, disabled)
                assert all(len(page) <= 2 for page in observed.pages)
                assert observed.admissions == [(enabled, 1)]
                assert [work.scope_id for work in launcher.assignments] == [enabled]
                assert len(generator.inputs) == 1

                # Policy writes wake existing dirty work; no new Source is added.
                await contexts.profiles.put_policy(missing, generation_enabled=True, expected_version=0)
                await contexts.profiles.put_policy(disabled, generation_enabled=True, expected_version=1)
                assert (await _scan(supervisor, state)).scan_generation == 3
                assert Counter(work.scope_id for work in launcher.assignments) == Counter({
                    missing: 1,
                    disabled: 1,
                    enabled: 1,
                })
                for scope in (missing, disabled):
                    intent = await _intent(profile.database, BINDING, scope)
                    assert intent.requested_generation == intent.handled_generation == 1
                    assert intent.last_auto_scan_generation == 3
                    assert intent.clean_generation == intent.dirty_generation
                    async with profile.database.transaction() as connection:
                        cursor = await SourceCursorRepository().load(connection, scope, BINDING)
                        artifact = await contexts.profiles.latest(connection, scope)
                        assert cursor is not None and cursor.cursor.sequence == 1
                        assert artifact is not None
                        assert artifact.content.generation.source_window is not None
                        assert artifact.content.generation.source_window.through == 1
                    assert any(original[scope].model_dump_json() in request.sources for request in generator.inputs)
                assert state.retries == {}
            finally:
                await supervisor._lose_leadership()

    asyncio.run(scenario())


@pytest.mark.parametrize("enabled", [None, False])
def test_explicit_profile_request_without_enabled_policy_is_acknowledged_and_remains_dirty(tmp_path, enabled):
    async def scenario():
        config = _config(tmp_path)
        assert isinstance(config.database, SQLiteConfig)
        async with SQLiteProfile.open(config.database, tables=BUILTIN_TABLES) as profile:
            contexts = RelationalContexts(database=profile.database)
            scope = await _scope(contexts, "explicit", enabled=enabled)
            original = await _unconsumed(contexts, scope)
            generator = _Generator()
            contexts.profiles.generator = generator

            async def work(assignment):
                return await process_family_invocation(contexts, assignment, config=config)

            launcher = _Launcher(profile.database, on_work=work)
            binding = replace(_artifact_processing_bindings(config, contexts, ())[0], launcher=launcher)
            supervisor = ArtifactProcessingSupervisor(
                database=profile.database, bindings=(binding,), lease_mode="single-process"
            )
            await supervisor._acquire()
            state = supervisor._families[BINDING]
            try:
                async with profile.database.transaction() as connection:
                    await ArtifactProcessingIntentRepository().request(connection, scope, BINDING)
                await supervisor._discover_requested(state)
                assert await _dispatch(supervisor, state)
                intent = await _intent(profile.database, BINDING, scope)
                assert intent.requested_generation == intent.handled_generation == 1
                assert intent.last_auto_scan_generation == 0
                assert intent.dirty_generation > intent.clean_generation
                assert [work.scope_id for work in launcher.assignments] == [scope]
                assert state.retries == {} and generator.inputs == []
                assert await _unconsumed(contexts, scope) == original
            finally:
                await supervisor._lose_leadership()

    asyncio.run(scenario())


def test_enforced_cron_qualifies_before_authorization_but_keeps_explicit_and_enabled_denials(tmp_path, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(processing, "database_utc_now", clock.read)
    monkeypatch.setattr(processing, "_DISCOVERY_PAGE_SIZE", 2)

    async def scenario():
        config = _config(tmp_path)
        assert isinstance(config.database, SQLiteConfig)
        async with SQLiteProfile.open(config.database, tables=(*BUILTIN_TABLES, *ACCESS_TABLES)) as profile:
            contexts = RelationalContexts(database=profile.database)
            missing = await _scope(contexts, "missing")
            disabled = await _scope(contexts, "disabled", enabled=False)
            enabled = await _scope(contexts, "enabled", enabled=True)
            observed = _ObservedIntents()
            authorizations = []
            policy_reads = []
            async with open_worker_security(security_spec(allowed=False), profile.database) as security:
                assert security is not None
                authorize = security.authorize_transaction
                read_policy = contexts.profiles.policies.get

                async def observed_authorize(connection, scope_id):
                    authorizations.append(scope_id)
                    await authorize(connection, scope_id)

                async def observed_policy(*args, **kwargs):
                    policy_reads.append(args[1])
                    return await read_policy(*args, **kwargs)

                monkeypatch.setattr(security, "authorize_transaction", observed_authorize)
                monkeypatch.setattr(contexts.profiles.policies, "get", observed_policy)

                async def work(assignment):
                    return await process_family_invocation(contexts, assignment, config=config, security=security)

                launcher = _Launcher(profile.database, on_work=work)
                binding = replace(_artifact_processing_bindings(config, contexts, ())[0], launcher=launcher)
                supervisor = ArtifactProcessingSupervisor(
                    database=profile.database,
                    bindings=(binding,),
                    lease_mode="single-process",
                    intents=observed,
                    retry_base_seconds=3600,
                    retry_cap_seconds=3600,
                )
                await supervisor._acquire()
                state = supervisor._families[BINDING]
                try:
                    for generation in (1, 2):
                        assert (await _scan(supervisor, state)).scan_generation == generation
                        assert set(state.retries) == {enabled}
                        for scope in (missing, disabled):
                            intent = await _intent(profile.database, BINDING, scope)
                            assert intent.requested_generation == intent.handled_generation == 0
                            assert intent.last_auto_scan_generation == 0
                        clock.fire()
                    assert observed.admissions == [(enabled, 1)]
                    assert authorizations == [enabled]
                    assert policy_reads == []  # Worker denial still precedes business Policy/Source reads.
                    assert [work.scope_id for work in launcher.assignments] == [enabled]
                    assert state.failed == 1

                    # Explicit work stays accepted and protected even for disabled Policies.
                    for scope in (missing, disabled):
                        async with profile.database.transaction() as connection:
                            await ArtifactProcessingIntentRepository().request(connection, scope, BINDING)
                    for _ in range(3):
                        await supervisor._discover_requested(state)
                        results = await _dispatch(supervisor, state)
                        assert all(isinstance(result, AccessDeniedError) for result in results)
                    assert Counter(authorizations) == Counter({missing: 1, disabled: 1, enabled: 1})
                    assert set(state.retries) == {missing, disabled, enabled}
                    assert policy_reads == []
                    for scope in (missing, disabled, enabled):
                        intent = await _intent(profile.database, BINDING, scope)
                        assert intent.requested_generation == 1 and intent.handled_generation == 0
                        assert intent.dirty_generation > intent.clean_generation
                        await _unconsumed(contexts, scope)
                finally:
                    await supervisor._lose_leadership()

    asyncio.run(scenario())
