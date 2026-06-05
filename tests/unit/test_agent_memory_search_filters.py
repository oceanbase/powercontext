from unittest.mock import MagicMock

from powermem.agent.agent import AgentMemory


def test_agent_memory_search_passes_filters_to_manager():
    agent_memory = AgentMemory.__new__(AgentMemory)
    agent_memory._initialized = True
    agent_memory._agent_manager = MagicMock()
    agent_memory._agent_manager.get_memories.return_value = [{"id": "memory-1"}]

    results = agent_memory.search(
        query="customer",
        user_id="user-1",
        agent_id="agent-1",
        scope="GROUP",
        filters={"category": "communication"},
        limit=5,
    )

    assert results == [{"id": "memory-1"}]
    agent_memory._agent_manager.get_memories.assert_called_once_with(
        agent_id="agent-1",
        query="customer",
        filters={
            "category": "communication",
            "user_id": "user-1",
            "scope": "GROUP",
        },
    )


def test_agent_memory_get_all_passes_filters_to_manager():
    agent_memory = AgentMemory.__new__(AgentMemory)
    agent_memory._initialized = True
    agent_memory._agent_manager = MagicMock()
    agent_memory._agent_manager.get_memories.return_value = [{"id": "memory-1"}]

    results = agent_memory.get_all(
        user_id="user-1",
        agent_id="agent-1",
        filters={"category": "communication"},
        limit=5,
    )

    assert results == [{"id": "memory-1"}]
    agent_memory._agent_manager.get_memories.assert_called_once_with(
        agent_id="agent-1",
        filters={
            "category": "communication",
            "user_id": "user-1",
        },
    )


def test_agent_memory_delete_all_passes_filters_to_manager():
    agent_memory = AgentMemory.__new__(AgentMemory)
    agent_memory._initialized = True
    agent_memory._agent_manager = MagicMock()
    agent_memory._agent_manager.get_memories.return_value = [{"id": "memory-1"}]
    agent_memory._agent_manager.delete_memory.return_value = {"success": True}

    result = agent_memory.delete_all(
        user_id="user-1",
        agent_id="agent-1",
        filters={"category": "communication"},
    )

    assert result is True
    agent_memory._agent_manager.get_memories.assert_called_once_with(
        agent_id="agent-1",
        filters={
            "category": "communication",
            "user_id": "user-1",
        },
    )
    agent_memory._agent_manager.delete_memory.assert_called_once_with(
        memory_id="memory-1",
        agent_id="agent-1",
    )


def test_agent_memory_search_keeps_positional_limit_compatibility():
    agent_memory = AgentMemory.__new__(AgentMemory)
    agent_memory._initialized = True
    agent_memory._agent_manager = MagicMock()
    agent_memory._agent_manager.get_memories.return_value = [
        {"id": "memory-1"},
        {"id": "memory-2"},
    ]

    results = agent_memory.search("customer", None, "agent-1", None, 1)

    assert results == [{"id": "memory-1"}]
    agent_memory._agent_manager.get_memories.assert_called_once_with(
        agent_id="agent-1",
        query="customer",
        filters={},
    )


def test_agent_memory_get_all_keeps_positional_limit_compatibility():
    agent_memory = AgentMemory.__new__(AgentMemory)
    agent_memory._initialized = True
    agent_memory._agent_manager = MagicMock()
    agent_memory._agent_manager.get_memories.return_value = [
        {"id": "memory-1"},
        {"id": "memory-2"},
    ]

    results = agent_memory.get_all(None, "agent-1", 1)

    assert results == [{"id": "memory-1"}]
    agent_memory._agent_manager.get_memories.assert_called_once_with(
        agent_id="agent-1",
        filters={},
    )
