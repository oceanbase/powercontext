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

"""The processing snapshot digest is frozen migration evidence."""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import closing

from sqlalchemy.ext.asyncio import create_async_engine

from powercontext.builtin.persistence.migrations.atomic_memory_v1 import _processing_snapshot


def test_processing_snapshot_preserves_frozen_json_byte_order(tmp_path):
    database = tmp_path / "golden.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute(
            "CREATE TABLE pc_sources (scope_id TEXT, source_type TEXT, source_id TEXT, "
            "payload BLOB, journal_position INTEGER)"
        )
        connection.executemany(
            "INSERT INTO pc_sources VALUES (?, ?, ?, ?, ?)",
            [
                ("scope", "content", "z", b"\x00\xff", 0),
                ("scope", "content", "a", None, 1),
                ("scope", "content", "cjk", "中文 e\u0301", 2),
                ("scope", "content", "json", b'{"z":1,"a":2}', 3),
                ("scope", "content", "empty", b"", 4),
            ],
        )
        connection.commit()

    async def inspect_snapshot():
        engine = create_async_engine(f"sqlite+aiosqlite:///{database}")
        try:
            async with engine.connect() as connection:
                errors: list[str] = []
                digest = await _processing_snapshot(connection, {"pc_sources"}, errors)
                assert errors == []
                # Frozen at 88899876, with NULL/BLOB/text and JSON order unlike identity order.
                assert digest == "461721ed81b06924e6450f6d6736de3ff1932ec100a9246bb37fd21fa47c1b38"
        finally:
            await engine.dispose()

    asyncio.run(inspect_snapshot())
