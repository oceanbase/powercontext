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

"""Backend-owned maintenance connections for clean Atomic Memory migration acceptance."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import create_async_engine

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.persistence.atomic_memory_index import AtomicMemoryIndex
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.oceanbase import profile as oceanbase_profile
from powercontext.builtin.persistence.oceanbase.atomic_memory_index import OceanBaseAtomicMemoryIndex
from powercontext.builtin.persistence.seekdb import SeekDBConfig, SeekDBProfile
from powercontext.builtin.persistence.seekdb import profile as seekdb_profile
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.sqlite import profile as sqlite_profile
from powercontext.builtin.persistence.sqlite.atomic_memory_index import SQLiteAtomicMemoryIndex
from powercontext.builtin.runtime.config import DatabaseConfig


@dataclass(frozen=True)
class MigrationBackend:
    config: DatabaseConfig
    directory: Path


@asynccontextmanager
async def migration_profile(
    config: DatabaseConfig,
    *,
    load_vector_extension: bool = False,
) -> AsyncIterator[SQLiteProfile | SeekDBProfile | OceanBaseProfile]:
    # Maintenance must inspect and repair the deliberately unready fixture.
    # Business profile admission would reject it before the migration can run.
    async with AsyncExitStack() as resources:
        if isinstance(config, SQLiteConfig):
            sqlite_profile._create_database_directory(config.url)
            engine = create_async_engine(config.url, echo=config.echo, hide_parameters=True)
            sqlite_profile._configure_sqlite(engine, config, load_vector_extension=load_vector_extension)
            profile_type = SQLiteProfile
        elif isinstance(config, SeekDBConfig):
            path = config.path.expanduser().resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            instance = await seekdb_profile._open_instance(seekdb_profile._load_binding(), path)
            resources.callback(instance.close)
            engine = seekdb_profile._create_engine(config, instance.connection_options())
            profile_type = SeekDBProfile
        else:
            assert isinstance(config, OceanBaseConfig)
            oceanbase_profile._register_official_dialect()
            engine = create_async_engine(
                config.url.get_secret_value(),
                connect_args={"init_command": "SET autocommit = 0"},
                echo=config.echo,
                hide_parameters=True,
                pool_pre_ping=config.pool_pre_ping,
            )
            profile_type = OceanBaseProfile
        database = AsyncDatabase.own(engine)
        resources.push_async_callback(database.close)
        if isinstance(config, SQLiteConfig):
            await sqlite_profile._warm_sqlite(engine, config)
        elif isinstance(config, OceanBaseConfig):
            async with database.transaction() as connection:
                await oceanbase_profile._require_mysql_tenant(connection)
        yield profile_type(database=database, tables=())


def migration_index(config: DatabaseConfig, profile: EmbeddingProfile | None = None) -> AtomicMemoryIndex:
    return SQLiteAtomicMemoryIndex(profile) if isinstance(config, SQLiteConfig) else OceanBaseAtomicMemoryIndex(profile)
