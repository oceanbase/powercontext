"""Unit tests for multi-agent CRUD/id lookup behavior."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from powermem.agent.implementations.multi_agent import MultiAgentMemoryManager
from powermem.agent.types import AccessPermission, MemoryScope, MemoryType


def _build_manager() -> MultiAgentMemoryManager:
    manager = MultiAgentMemoryManager.__new__(MultiAgentMemoryManager)
    manager.scope_memories = {
        scope: {memory_type: {} for memory_type in MemoryType}
        for scope in MemoryScope
    }
    manager.scope_controller = SimpleNamespace(
        scope_storage={
            scope: {memory_type: {} for memory_type in MemoryType}
            for scope in MemoryScope
        }
    )
    manager.permission_controller = MagicMock()
    manager.permission_controller.check_permission.return_value = True
    manager.permission_controller.revoke_all_for_memory = MagicMock()
    manager.collaboration_memories = {}
    manager._memory_instance = MagicMock()
    manager._get_or_create_memory_instance = MagicMock(
        return_value=manager._memory_instance
    )
    memory_data = {
        "id": 123,
        "content": "hello",
        "agent_id": "agent-1",
        "user_id": "user-1",
        "scope": MemoryScope.PRIVATE,
        "memory_type": MemoryType.WORKING,
        "metadata": {},
    }
    manager.scope_memories[MemoryScope.PRIVATE][MemoryType.WORKING][123] = memory_data
    manager.scope_controller.scope_storage[MemoryScope.PRIVATE][MemoryType.WORKING][123] = (
        memory_data
    )
    return manager


def _move_memory_to_string_cache_key(manager: MultiAgentMemoryManager) -> None:
    memory_data = manager.scope_memories[MemoryScope.PRIVATE][MemoryType.WORKING].pop(123)
    manager.scope_memories[MemoryScope.PRIVATE][MemoryType.WORKING]["123"] = memory_data
    manager.scope_controller.scope_storage[MemoryScope.PRIVATE][MemoryType.WORKING].pop(123)
    manager.scope_controller.scope_storage[MemoryScope.PRIVATE][MemoryType.WORKING]["123"] = (
        memory_data
    )


def test_find_memory_accepts_string_and_int_ids():
    manager = _build_manager()

    assert manager._find_memory("123")["content"] == "hello"
    assert manager._find_memory(123)["content"] == "hello"


def test_update_memory_persists_to_db_and_does_not_call_dead_intelligent_method():
    manager = _build_manager()
    manager.intelligent_manager = MagicMock()

    result = manager.update_memory("123", "agent-1", {"content": "updated"})

    manager._memory_instance.update.assert_called_once()
    manager.intelligent_manager.update_memory.assert_not_called()
    assert result["id"] == 123
    assert result["memory"] == "updated"


def test_update_memory_keeps_merged_metadata_in_cache():
    manager = _build_manager()
    memory_data = manager.scope_memories[MemoryScope.PRIVATE][MemoryType.WORKING][123]
    memory_data["metadata"] = {"source": "original"}

    manager.update_memory("123", "agent-1", {"metadata": {"topic": "agent"}})

    assert memory_data["metadata"] == {"source": "original", "topic": "agent"}
    manager._memory_instance.update.assert_called_once_with(
        memory_id=123,
        content="hello",
        user_id="user-1",
        agent_id="agent-1",
        metadata={"source": "original", "topic": "agent"},
    )


def test_update_memory_rekeys_string_cache_entry_without_duplicate():
    manager = _build_manager()
    _move_memory_to_string_cache_key(manager)

    manager.update_memory("123", "agent-1", {"content": "updated"})

    storage = manager.scope_memories[MemoryScope.PRIVATE][MemoryType.WORKING]
    scope_storage = manager.scope_controller.scope_storage[MemoryScope.PRIVATE][
        MemoryType.WORKING
    ]
    assert list(storage.keys()) == [123]
    assert list(scope_storage.keys()) == [123]


def test_delete_memory_calls_db_delete_before_cache_cleanup():
    manager = _build_manager()

    result = manager.delete_memory("123", "agent-1")

    manager._memory_instance.delete.assert_called_once_with(
        memory_id=123,
        user_id="user-1",
        agent_id="agent-1",
    )
    assert manager._find_memory(123) is None
    assert result["success"] is True


def test_delete_memory_removes_all_cache_key_variants():
    manager = _build_manager()
    memory_data = manager.scope_memories[MemoryScope.PRIVATE][MemoryType.WORKING][123]
    manager.scope_memories[MemoryScope.PRIVATE][MemoryType.WORKING]["123"] = memory_data
    manager.scope_controller.scope_storage[MemoryScope.PRIVATE][MemoryType.WORKING][
        "123"
    ] = memory_data

    manager.delete_memory("123", "agent-1")

    assert manager.scope_memories[MemoryScope.PRIVATE][MemoryType.WORKING] == {}
    assert manager.scope_controller.scope_storage[MemoryScope.PRIVATE][
        MemoryType.WORKING
    ] == {}


def test_cleanup_forgotten_memories_returns_cleaned_memory_ids():
    manager = _build_manager()
    memory_data = manager.scope_memories[MemoryScope.PRIVATE][MemoryType.WORKING][123]
    memory_data["retention_score"] = 0.05

    result = manager.cleanup_forgotten_memories()

    assert result["cleaned_memory_ids"] == [123]
    assert manager._find_memory(123) is None
