"""Integration tests for the no-embedding mode (EMBEDDING_PROVIDER=none).

Mirrors tests/integration/test_noop_llm_mode.py, but with the embedding
provider disabled instead of the LLM.

When embedding is disabled:
- add() stores memories using a mock fallback vector
- get() retrieves stored memories by ID
- update() and delete() function normally
- search() returns empty results (vector search requires embeddings)
"""

from __future__ import annotations

import uuid

import pytest

from powermem import Memory
from powermem.core.async_memory import AsyncMemory


def _sqlite_noop_embedding_config(tmp_path):
    return {
        "vector_store": {
            "provider": "sqlite",
            "config": {
                "database_path": str(tmp_path / "noop_embed.db"),
                "collection_name": f"noop_embed_{uuid.uuid4().hex[:8]}",
            },
        },
        "llm": {
            "provider": "noop",
            "config": {"model": "noop"},
        },
        "embedder": {
            "provider": "none",
        },
    }


def test_noop_embedding_preserves_basic_memory_crud(tmp_path):
    memory = Memory(config=_sqlite_noop_embedding_config(tmp_path))

    add_result = memory.add("User prefers dark mode", user_id="user_noop_embed")
    assert len(add_result["results"]) == 1
    memory_id = add_result["results"][0]["id"]

    stored = memory.get(memory_id, user_id="user_noop_embed")
    assert stored is not None
    assert "dark mode" in stored.get("content", "")

    update_result = memory.update(
        memory_id, "User prefers light mode", user_id="user_noop_embed"
    )
    assert update_result is not None

    assert memory.delete(memory_id, user_id="user_noop_embed") is True
    assert memory.get(memory_id, user_id="user_noop_embed") is None


def test_noop_embedding_search_returns_empty(tmp_path):
    """Vector search must return empty results when embedding is disabled."""
    memory = Memory(config=_sqlite_noop_embedding_config(tmp_path))

    memory.add("User loves hiking", user_id="user_search_noop")

    results = memory.search("hiking", user_id="user_search_noop")
    assert results["results"] == []


def test_noop_embedding_add_multiple_memories(tmp_path):
    """Multiple adds must not interfere with each other under noop embedding."""
    memory = Memory(config=_sqlite_noop_embedding_config(tmp_path))

    for i in range(3):
        result = memory.add(f"Memory entry {i}", user_id="user_multi_noop")
        assert len(result["results"]) == 1

    all_memories = memory.get_all(user_id="user_multi_noop")
    assert len(all_memories["results"]) == 3


@pytest.mark.asyncio
async def test_noop_embedding_async_crud(tmp_path):
    memory = AsyncMemory(config=_sqlite_noop_embedding_config(tmp_path))

    add_result = await memory.add("Async memory with no embedding", user_id="async_noop")
    assert len(add_result["results"]) == 1
    memory_id = add_result["results"][0]["id"]

    stored = await memory.get(memory_id, user_id="async_noop")
    assert stored is not None

    assert await memory.delete(memory_id, user_id="async_noop") is True
    assert await memory.get(memory_id, user_id="async_noop") is None


@pytest.mark.asyncio
async def test_noop_embedding_async_search_returns_empty(tmp_path):
    """Async vector search must return empty results when embedding is disabled."""
    memory = AsyncMemory(config=_sqlite_noop_embedding_config(tmp_path))

    await memory.add("Async user loves hiking", user_id="async_search_noop")

    results = await memory.search("hiking", user_id="async_search_noop")
    assert results["results"] == []
