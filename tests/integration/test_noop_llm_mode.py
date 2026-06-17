import json
import uuid

import pytest

from powermem import Memory
from powermem.core.async_memory import AsyncMemory
from powermem.user_memory import UserMemory


class _FailingGraphStore:
    def add(self, *args, **kwargs):
        raise AssertionError("graph add should not be called in no-LLM mode")

    def search(self, *args, **kwargs):
        raise AssertionError("graph search should not be called in no-LLM mode")


def _sqlite_noop_config(tmp_path):
    return {
        "vector_store": {
            "provider": "sqlite",
            "config": {
                "database_path": str(tmp_path / "noop_memory.db"),
                "collection_name": f"noop_memories_{uuid.uuid4().hex[:8]}",
            },
        },
        "llm": {
            "provider": "noop",
            "config": {"model": "noop"},
        },
        "embedder": {
            "provider": "mock",
            "config": {"embedding_dims": 16},
        },
    }


def test_noop_llm_preserves_basic_memory_crud(tmp_path):
    memory = Memory(config=_sqlite_noop_config(tmp_path))

    add_result = memory.add("User likes black coffee", user_id="user_noop")
    assert len(add_result["results"]) == 1
    memory_id = add_result["results"][0]["id"]

    search_result = memory.search("coffee", user_id="user_noop")
    assert search_result["results"]

    update_result = memory.update(memory_id, "User likes green tea", user_id="user_noop")
    assert update_result is not None

    assert memory.delete(memory_id, user_id="user_noop") is True
    assert memory.get(memory_id, user_id="user_noop") is None


def test_noop_llm_imports_memories(tmp_path):
    memory = Memory(config=_sqlite_noop_config(tmp_path))
    source = json.dumps(
        [
            {
                "content": "Imported no-LLM memory about black coffee",
                "metadata": {"source": "import-test"},
            }
        ]
    )

    result = memory.import_memories(
        source=source,
        format="json",
        user_id="user_import_noop",
    )

    assert result == {"success": 1, "failed": 0}
    search_result = memory.search("black coffee", user_id="user_import_noop")
    assert search_result["results"]


def test_noop_llm_skips_graph_operations(tmp_path):
    memory = Memory(config=_sqlite_noop_config(tmp_path))
    memory.enable_graph = True
    memory.graph_store = _FailingGraphStore()

    add_result = memory.add("User likes black coffee", user_id="user_graph_noop")
    assert len(add_result["results"]) == 1
    assert "relations" not in add_result

    search_result = memory.search("coffee", user_id="user_graph_noop")
    assert search_result["results"]
    assert "relations" not in search_result


@pytest.mark.asyncio
async def test_noop_llm_preserves_async_memory_crud(tmp_path):
    memory = AsyncMemory(config=_sqlite_noop_config(tmp_path))

    add_result = await memory.add("User likes black coffee", user_id="async_user_noop")
    assert len(add_result["results"]) == 1
    memory_id = add_result["results"][0]["id"]

    search_result = await memory.search("coffee", user_id="async_user_noop")
    assert search_result["results"]

    update_result = await memory.update(memory_id, "User likes green tea", user_id="async_user_noop")
    assert update_result is not None

    assert await memory.delete(memory_id, user_id="async_user_noop") is True
    assert await memory.get(memory_id, user_id="async_user_noop") is None


def test_user_memory_skips_profile_extraction_when_llm_is_noop(tmp_path):
    user_memory = UserMemory(config=_sqlite_noop_config(tmp_path))

    result = user_memory.add(
        messages="I prefer Python for backend services.",
        user_id="user_profile_noop",
    )

    assert result["results"]
    assert result["profile_extracted"] is False
    assert "profile_content" not in result
