# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Select local graph storage and own its lifetime for both CLI and Server."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from powercontext.builtin.code.models import CodeConfig
from powercontext.builtin.code.service import CodeService
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.seekdb import SeekDBConfig, SeekDBProfile
from powercontext.builtin.persistence.seekdb.profile import _finish_task
from powercontext.builtin.persistence.sqlite import SQLiteConfig


@asynccontextmanager
async def open_code_service(
    config: CodeConfig, database: SQLiteConfig | OceanBaseConfig | SeekDBConfig
) -> AsyncIterator[CodeService]:
    """Use seekdb for an embedded deployment; retain the local SQLite default otherwise."""
    if not config.enabled or not isinstance(database, SeekDBConfig):
        yield CodeService(config)
        return
    from powercontext.builtin.code.seekdb import SeekDBGraphStore

    async with SeekDBProfile.open(database, tables=()) as profile:
        store = SeekDBGraphStore(database.path, profile.connection_options)
        try:
            await asyncio.to_thread(store.initialize, time.monotonic() + config.limits.build_seconds)
            yield CodeService(config, store=store)
        finally:
            close_task = asyncio.create_task(asyncio.to_thread(store.close))
            try:
                await asyncio.shield(close_task)
            except asyncio.CancelledError:
                await _finish_task(close_task)
                raise
