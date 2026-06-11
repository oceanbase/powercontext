from unittest.mock import MagicMock

from server.models.errors import APIError, ErrorCode
from server.services.user_service import UserService


def test_update_user_memory_adds_memory_id_when_storage_payload_omits_it():
    service = UserService.__new__(UserService)
    service.user_memory = MagicMock()
    service.user_memory.update.return_value = {
        "content": "updated",
        "metadata": {"scope": "personal"},
    }

    result = service.update_user_memory(
        user_id="u01",
        memory_id=123,
        content="updated",
        agent_id="a01",
        metadata={"scope": "personal"},
    )

    assert result["id"] == 123
    assert result["memory_id"] == 123


def test_update_user_memory_raises_not_found_when_update_returns_none():
    service = UserService.__new__(UserService)
    service.user_memory = MagicMock()
    service.user_memory.update.return_value = None

    try:
        service.update_user_memory(
            user_id="u01",
            memory_id=123,
            content="updated",
            agent_id="a01",
        )
    except APIError as exc:
        assert exc.code == ErrorCode.MEMORY_NOT_FOUND
        assert exc.status_code == 404
    else:
        raise AssertionError("Expected APIError")
