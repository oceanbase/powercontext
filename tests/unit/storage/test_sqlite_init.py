"""Tests for SQLiteVectorStore initialisation behaviour.

Covers the fixes from feat/sqlite-default-coding-agent:
  - pysqlite3 fallback import
  - FTS5 / JSON1 feature smoke test (replaces bare version check)
  - WAL mode enabled by default on disk databases
  - WAL skipped for :memory: databases
  - enable_wal=False respected
  - timeout parameter forwarded to sqlite3.connect
"""

import os
import tempfile

import pytest

import powermem.storage.sqlite.sqlite_vector_store as _mod
from powermem.storage.sqlite.sqlite_vector_store import SQLiteVectorStore


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _disk_store(**kwargs):
    """Return a context manager that yields a SQLiteVectorStore on a temp file."""
    import contextlib

    @contextlib.contextmanager
    def _cm():
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            store = SQLiteVectorStore(database_path=path, collection_name="t", **kwargs)
            yield store
            store.close()
        finally:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.unlink(path + suffix)
                except FileNotFoundError:
                    pass

    return _cm()


# ---------------------------------------------------------------------------
# import / availability
# ---------------------------------------------------------------------------

def test_sqlite3_module_loaded():
    assert hasattr(_mod.sqlite3, "connect")


# ---------------------------------------------------------------------------
# FTS5 / JSON1 feature smoke test
# ---------------------------------------------------------------------------

def test_fts5_available():
    """Creating an :memory: store must succeed, proving FTS5 is available."""
    store = SQLiteVectorStore(database_path=":memory:", collection_name="probe")
    store.close()


def test_json1_available():
    """json_extract must execute without error on the active SQLite library."""
    store = SQLiteVectorStore(database_path=":memory:", collection_name="probe")
    row = store.connection.execute("SELECT json_extract('{}', '$')").fetchone()
    store.close()
    assert row is not None


# ---------------------------------------------------------------------------
# WAL mode
# ---------------------------------------------------------------------------

def test_wal_enabled_by_default_on_disk_db():
    with _disk_store() as store:
        row = store.connection.execute("PRAGMA journal_mode").fetchone()
        assert row and row[0].lower() == "wal"


def test_wal_skipped_for_memory_db():
    store = SQLiteVectorStore(database_path=":memory:", collection_name="t",
                              enable_wal=True)
    row = store.connection.execute("PRAGMA journal_mode").fetchone()
    store.close()
    assert row and row[0].lower() == "memory"


def test_wal_disabled_when_enable_wal_false():
    with _disk_store(enable_wal=False) as store:
        row = store.connection.execute("PRAGMA journal_mode").fetchone()
        assert row and row[0].lower() != "wal"


# ---------------------------------------------------------------------------
# timeout parameter
# ---------------------------------------------------------------------------

def test_timeout_accepted():
    store = SQLiteVectorStore(database_path=":memory:", collection_name="t",
                              timeout=60)
    store.close()


# ---------------------------------------------------------------------------
# basic CRUD
# ---------------------------------------------------------------------------

@pytest.fixture
def memory_store():
    store = SQLiteVectorStore(database_path=":memory:", collection_name="crud")
    yield store
    store.close()


def test_insert_and_get(memory_store):
    ids = memory_store.insert(
        vectors=[[0.1, 0.2, 0.3]],
        payloads=[{"data": "hello world", "user_id": "u1"}],
    )
    assert len(ids) == 1
    record = memory_store.get(ids[0])
    assert record is not None
    assert record.id == ids[0]


def test_vector_search_returns_closest(memory_store):
    ids = memory_store.insert(
        vectors=[[0.1, 0.2, 0.3], [0.9, 0.8, 0.7]],
        payloads=[{"data": "hello"}, {"data": "world"}],
    )
    results = memory_store.search("", vectors=[[0.1, 0.2, 0.3]], limit=2)
    assert len(results) > 0
    assert results[0].id == ids[0]


def test_delete(memory_store):
    ids = memory_store.insert(
        vectors=[[0.1, 0.2, 0.3]],
        payloads=[{"data": "to delete"}],
    )
    memory_store.delete(ids[0])
    assert memory_store.get(ids[0]) is None
