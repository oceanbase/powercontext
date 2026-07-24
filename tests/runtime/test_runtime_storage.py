from __future__ import annotations

import asyncio
import sqlite3
import subprocess
import sys

from powercontext.runtime import (
    MemoryBindingStore,
    PowerContextRuntime,
    RuntimeScopeStorage,
    RuntimeStorage,
    SourceCursor,
)
from powercontext.runtime.backends.sqlite import (
    SQLiteMemoryBindingStore,
    SQLiteRuntimeScopeStorage,
    SQLiteRuntimeStorage,
)


def test_sqlite_runtime_storage_implements_backend_neutral_contracts() -> None:
    assert MemoryBindingStore in SQLiteMemoryBindingStore.__bases__
    assert RuntimeScopeStorage in SQLiteRuntimeScopeStorage.__bases__
    assert RuntimeStorage in SQLiteRuntimeStorage.__bases__


def test_runtime_contracts_do_not_require_a_database_driver() -> None:
    program = """
import importlib.abc
import sys

class Blocker(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname in {'apsw', 'pymysql'}:
            raise ModuleNotFoundError(fullname)
        return None

sys.meta_path.insert(0, Blocker())
from powercontext.runtime import PowerContextRuntime, RuntimeStorage
assert PowerContextRuntime.__name__ == 'PowerContextRuntime'
assert RuntimeStorage.__name__ == 'RuntimeStorage'
"""
    completed = subprocess.run(  # noqa: S603
        [sys.executable, "-c", program],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_memory_binding_is_stable_per_store_and_unique_across_stores(tmp_path) -> None:
    async def scenario() -> None:
        first_database = tmp_path / "first.db"
        first = SQLiteMemoryBindingStore(first_database)
        concurrent = SQLiteMemoryBindingStore(first_database)
        independent = SQLiteMemoryBindingStore(tmp_path / "independent.db")
        await first.initialize()
        await concurrent.initialize()
        await independent.initialize()
        try:
            first_id, concurrent_id = await asyncio.gather(
                first.memory_artifact_id("scope:shared"),
                concurrent.memory_artifact_id("scope:shared"),
            )
            independent_id = await independent.memory_artifact_id("scope:shared")

            assert first_id == concurrent_id
            assert first_id != independent_id
        finally:
            await first.close()
            await concurrent.close()
            await independent.close()

        restored = SQLiteMemoryBindingStore(first_database)
        await restored.initialize()
        try:
            assert await restored.memory_artifact_id("scope:shared") == first_id
        finally:
            await restored.close()

        with sqlite3.connect(first_database) as connection:
            columns = tuple(str(row[1]) for row in connection.execute("PRAGMA table_info(runtime_memory_bindings)"))
        assert columns == ("scope_id", "memory_artifact_id")

    asyncio.run(scenario())


def test_runtime_assembles_from_the_backend_neutral_storage_contract(tmp_path) -> None:
    async def scenario() -> None:
        storage = SQLiteRuntimeStorage(tmp_path / "runtime.db")
        runtime = await PowerContextRuntime.assemble(storage=storage)
        try:
            assert await runtime.memory.for_scope("scope:assembled").cursor() == SourceCursor()
        finally:
            await runtime.close()

    asyncio.run(scenario())
