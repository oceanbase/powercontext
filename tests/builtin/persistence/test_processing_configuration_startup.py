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

"""Adopting generation models on an initialized model-free deployment is one offline switch."""

import asyncio
import os

import pytest
from pydantic import AnyHttpUrl

from powercontext.builtin.persistence import processing_migration
from powercontext.builtin.persistence.migrations.deployment import _migration_configuration
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.migrations.server_readiness import prepare_server_storage
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.runtime.config import InferenceConfig, RuntimeConfig
from powercontext.server.settings import ServerSettings

GENERATION = InferenceConfig(
    generation_model="openai-chat:test-model", generation_base_url=AnyHttpUrl("http://127.0.0.1:9/v1")
)


@pytest.fixture(autouse=True)
def clean_server_environment(monkeypatch):
    for key in tuple(os.environ):
        if key.startswith("POWERCONTEXT_SERVER_"):
            monkeypatch.delenv(key)


def test_a_first_generation_model_names_the_offline_processing_switch(tmp_path):
    database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'server.db'}")
    # The basic-memory start records a Processing declaration without capabilities.
    prepare_server_storage(ServerSettings(database=database))

    configured = ServerSettings(database=database, inference=GENERATION)
    with pytest.raises(MigrationError, match="incompatible_processing_configuration") as refused:
        prepare_server_storage(configured)
    assert "processing-migrate" in str(refused.value)

    # The named offline switch completes, and the same data directory then starts with the models.
    manifest = _migration_configuration(configured)["processing_manifest"]

    async def switch():
        async with SQLiteProfile.open(database, tables=()) as profile:
            while True:
                async with profile.database.transaction() as connection:
                    progress = await processing_migration.apply_processing_migration(
                        connection, config_manifest=manifest, migration_id="issue-2618"
                    )
                if progress.complete:
                    return

    asyncio.run(switch())
    prepare_server_storage(configured)


def test_a_configuration_that_reshapes_ownership_keeps_the_plain_refusal(tmp_path):
    database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'server.db'}")
    prepare_server_storage(ServerSettings(database=database))

    reshaped = ServerSettings(
        database=database,
        runtime=RuntimeConfig(artifact_processing_supervisor_mode="dedicated"),
        inference=GENERATION,
    )
    with pytest.raises(MigrationError, match="incompatible_processing_configuration") as refused:
        prepare_server_storage(reshaped)
    assert "processing-migrate" not in str(refused.value)
    assert "differs from the migrated declaration." in str(refused.value)
