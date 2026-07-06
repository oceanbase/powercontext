"""Unit tests for hybrid manager cleanup index synchronization."""

from unittest.mock import MagicMock

from powermem.agent.implementations.hybrid import HybridMemoryManager


def test_cleanup_forgotten_memories_removes_cleaned_ids_from_indexes():
    manager = HybridMemoryManager.__new__(HybridMemoryManager)
    manager.multi_agent_manager = MagicMock()
    manager.multi_user_manager = MagicMock()
    manager.multi_agent_manager.cleanup_forgotten_memories.return_value = {
        "cleaned_memories": 1,
        "archived_memories": 0,
        "deleted_memories": 1,
        "cleaned_memory_ids": [123],
    }
    manager.multi_user_manager.cleanup_forgotten_memories.return_value = {
        "cleaned_memories": 0,
        "archived_memories": 0,
        "deleted_memories": 0,
        "cleaned_memory_ids": [],
    }
    manager.unified_memory_index = {
        "123": {"mode": "multi_agent"},
        456: {"mode": "multi_user"},
    }
    manager.mode_specific_memories = {
        "multi_agent": {123: {"id": 123}},
        "multi_user": {456: {"id": 456}},
    }

    result = manager.cleanup_forgotten_memories()

    assert result["total_cleaned"] == 1
    assert "123" not in manager.unified_memory_index
    assert 123 not in manager.mode_specific_memories["multi_agent"]
    assert 456 in manager.unified_memory_index
    assert 456 in manager.mode_specific_memories["multi_user"]
