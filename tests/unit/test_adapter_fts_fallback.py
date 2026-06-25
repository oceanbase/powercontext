"""Tests for StorageAdapter FTS fallback when embedding is unavailable.

When EMBEDDING_PROVIDER=none (NoopEmbedding), search_memories() receives an
empty query_embedding. The adapter must fall through to the storage layer's
FTS5 full-text search using the raw query text instead of returning [].
"""

import pytest

from powermem.storage.adapter import StorageAdapter
from powermem.storage.sqlite.sqlite_vector_store import SQLiteVectorStore


def _add(adapter, content, user_id="u1"):
    adapter.add_memory({"content": content, "user_id": user_id})


def test_search_with_empty_embedding_and_query_returns_fts_results():
    store = SQLiteVectorStore(database_path=":memory:")
    adapter = StorageAdapter(store)

    _add(adapter, "Claude Code is a terminal assistant for software engineering")
    _add(adapter, "Python is a popular programming language")

    results = adapter.search_memories(
        query_embedding=[],
        user_id="u1",
        query="terminal assistant software",
        limit=5,
    )

    assert len(results) == 1
    assert "Claude Code" in results[0]["memory"]


def test_search_with_none_embedding_and_query_returns_fts_results():
    store = SQLiteVectorStore(database_path=":memory:")
    adapter = StorageAdapter(store)

    _add(adapter, "Claude Code is a terminal assistant for software engineering")
    _add(adapter, "Python is a popular programming language")

    results = adapter.search_memories(
        query_embedding=None,
        user_id="u1",
        query="Python programming",
        limit=5,
    )

    assert len(results) == 1
    assert "Python" in results[0]["memory"]


def test_search_with_empty_embedding_and_no_query_returns_empty():
    store = SQLiteVectorStore(database_path=":memory:")
    adapter = StorageAdapter(store)

    _add(adapter, "some memory content")

    results = adapter.search_memories(
        query_embedding=[],
        user_id="u1",
        query=None,
        limit=5,
    )

    assert results == []


def test_search_fts_does_not_match_unrelated_query():
    store = SQLiteVectorStore(database_path=":memory:")
    adapter = StorageAdapter(store)

    _add(adapter, "Claude Code is a terminal assistant for software engineering")

    results = adapter.search_memories(
        query_embedding=[],
        user_id="u1",
        query="cooking recipe pasta",
        limit=5,
    )

    assert results == []


def test_search_fts_respects_user_id_filter():
    store = SQLiteVectorStore(database_path=":memory:")
    adapter = StorageAdapter(store)

    _add(adapter, "shared keyword memory", user_id="u1")
    _add(adapter, "shared keyword memory", user_id="u2")

    results = adapter.search_memories(
        query_embedding=[],
        user_id="u1",
        query="shared keyword",
        limit=5,
    )

    assert len(results) == 1
    assert results[0]["user_id"] == "u1"
