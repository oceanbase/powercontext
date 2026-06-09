from unittest.mock import MagicMock

import pytest

from powermem.core.memory import (
    Memory,
    _HTTPMemoryClient,
    _looks_like_embedded_seekdb_lock_error,
)


def test_memory_uses_http_fallback_when_embedded_seekdb_is_locked(monkeypatch):
    http_client = MagicMock()
    http_client.base_url = "http://localhost:8848/api/v1"
    http_client.add.return_value = {"results": [{"id": "memory-1", "memory": "hello"}]}
    http_client.delete.return_value = {"deleted": True}
    http_client.delete_all.return_value = {
        "deleted_count": 1,
        "memory_ids": ["memory-1"],
    }

    monkeypatch.setattr(
        "powermem.core.memory.VectorStoreFactory.create",
        MagicMock(side_effect=RuntimeError("seekdb opened by other process")),
    )
    monkeypatch.setattr(
        "powermem.core.memory._HTTPMemoryClient.from_env_if_healthy",
        MagicMock(return_value=http_client),
    )

    memory = Memory(config={"vector_store": {"provider": "oceanbase", "config": {}}})

    assert memory.add("hello") == {
        "results": [{"id": "memory-1", "memory": "hello"}],
    }
    assert memory.delete("memory-1") is True
    assert memory.delete_all(user_id="user-1") is True
    http_client.add.assert_called_once()
    http_client.delete.assert_called_once_with(
        "memory-1",
        user_id=None,
        agent_id=None,
    )
    http_client.delete_all.assert_called_once_with(
        user_id="user-1",
        agent_id=None,
        run_id=None,
    )


def test_memory_http_fallback_reset_preserves_sdk_contract(monkeypatch):
    http_client = MagicMock()
    http_client.base_url = "http://localhost:8848/api/v1"
    http_client.reset.return_value = {
        "deleted_count": 1,
        "memory_ids": ["memory-1"],
    }

    monkeypatch.setattr(
        "powermem.core.memory.VectorStoreFactory.create",
        MagicMock(side_effect=RuntimeError("seekdb opened by other process")),
    )
    monkeypatch.setattr(
        "powermem.core.memory._HTTPMemoryClient.from_env_if_healthy",
        MagicMock(return_value=http_client),
    )

    memory = Memory(config={"vector_store": {"provider": "oceanbase", "config": {}}})

    assert memory.reset() is None
    http_client.reset.assert_called_once_with()


def test_memory_does_not_fallback_for_unrelated_initialization_errors(monkeypatch):
    monkeypatch.setattr(
        "powermem.core.memory.VectorStoreFactory.create",
        MagicMock(side_effect=RuntimeError("bad vector configuration")),
    )
    from_env = MagicMock(return_value=MagicMock())
    monkeypatch.setattr(
        "powermem.core.memory._HTTPMemoryClient.from_env_if_healthy",
        from_env,
    )

    with pytest.raises(RuntimeError, match="bad vector configuration"):
        Memory(config={"vector_store": {"provider": "oceanbase", "config": {}}})

    from_env.assert_not_called()


def test_seekdb_common_error_is_treated_as_lock_error():
    error = RuntimeError("open seekdb failed OB_ERROR(4000): Common error")

    assert _looks_like_embedded_seekdb_lock_error(error) is True


def test_http_fallback_search_returns_sdk_result_schema(monkeypatch):
    client = _HTTPMemoryClient("http://localhost:8848")

    monkeypatch.setattr(
        "httpx.request",
        MagicMock(
            return_value=MagicMock(
                json=MagicMock(
                    return_value={
                        "success": True,
                        "data": {
                            "results": [
                                {
                                    "memory_id": "memory-1",
                                    "content": "hello",
                                    "score": 0.9,
                                    "metadata": {"topic": "greeting"},
                                }
                            ]
                        },
                    }
                ),
                raise_for_status=MagicMock(),
            )
        ),
    )

    result = client.search("hello")

    assert result["results"] == [
        {
            "id": "memory-1",
            "memory_id": "memory-1",
            "memory": "hello",
            "event": "ADD",
            "user_id": None,
            "agent_id": None,
            "run_id": None,
            "metadata": {"topic": "greeting"},
            "created_at": None,
            "updated_at": None,
            "score": 0.9,
        }
    ]


def test_http_fallback_get_all_filters_run_id_and_metadata(monkeypatch):
    client = _HTTPMemoryClient("http://localhost:8848")
    request = MagicMock(
        return_value=MagicMock(
            json=MagicMock(
                return_value={
                    "success": True,
                    "data": {
                        "memories": [
                            {"id": "1", "run_id": "run-1", "metadata": {"kind": "keep"}},
                            {"id": "2", "run_id": "run-2", "metadata": {"kind": "keep"}},
                            {"id": "3", "run_id": "run-1", "metadata": {"kind": "drop"}},
                        ],
                        "total": 3,
                    },
                }
            ),
            raise_for_status=MagicMock(),
        )
    )
    monkeypatch.setattr("httpx.request", request)

    result = client.get_all(
        user_id="user-1",
        run_id="run-1",
        filters={"kind": "keep"},
    )

    assert result["results"] == [
        {"id": "1", "run_id": "run-1", "metadata": {"kind": "keep"}},
    ]


def test_http_fallback_delete_all_respects_run_id(monkeypatch):
    client = _HTTPMemoryClient("http://localhost:8848")
    responses = [
        MagicMock(
            json=MagicMock(
                return_value={
                    "success": True,
                    "data": {
                        "memories": [
                            {"id": "1", "run_id": "run-1", "metadata": {}},
                            {"id": "2", "run_id": "run-2", "metadata": {}},
                        ],
                        "total": 2,
                    },
                }
            ),
            raise_for_status=MagicMock(),
        ),
        MagicMock(
            json=MagicMock(
                return_value={
                    "success": True,
                    "data": {"deleted_count": 1, "memory_ids": ["1"]},
                }
            ),
            raise_for_status=MagicMock(),
        ),
    ]
    request = MagicMock(side_effect=responses)
    monkeypatch.setattr("httpx.request", request)

    result = client.delete_all(user_id="user-1", run_id="run-1")

    assert result == {"deleted_count": 1, "memory_ids": ["1"]}
    delete_payload = request.call_args_list[1].kwargs["json"]
    assert delete_payload["memory_ids"] == ["1"]
