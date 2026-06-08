from types import SimpleNamespace

from powermem.agent.implementations.multi_agent import MultiAgentMemoryManager
from powermem.agent.types import AccessPermission, MemoryScope, MemoryType


def test_multi_agent_loads_legacy_agent_scope_as_agent_group():
    manager = MultiAgentMemoryManager.__new__(MultiAgentMemoryManager)
    manager.config = {}
    manager.multi_agent_config = SimpleNamespace(
        default_permissions={"owner": ["read", "write", "delete", "share", "admin"]}
    )
    manager.scope_memories = {
        scope: {memory_type: {} for memory_type in MemoryType} for scope in MemoryScope
    }
    manager.scope_controller = SimpleNamespace(
        scope_storage={
            scope: {memory_type: {} for memory_type in MemoryType}
            for scope in MemoryScope
        },
        check_scope_access=lambda agent_id, memory_id: True,
    )
    manager.permission_controller = SimpleNamespace(
        memory_permissions={},
        check_permission=lambda agent_id, memory_id, permission: True,
    )
    manager._memory_instance = SimpleNamespace(
        get_all=lambda **kwargs: {
            "results": [
                {
                    "id": "memory-1",
                    "memory": "legacy scope memory",
                    "agent_id": "agent-1",
                    "metadata": {"scope": "agent", "memory_type": "working"},
                }
            ]
        }
    )

    memories = manager.get_memories("agent-1")

    assert memories[0]["scope"] == MemoryScope.AGENT_GROUP
    assert (
        AccessPermission.SHARE
        in manager.permission_controller.memory_permissions["memory-1"]["agent-1"]
    )
