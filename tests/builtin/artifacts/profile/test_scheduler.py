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


import asyncio
from datetime import UTC, datetime
from functools import partial

import pytest
from pydantic import ValidationError

from powercontext.builtin.artifacts.profile.models import PROFILE_SOURCE_WINDOW_BINDING
from powercontext.builtin.artifacts.profile.service import ProfileGenerationInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig, open_builtin_runtime
from powercontext.builtin.runtime.artifact_processing import (
    ArtifactProcessingBinding,
    SpawnArtifactProcessingWorkerLauncher,
)
from powercontext.builtin.runtime.composition import open_builtin_contexts
from powercontext.builtin.runtime.cron import CronSchedule
from powercontext.builtin.runtime.family_processing import process_family_invocation
from powercontext.builtin.scope import ScopeDraft


def test_profile_cron_defaults_and_validation():
    config = RuntimeConfig()
    assert not config.profile_schedule_enabled
    schedule = CronSchedule.parse(config.profile_cron, config.profile_timezone)
    now = datetime(2026, 9, 6, 17, 0, tzinfo=UTC)
    assert schedule.next_after(now) == now.replace(hour=18, tzinfo=None)
    with pytest.raises((ValidationError, ValueError)):
        RuntimeConfig(profile_cron="invalid")
    with pytest.raises((ValidationError, KeyError)):
        RuntimeConfig(profile_timezone="Not/A_Zone")


class _ProfileGenerator:
    async def generate(self, value: ProfileGenerationInput):
        return "# Caught up"


def _profile_worker(config, assignment):
    async def run():
        async with open_builtin_contexts(config) as contexts:
            contexts.profiles.generator = _ProfileGenerator()
            return await process_family_invocation(contexts, assignment, config=config)

    return asyncio.run(run())


def test_startup_scan_catches_up_profile_evidence(tmp_path):
    async def run():
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            runtime=RuntimeConfig(artifact_processing_families=("profile",)),
        )
        # Register a reconstructible test child, with the production invocation
        # and completion protocol. Scope data outlives the initial Runtime.
        binding = ArtifactProcessingBinding(
            PROFILE_SOURCE_WINDOW_BINDING,
            "profile",
            SpawnArtifactProcessingWorkerLauncher(partial(_profile_worker, config)),
        )
        async with open_builtin_runtime(config, artifact_processing_bindings=(binding,)) as runtime:
            assert runtime.scopes is not None and runtime.profiles is not None
            sid = (
                await runtime.scopes.create(ScopeDraft(title="User", summary="User", idempotency_key="User"))
            ).scope_id
            await runtime.profiles.put_policy(sid, generation_enabled=True, expected_version=0)
            await runtime.records.for_scope(sid).create_source("content", "Chinese")
        scheduled = ArtifactProcessingBinding(
            PROFILE_SOURCE_WINDOW_BINDING,
            "profile",
            binding.launcher,
            cron="0 2 * * *",
            timezone="Asia/Shanghai",
        )
        async with open_builtin_runtime(config, artifact_processing_bindings=(scheduled,)) as runtime:
            from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
            from powercontext.builtin.runtime.relational import RelationalContexts

            assert isinstance(runtime._provider, RelationalContexts)
            async with asyncio.timeout(30):
                while True:
                    async with runtime._provider.database.transaction() as connection:
                        intent = await ArtifactProcessingIntentRepository().load(
                            connection, sid, PROFILE_SOURCE_WINDOW_BINDING
                        )
                    if intent is not None and intent.handled_generation >= 1:
                        break
                    await asyncio.sleep(0.02)
            saved = await runtime.records.for_scope(sid).get_artifact("profile", "profile")
            assert saved.revision == 1
        assert not (tmp_path / "jobs.db").exists()

    asyncio.run(run())


def test_manual_profile_generation_does_not_create_scheduler_when_disabled(tmp_path):
    class Generator:
        async def generate(self, value):
            return "# Manual flush"

    async def run():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'manual.db'}"))
        scheduler_path = tmp_path / "jobs.db"
        async with open_builtin_runtime(
            config, profile_generator=Generator(), scheduler_path=scheduler_path
        ) as runtime:
            assert runtime.scopes is not None and runtime.profiles is not None
            sid = (
                await runtime.scopes.create(ScopeDraft(title="User", summary="User", idempotency_key="User"))
            ).scope_id
            await runtime.profiles.put_policy(sid, generation_enabled=True, expected_version=0)
            await runtime.records.for_scope(sid).create_source("content", "Chinese")
            result = await runtime.profiles.flush(sid)
            assert result.status == "updated"
        assert not scheduler_path.exists()

    asyncio.run(run())
