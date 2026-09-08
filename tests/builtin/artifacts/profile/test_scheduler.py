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
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from apscheduler.triggers.cron import CronTrigger
from pydantic import ValidationError

from powercontext.builtin.artifacts.profile.service import ProfileGenerationInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig, open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft


def test_profile_cron_defaults_and_validation():
    config = RuntimeConfig()
    assert not config.profile_schedule_enabled
    trigger = CronTrigger.from_crontab(config.profile_cron, timezone=ZoneInfo(config.profile_timezone))
    now = datetime(2026, 9, 7, 1, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert trigger.get_next_fire_time(None, now) == now.replace(hour=2)
    with pytest.raises((ValidationError, ValueError)):
        RuntimeConfig(profile_cron="invalid")
    with pytest.raises((ValidationError, KeyError)):
        RuntimeConfig(profile_timezone="Not/A_Zone")


def test_startup_scan_catches_up_profile_evidence(tmp_path):
    class Generator:
        def __init__(self):
            self.called = asyncio.Event()

        async def generate(self, value: ProfileGenerationInput):
            self.called.set()
            return "# Caught up"

    async def run():
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"))
        async with open_builtin_runtime(config, scheduler_path=tmp_path / "jobs.db") as runtime:
            assert runtime.scopes is not None and runtime.profiles is not None
            sid = (
                await runtime.scopes.create(ScopeDraft(title="User", summary="User", idempotency_key="User"))
            ).scope_id
            await runtime.profiles.put_policy(sid, generation_enabled=True, expected_version=0)
            await runtime.records.for_scope(sid).create_source("content", "Chinese")
        generator = Generator()
        async with open_builtin_runtime(
            config.model_copy(update={"runtime": RuntimeConfig(profile_schedule_enabled=True)}),
            profile_generator=generator,
            scheduler_path=tmp_path / "jobs.db",
        ) as runtime:
            await asyncio.wait_for(generator.called.wait(), timeout=5)
        # Runtime.close drains in-flight Profile work before disposing persistence.
        async with open_builtin_runtime(config, scheduler_path=tmp_path / "jobs.db") as runtime:
            saved = await runtime.records.for_scope(sid).get_artifact("profile", "profile")
            assert saved.revision == 1

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
