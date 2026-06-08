import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from powermem.core.async_memory import AsyncMemory
from powermem.core.memory import Memory


def _enabled_plugin(updates=None, forget_ids=None, get_result=(None, True)):
    plugin = SimpleNamespace(enabled=True)
    plugin.on_search = MagicMock(return_value=(updates or [], forget_ids or []))
    plugin.on_get = MagicMock(return_value=get_result)
    return plugin


def _embedding_service():
    service = MagicMock()
    service.embed.return_value = [0.1, 0.2, 0.3]
    return service


def test_memory_search_marks_forgotten_memories_without_deleting():
    memory = object.__new__(Memory)
    memory._get_embedding_service = MagicMock(return_value=_embedding_service())
    memory.intelligence = SimpleNamespace(enabled=False)
    memory._intelligence_plugin = _enabled_plugin(forget_ids=["memory-1"])
    memory.audit = MagicMock()
    memory.telemetry = MagicMock()
    memory.enable_graph = False

    storage = MagicMock()
    storage.search_memories.return_value = [
        {"id": "memory-1", "memory": "stale memory", "metadata": {}, "score": 0.9}
    ]
    storage.vector_store = SimpleNamespace(connection_args={})
    memory.storage = storage

    result = memory.search("stale", user_id="user-1", agent_id="agent-1")

    assert result["results"][0]["id"] == "memory-1"
    storage.delete_memory.assert_not_called()
    storage.update_memory.assert_called_once()
    mem_id, updates, user_id, agent_id = storage.update_memory.call_args.args
    assert mem_id == "memory-1"
    assert user_id == "user-1"
    assert agent_id == "agent-1"
    assert updates["should_forget"] is True
    assert "marked_for_forgetting_at" in updates


def test_async_memory_search_marks_forgotten_memories_without_deleting():
    async def run_test():
        memory = object.__new__(AsyncMemory)
        memory._get_embedding_service = MagicMock(return_value=_embedding_service())
        memory.intelligence = SimpleNamespace(enabled=False)
        memory._intelligence_plugin = _enabled_plugin(forget_ids=["memory-1"])
        memory.audit = SimpleNamespace(log_event_async=AsyncMock())
        memory.telemetry = MagicMock()
        memory.enable_graph = False

        storage = SimpleNamespace(
            search_memories_async=AsyncMock(
                return_value=[
                    {"id": "memory-1", "memory": "stale memory", "metadata": {}, "score": 0.9}
                ]
            ),
            update_memory_async=AsyncMock(),
            delete_memory_async=AsyncMock(),
        )
        memory.storage = storage

        result = await memory.search("stale", user_id="user-1", agent_id="agent-1")

        assert result["results"][0]["id"] == "memory-1"
        storage.delete_memory_async.assert_not_called()
        storage.update_memory_async.assert_awaited_once()
        mem_id, updates, user_id, agent_id = storage.update_memory_async.call_args.args
        assert mem_id == "memory-1"
        assert user_id == "user-1"
        assert agent_id == "agent-1"
        assert updates["should_forget"] is True
        assert "marked_for_forgetting_at" in updates

    asyncio.run(run_test())


def test_async_memory_get_marks_forgotten_memory_without_deleting():
    async def run_test():
        memory = object.__new__(AsyncMemory)
        memory._intelligence_plugin = _enabled_plugin()
        memory.audit = SimpleNamespace(log_event_async=AsyncMock())

        stored = {"id": "memory-1", "memory": "stale memory"}
        storage = SimpleNamespace(
            get_memory_async=AsyncMock(return_value=stored),
            update_memory_async=AsyncMock(),
            delete_memory_async=AsyncMock(),
        )
        memory.storage = storage

        result = await memory.get("memory-1", user_id="user-1", agent_id="agent-1")

        assert result == stored
        storage.delete_memory_async.assert_not_called()
        storage.update_memory_async.assert_awaited_once()
        mem_id, updates, user_id, agent_id = storage.update_memory_async.call_args.args
        assert mem_id == "memory-1"
        assert user_id == "user-1"
        assert agent_id == "agent-1"
        assert updates["should_forget"] is True
        assert "marked_for_forgetting_at" in updates

    asyncio.run(run_test())
