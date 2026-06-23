from datetime import datetime
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from server.api.v1 import router as v1_router
from server.config import config
from server.services.memory_service import MemoryService


API_HEADERS = {"X-API-Key": "secret"}


class InMemorySessionTimelineService(MemoryService):
    def __init__(self):
        self.memories: list[dict[str, Any]] = []
        self._next_id = 1

    def create_memory(
        self,
        content: str,
        user_id: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        **_kwargs,
    ):
        memory_id = str(self._next_id)
        self._next_id += 1
        occurred_at = (metadata or {}).get("occurred_at")
        memory = {
            "id": memory_id,
            "memory_id": memory_id,
            "memory": content,
            "content": content,
            "user_id": user_id,
            "agent_id": agent_id,
            "run_id": run_id,
            "metadata": metadata or {},
            "created_at": occurred_at or datetime.utcnow().isoformat() + "Z",
            "updated_at": occurred_at or datetime.utcnow().isoformat() + "Z",
        }
        self.memories.append(memory)
        return [memory]

    def _filtered_memories(
        self,
        user_id: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
        filters: dict[str, Any] | None = None,
    ):
        filtered = []
        for memory in self.memories:
            if user_id is not None and memory.get("user_id") != user_id:
                continue
            if agent_id is not None and memory.get("agent_id") != agent_id:
                continue
            if run_id is not None and memory.get("run_id") != run_id:
                continue
            metadata = memory.get("metadata") or {}
            if filters and any(metadata.get(key) != value for key, value in filters.items()):
                continue
            filtered.append(memory)
        return filtered

    def list_memories(
        self,
        user_id: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
        sort_by: str | None = None,
        order: str = "desc",
        filters: dict[str, Any] | None = None,
    ):
        memories = self._filtered_memories(
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
            filters=filters,
        )
        sort_key = sort_by or "created_at"
        memories = sorted(
            memories,
            key=lambda item: str(item.get(sort_key) or ""),
            reverse=order != "asc",
        )
        return memories[offset : offset + limit]

    def count_memories(
        self,
        user_id: str | None = None,
        agent_id: str | None = None,
        run_id: str | None = None,
        filters: dict[str, Any] | None = None,
    ):
        return len(
            self._filtered_memories(
                user_id=user_id,
                agent_id=agent_id,
                run_id=run_id,
                filters=filters,
            )
        )


@pytest.fixture
def session_timeline_client(monkeypatch):
    monkeypatch.setattr(config, "auth_enabled", True)
    monkeypatch.setattr(config, "api_keys", "secret")
    app = FastAPI()
    app.state.memory_service = InMemorySessionTimelineService()
    app.include_router(v1_router)
    return TestClient(app)


def _create_memory(
    client: TestClient,
    content: str,
    *,
    run_id: str | None,
    occurred_at: str,
    metadata: dict[str, Any],
):
    payload = {
        "content": content,
        "user_id": "e2e-user",
        "agent_id": "e2e-agent",
        "run_id": run_id,
        "metadata": {
            "schema": "powermem.coding_agent_observation.v1",
            "scope": "coding_agent",
            "source": "coding_agent",
            "record_kind": "observation_raw",
            "occurred_at": occurred_at,
            **metadata,
        },
        "infer": False,
    }
    response = client.post("/api/v1/memories", headers=API_HEADERS, json=payload)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert len(data) == 1
    return data[0]["memory_id"]


