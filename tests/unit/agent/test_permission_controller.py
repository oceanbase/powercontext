"""Unit tests for permission controller enum/string comparison fixes."""

from types import SimpleNamespace

from powermem.agent.components.permission_controller import PermissionController
from powermem.agent.implementations.multi_agent import MultiAgentMemoryManager
from powermem.agent.types import AccessPermission


def _build_permission_controller() -> PermissionController:
    controller = PermissionController.__new__(PermissionController)
    controller.config = SimpleNamespace(
        agent_memory=SimpleNamespace(
            multi_agent_config=SimpleNamespace(
                default_permissions={
                    "owner": ["read", "write", "delete", "share", "admin"],
                    "public": ["read"],
                },
                agent_groups={},
            )
        )
    )
    controller.multi_agent_config = controller.config.agent_memory.multi_agent_config
    controller.memory_permissions = {}
    controller.role_permissions = controller.multi_agent_config.default_permissions
    controller.access_log = []
    controller.agent_roles = {"agent-1": ["owner"]}
    return controller


def test_role_fallback_grants_read_with_enum():
    controller = _build_permission_controller()

    assert controller.check_permission(
        "agent-1", "memory-1", AccessPermission.READ
    ) is True


def test_default_public_permission_grants_read_with_enum():
    controller = _build_permission_controller()

    assert controller.check_permission(
        "agent-without-role", "memory-1", AccessPermission.READ
    ) is True


def test_direct_grant_still_works():
    controller = _build_permission_controller()
    controller.grant_permission(
        memory_id="memory-1",
        agent_id="agent-2",
        permission=AccessPermission.WRITE,
        granted_by="agent-1",
    )

    assert controller.check_permission(
        "agent-2", "memory-1", AccessPermission.WRITE
    ) is True


def test_direct_grant_matches_int_and_string_memory_ids():
    controller = _build_permission_controller()
    controller.grant_permission(
        memory_id=123,
        agent_id="agent-2",
        permission=AccessPermission.WRITE,
        granted_by="agent-1",
    )

    assert controller.check_permission(
        "agent-2", "123", AccessPermission.WRITE
    ) is True


def test_revoke_all_for_memory_removes_bulk_permission_records():
    controller = _build_permission_controller()
    controller.grant_permission(
        memory_id="memory-1",
        agent_id="agent-2",
        permission=AccessPermission.WRITE,
        granted_by="agent-1",
    )

    controller.revoke_all_for_memory("memory-1")

    assert "memory-1" not in controller.memory_permissions


def test_multi_agent_wrapper_accepts_read_string_variants():
    manager = MultiAgentMemoryManager.__new__(MultiAgentMemoryManager)
    manager.permission_controller = _build_permission_controller()

    assert manager.check_permission("agent-1", "memory-1", "read") is True
    assert manager.check_permission("agent-1", "memory-1", "READ") is True
    assert manager.check_permission(
        "agent-1", "memory-1", AccessPermission.READ
    ) is True
