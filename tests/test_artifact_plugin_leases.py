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

"""Plugin activation preserves the renewable Supervisor ownership contract."""

import asyncio

from pydantic import SecretStr

from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.supervision import ArtifactProcessingLeaseRepository
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.server.artifact_plugins import ArtifactPluginManager
from tests.e2e.test_artifact_plugins import EXAMPLE, deploy_plugin


def test_first_plugin_activation_preserves_an_unexpired_oceanbase_owner(tmp_path):
    deploy_plugin(EXAMPLE, tmp_path / "plugins" / "incident-reports", None)

    async def run():
        # SQLite hosts real lease state; OceanBaseConfig exercises configuration
        # selection without claiming acceptance against an OceanBase server.
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as storage:
            database = storage.database
            leases = ArtifactProcessingLeaseRepository()
            async with database.transaction() as connection:
                owner = await leases.try_acquire(connection, "existing-background-owner", 3600)
            assert owner is not None and owner.lease_expires_at is not None
            fence = owner.fence("oceanbase")
            async with ArtifactPluginManager(
                database=database,
                config=OceanBaseConfig(url=SecretStr("mysql+aoceanbase://test@localhost:2881/test?charset=utf8mb4")),
                supervisor=None,
                security_builder=lambda: None,
                on_supervisor=lambda _: None,
                plugins_dir=tmp_path / "plugins",
            ) as manager:
                registration = await manager.get("incident-reports")
                assert registration.phase == "disabled"
                await manager.change("incident-reports", registration.binding_generation, "enabled")
                async with asyncio.timeout(10):
                    while (state := await manager.get("incident-reports")).phase == "activating":  # noqa: ASYNC110
                        await asyncio.sleep(0.01)
                assert state.phase == "enabled", state.last_error
                async with database.transaction() as connection:
                    assert await leases.require_fence(connection, fence) == owner
            # Closing the standby must not release another Supervisor's term.
            async with database.transaction() as connection:
                assert await leases.require_fence(connection, fence) == owner

    asyncio.run(run())
