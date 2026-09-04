# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import asyncio

import pytest

from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import COORDINATION_TABLES
from powercontext.builtin.runtime.config import CoordinationConfig, DeploymentConfig
from powercontext.builtin.runtime.membership import DuplicateSingleNodeError, RuntimeMembership


def test_single_node_membership_rejects_a_second_live_runtime() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=COORDINATION_TABLES) as profile:
            config = CoordinationConfig(member_ttl_seconds=3, member_heartbeat_seconds=1)
            deployment = DeploymentConfig()
            first = RuntimeMembership(
                database=profile.database,
                deployment=deployment,
                coordination=config,
                build_version="test",
            )
            second = RuntimeMembership(
                database=profile.database,
                deployment=deployment,
                coordination=config,
                build_version="test",
            )

            await first.start()
            with pytest.raises(DuplicateSingleNodeError):
                await second.start()

            await first.stop()
            await second.start()
            assert await second.readiness() == "ready"
            await second.stop()

    asyncio.run(scenario())


def test_api_membership_reports_missing_worker_and_scheduler_as_degraded_dependencies() -> None:
    async def scenario() -> None:
        async with SQLiteProfile.open(SQLiteConfig(), tables=COORDINATION_TABLES) as profile:
            membership = RuntimeMembership(
                database=profile.database,
                deployment=DeploymentConfig(mode="distributed", role="api", id="api-a"),
                coordination=CoordinationConfig(),
                build_version="test",
            )
            await membership.start()

            assert await membership.readiness() == "ready"
            assert await membership.role_readiness("scheduler") == "unavailable"
            assert await membership.role_readiness("worker") == "unavailable"
            await membership.stop()

    asyncio.run(scenario())
