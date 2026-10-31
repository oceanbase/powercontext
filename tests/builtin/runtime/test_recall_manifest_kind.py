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

"""Manifest filtering over real SQLite columns with nonbinary defaults."""

import asyncio

import pytest
from sqlalchemy import Engine, event

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts


@pytest.mark.parametrize("collation", ["NOCASE", "RTRIM"])
def test_manifest_kind_is_exact_with_a_nonbinary_column(collation):
    def nonbinary_kind(connection, cursor, statement, parameters, context, executemany):
        # Simulate case folding or space padding, not native OceanBase.
        if statement.lstrip().startswith("CREATE TABLE pc_atomic_memory_current"):
            statement = statement.replace("kind TEXT NOT NULL", f"kind TEXT COLLATE {collation} NOT NULL")
        return statement, parameters

    async def scenario():
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            await contexts.get("manifest-kind")
            expected = {}
            kinds = ("fact", "Fact", "fact ", "fact", "café", "café", "事实")
            for kind in kinds:
                created = await contexts.records.create_artifact(
                    "manifest-kind",
                    "atomic-memory",
                    ArtifactWrite(content={"kind": kind, "text": "A stored fact.", "description": "A fixture summary"}),
                )
                expected.setdefault(kind, set()).add(created.artifact_id)
            memories = contexts.atomic_memory.for_scope("manifest-kind")
            assert len((await memories.manifest()).items) == len(kinds)
            for kind in (*expected, "fAcT"):
                received = set()
                cursor = None
                while True:
                    page = await memories.manifest(kind=kind, limit=1, cursor=cursor)
                    assert all(item.kind == kind for item in page.items)
                    received.update(item.ref.artifact_id for item in page.items)
                    cursor = page.next_cursor
                    if cursor is None:
                        break
                assert received == expected.get(kind, set())

    event.listen(Engine, "before_cursor_execute", nonbinary_kind, retval=True)
    try:
        asyncio.run(scenario())
    finally:
        event.remove(Engine, "before_cursor_execute", nonbinary_kind)