def test_session_timeline_api_routes_cover_run_filter_aliases_and_source_loading(
    session_timeline_client,
):
    first_memory_id = _create_memory(
        session_timeline_client,
        "command output full source body",
        run_id="e2e-run-1",
        occurred_at="2026-06-20T10:00:00Z",
        metadata={
            "observation_id": "obs-e2e-1",
            "observation_kind": "command_result",
            "observation_status": "succeeded",
            "source_content": "top level source content",
            "raw_source": "top level raw source",
            "observation": {
                "observation_id": "obs-e2e-1",
                "session_id": "e2e-run-1",
                "observation_kind": "command_result",
                "content": "nested source content",
                "source_content": "nested source body",
                "raw_source": "nested raw source",
            },
        },
    )
    _create_memory(
        session_timeline_client,
        "no operation event",
        run_id="e2e-run-1",
        occurred_at="2026-06-20T10:01:00Z",
        metadata={
            "observation_id": "obs-e2e-2",
            "observation_kind": "none",
            "observation_status": "none",
            "observation": {
                "observation_id": "obs-e2e-2",
                "session_id": "e2e-run-1",
                "observation_kind": "none",
            },
        },
    )
    _create_memory(
        session_timeline_client,
        "alias session hook record",
        run_id=None,
        occurred_at="2026-06-20T10:02:00Z",
        metadata={
            "observation_id": "obs-e2e-alias",
            "session_id": "alias-session-e2e",
            "observation_kind": "session_start",
            "observation": {
                "observation_id": "obs-e2e-alias",
                "session_id": "alias-session-e2e",
                "observation_kind": "session_start",
            },
        },
    )

    list_response = session_timeline_client.get(
        "/api/v1/memories",
        headers=API_HEADERS,
        params={"run_id": "e2e-run-1"},
    )
    assert list_response.status_code == 200
    assert list_response.json()["data"]["total"] == 2

    sessions_response = session_timeline_client.get(
        "/api/v1/memories/sessions",
        headers=API_HEADERS,
    )
    assert sessions_response.status_code == 200
    sessions = sessions_response.json()["data"]["sessions"]
    assert {session["run_id"] for session in sessions} == {
        "e2e-run-1",
        "alias-session-e2e",
    }
    assert sessions_response.json()["data"]["precision"] == "memory_snapshot"

    alias_timeline_response = session_timeline_client.get(
        "/api/v1/memories/timeline",
        headers=API_HEADERS,
        params={"run_id": "alias-session-e2e"},
    )
    assert alias_timeline_response.status_code == 200
    assert alias_timeline_response.json()["data"]["total"] == 1

    stats_response = session_timeline_client.get(
        "/api/v1/memories/session-stats",
        headers=API_HEADERS,
        params={"run_id": "e2e-run-1"},
    )
    assert stats_response.status_code == 200
    assert stats_response.json()["data"]["no_op_rate"] == 0.5

    timeline_response = session_timeline_client.get(
        "/api/v1/memories/timeline",
        headers=API_HEADERS,
        params={"run_id": "e2e-run-1", "event_type": "command_result"},
    )
    assert timeline_response.status_code == 200
    event = timeline_response.json()["data"]["events"][0]
    assert event["memory_id"] == first_memory_id
    assert event["precision"] == "memory_snapshot"
    assert "source_content" not in event
    assert "source_content" not in event["metadata"]
    assert event["metadata"]["source_content_preview"] == "top level source content"
    assert "content" not in event["metadata"]["observation"]
    assert "raw_source" not in event["metadata"]["observation"]
    assert event["metadata"]["observation"]["content_preview"] == "nested source content"
    assert event["metadata"]["observation"]["raw_source_preview"] == "nested raw source"

    source_response = session_timeline_client.get(
        "/api/v1/memories/timeline",
        headers=API_HEADERS,
        params={
            "run_id": "e2e-run-1",
            "event_type": "command_result",
            "include_source": "true",
        },
    )
    assert source_response.status_code == 200
    source_event = source_response.json()["data"]["events"][0]
    assert source_event["source_content"] == "command output full source body"
    assert source_event["metadata"]["source_content"] == "top level source content"

    bad_cursor_response = session_timeline_client.get(
        "/api/v1/memories/timeline",
        headers=API_HEADERS,
        params={"cursor": "!"},
    )
    assert bad_cursor_response.status_code == 200
