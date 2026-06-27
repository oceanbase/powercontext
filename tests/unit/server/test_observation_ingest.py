from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from server.api.v1.observations import _observation_result_response
from server.api.v1 import router as v1_router
from server.config import config
from server.models.errors import APIError, ErrorCode
from server.models.request import ObservationIngestRequest
from server.services.memory_service import MemoryService


def _observation(**overrides):
    data = {
        "content": "pytest failed with exit code 1",
        "observation_id": "obs-1",
        "observation_kind": "command_result",
        "observation_level": "error",
        "observation_status": "failed",
        "repo": "powermem",
        "branch": "feature-x",
        "commit_sha": "abc123",
        "tool_name": "pytest",
        "task_id": "task-1",
        "thread_id": "thread-1",
    }
    data.update(overrides)
    return ObservationIngestRequest(**data)


def test_observation_ingest_defaults_to_raw_without_inference():
    request = _observation()

    assert request.save_raw is True
    assert request.infer is False
    assert request.scope == "coding_agent"
    assert request.memory_type == "coding_agent_observation"


def test_observation_ingest_requires_content():
    with pytest.raises(ValidationError):
        ObservationIngestRequest(observation_id="obs-1")

    with pytest.raises(ValidationError):
        ObservationIngestRequest(content="   ")


def test_observation_ingest_normalizes_content():
    request = _observation(content="  pytest failed with exit code 1  ")

    assert request.content == "pytest failed with exit code 1"


def test_observation_ingest_rejects_noop_mode():
    with pytest.raises(ValidationError):
        _observation(save_raw=False, infer=False)


def test_observation_metadata_contains_flat_fields_and_full_payload():
    service = MemoryService.__new__(MemoryService)
    request = _observation(
        metadata={
            "custom": "value",
            "attempt": 2,
            "nested": {"suite": "unit"},
            "tags": ["pytest"],
        }
    )

    metadata = service._build_observation_metadata(
        request,
        record_kind="observation_raw",
    )

    assert metadata["schema"] == "powermem.coding_agent_observation.v1"
    assert metadata["source"] == "coding_agent"
    assert metadata["record_kind"] == "observation_raw"
    assert metadata["memory_type"] == "coding_agent_observation"
    assert metadata["scope"] == "coding_agent"
    assert metadata["observation_id"] == "obs-1"
    assert metadata["observation_kind"] == "command_result"
    assert metadata["observation_level"] == "error"
    assert metadata["observation_status"] == "failed"
    assert metadata["repo"] == "powermem"
    assert metadata["branch"] == "feature-x"
    assert metadata["tool_name"] == "pytest"
    assert metadata["custom"] == "value"
    assert metadata["attempt"] == 2
    assert "nested" not in metadata
    assert "tags" not in metadata
    assert metadata["observation"]["content"] == "pytest failed with exit code 1"
    assert metadata["observation"]["commit_sha"] == "abc123"
    assert metadata["observation"]["metadata"]["nested"] == {"suite": "unit"}
    assert metadata["observation"]["metadata"]["tags"] == ["pytest"]


def test_ingest_observation_reuses_existing_raw_observation_when_deduping():
    service = MemoryService.__new__(MemoryService)
    existing = {
        "id": 123,
        "memory": "pytest failed with exit code 1",
        "metadata": {"observation_id": "obs-1"},
    }
    service.memory = MagicMock()
    service.memory.get_all.return_value = {"results": [existing]}
    service.create_memory = MagicMock()

    result = service.ingest_observation(_observation(dedupe=True))

    assert result["deduped"] is True
    assert result["raw_memory"] == existing
    assert result["memories"] == [existing]
    service.memory.get_all.assert_called_once()
    assert service.memory.get_all.call_args.kwargs["filters"] == {
        "schema": "powermem.coding_agent_observation.v1",
        "source": "coding_agent",
        "record_kind": "observation_raw",
        "observation_id": "obs-1",
    }
    service.create_memory.assert_not_called()


def test_ingest_observation_dedupe_still_runs_requested_inference():
    service = MemoryService.__new__(MemoryService)
    existing = {
        "id": 123,
        "memory": "pytest failed with exit code 1",
        "metadata": {"observation_id": "obs-1"},
    }
    semantic = {
        "id": 456,
        "memory": "pytest failed",
        "metadata": {"record_kind": "observation_semantic"},
    }
    service.memory = MagicMock()
    service.memory.get_all.side_effect = [
        {"results": [existing]},
        {"results": []},
    ]
    service.create_memory = MagicMock(return_value=[semantic])

    result = service.ingest_observation(_observation(dedupe=True, infer=True))

    assert result["deduped"] is True
    assert result["inferred"] is True
    assert result["raw_memory"] == existing
    assert result["semantic_memories"] == [semantic]
    assert result["memories"] == [existing, semantic]
    assert service.memory.get_all.call_count == 2
    service.create_memory.assert_called_once()
    assert service.create_memory.call_args.kwargs["infer"] is True


def test_ingest_observation_dedupe_reuses_existing_semantic_memory():
    service = MemoryService.__new__(MemoryService)
    existing_raw = {
        "id": 123,
        "memory": "pytest failed with exit code 1",
        "metadata": {"record_kind": "observation_raw"},
    }
    existing_semantic = {
        "id": 456,
        "memory": "pytest failed",
        "metadata": {"record_kind": "observation_semantic"},
    }
    service.memory = MagicMock()
    service.memory.get_all.side_effect = [
        {"results": [existing_raw]},
        {"results": [existing_semantic]},
    ]
    service.create_memory = MagicMock()

    result = service.ingest_observation(_observation(dedupe=True, infer=True))

    assert result["deduped"] is True
    assert result["raw_memory"] == existing_raw
    assert result["semantic_memories"] == [existing_semantic]
    assert result["memories"] == [existing_raw, existing_semantic]
    service.create_memory.assert_not_called()


def test_ingest_observation_dedupe_reuses_semantic_without_raw_mode():
    service = MemoryService.__new__(MemoryService)
    existing_semantic = {
        "id": 456,
        "memory": "pytest failed",
        "metadata": {"record_kind": "observation_semantic"},
    }
    service.memory = MagicMock()
    service.memory.get_all.return_value = {"results": [existing_semantic]}
    service.create_memory = MagicMock()

    result = service.ingest_observation(
        _observation(save_raw=False, infer=True, dedupe=True)
    )

    assert result["deduped"] is True
    assert result["raw_memory"] is None
    assert result["semantic_memories"] == [existing_semantic]
    assert result["memories"] == [existing_semantic]
    service.memory.get_all.assert_called_once()
    assert service.memory.get_all.call_args.kwargs["filters"]["record_kind"] == "observation_semantic"
    service.create_memory.assert_not_called()


def test_ingest_observation_allows_infer_without_raw_persistence():
    service = MemoryService.__new__(MemoryService)
    semantic = {
        "id": 456,
        "memory": "pytest failed",
        "metadata": {"record_kind": "observation_semantic"},
    }
    service.create_memory = MagicMock(return_value=[semantic])

    result = service.ingest_observation(_observation(save_raw=False, infer=True))

    assert result["raw_memory"] is None
    assert result["saved_raw"] is False
    assert result["inferred"] is True
    assert result["semantic_memories"] == [semantic]
    assert result["memories"] == [semantic]
    service.create_memory.assert_called_once()
    call_kwargs = service.create_memory.call_args.kwargs
    assert call_kwargs["infer"] is True
    assert call_kwargs["metadata"]["record_kind"] == "observation_semantic"


def test_batch_ingest_observations_keeps_partial_failures():
    service = MemoryService.__new__(MemoryService)
    service.ingest_observation = MagicMock(
        side_effect=[
            {
                "observation_id": "obs-1",
                "raw_memory": {"id": 1, "memory": "ok"},
                "semantic_memories": [],
                "memories": [{"id": 1, "memory": "ok"}],
                "saved_raw": True,
                "inferred": False,
                "deduped": False,
            },
            APIError(
                code=ErrorCode.MEMORY_CREATE_FAILED,
                message="store failed",
                status_code=500,
            ),
        ]
    )

    result = service.batch_ingest_observations([
        _observation(observation_id="obs-1"),
        _observation(observation_id="obs-2"),
    ])

    assert result["total"] == 2
    assert result["success_count"] == 1
    assert result["failed_count"] == 1
    assert result["items"][0]["success"] is True
    assert result["items"][1]["success"] is False
    assert result["items"][1]["error"] == "store failed"


def test_batch_ingest_observations_keeps_validation_failures_per_item():
    service = MemoryService.__new__(MemoryService)
    service.create_memory = MagicMock(
        return_value=[
            {
                "id": 1,
                "memory": "ok",
                "metadata": {"observation_id": "obs-1"},
            }
        ]
    )

    result = service.batch_ingest_observations([
        {"content": "ok", "observation_id": "obs-1"},
        {"content": "   ", "observation_id": "obs-2"},
        {"content": "noop", "observation_id": "obs-3", "save_raw": False, "infer": False},
    ])

    assert result["total"] == 3
    assert result["success_count"] == 1
    assert result["failed_count"] == 2
    assert result["items"][0]["success"] is True
    assert result["items"][1]["success"] is False
    assert result["items"][1]["observation_id"] == "obs-2"
    assert "Request validation failed" in result["items"][1]["error"]
    assert result["items"][2]["success"] is False
    assert result["items"][2]["observation_id"] == "obs-3"
    assert "Request validation failed" in result["items"][2]["error"]


def test_observation_route_response_reuses_memory_serialization():
    response = _observation_result_response(
        {
            "observation_id": "obs-1",
            "raw_memory": {
                "id": 123,
                "memory": "pytest failed with exit code 1",
                "metadata": {"observation_id": "obs-1"},
            },
            "semantic_memories": [],
            "memories": [
                {
                    "id": 123,
                    "memory": "pytest failed with exit code 1",
                    "metadata": {"observation_id": "obs-1"},
                }
            ],
            "saved_raw": True,
            "inferred": False,
            "deduped": False,
        }
    )

    assert response.raw_memory.memory_id == 123
    assert response.raw_memory.id == "123"
    assert response.memories[0].content == "pytest failed with exit code 1"


def test_observation_router_is_registered_under_api_v1():
    app = FastAPI()
    app.include_router(v1_router)
    paths = app.openapi()["paths"]

    assert "post" in paths["/api/v1/observations"]
    assert "post" in paths["/api/v1/observations/batch"]


class FakeObservationService:
    def _get(self, item, key):
        if isinstance(item, dict):
            return item.get(key)
        return getattr(item, key)

    def ingest_observation(self, body):
        return {
            "observation_id": self._get(body, "observation_id"),
            "raw_memory": {
                "id": 123,
                "memory": self._get(body, "content"),
                "metadata": {"observation_id": self._get(body, "observation_id")},
            },
            "semantic_memories": [],
            "memories": [
                {
                    "id": 123,
                    "memory": self._get(body, "content"),
                    "metadata": {"observation_id": self._get(body, "observation_id")},
                }
            ],
            "saved_raw": True,
            "inferred": bool(self._get(body, "infer")),
            "deduped": False,
        }

    def batch_ingest_observations(self, observations):
        return {
            "items": [
                {
                    "index": 0,
                    "success": True,
                    "observation_id": self._get(observations[0], "observation_id"),
                    "result": self.ingest_observation(observations[0]),
                    "error": None,
                },
                {
                    "index": 1,
                    "success": False,
                    "observation_id": self._get(observations[1], "observation_id"),
                    "result": None,
                    "error": "store failed",
                },
            ],
            "total": 2,
            "success_count": 1,
            "failed_count": 1,
        }


class FakeSearchService:
    def __init__(self):
        self.search_kwargs = None

    def search_memories(self, **kwargs):
        self.search_kwargs = kwargs
        return {
            "results": [
                {
                    "id": 789,
                    "memory": "pytest failed",
                    "score": 0.91,
                    "metadata": {
                        "scope": "coding_agent",
                        "observation_id": "obs-1",
                        "observation_kind": "command_result",
                    },
                }
            ]
        }


@pytest.fixture
def observation_client(monkeypatch):
    monkeypatch.setattr(config, "auth_enabled", True)
    monkeypatch.setattr(config, "api_keys", "secret")
    app = FastAPI()
    app.state.memory_service = FakeObservationService()
    app.state.search_service = FakeSearchService()
    app.include_router(v1_router)
    return TestClient(app)


def test_observation_route_requires_api_key(observation_client):
    response = observation_client.post(
        "/api/v1/observations",
        json={"content": "pytest failed", "observation_id": "obs-1"},
    )

    assert response.status_code == 401


def test_observation_route_response_shape_with_api_key(observation_client):
    response = observation_client.post(
        "/api/v1/observations",
        headers={"X-API-Key": "secret"},
        json={"content": "pytest failed", "observation_id": "obs-1"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["data"]["observation_id"] == "obs-1"
    assert payload["data"]["raw_memory"]["memory_id"] == "123"
    assert payload["data"]["saved_raw"] is True
    assert payload["data"]["inferred"] is False


def test_observation_batch_route_reports_partial_failure(observation_client):
    response = observation_client.post(
        "/api/v1/observations/batch",
        headers={"X-API-Key": "secret"},
        json={
            "observations": [
                {"content": "first", "observation_id": "obs-1"},
                {"content": "second", "observation_id": "obs-2"},
            ]
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"]["total"] == 2
    assert payload["data"]["success_count"] == 1
    assert payload["data"]["failed_count"] == 1
    assert payload["data"]["items"][0]["success"] is True
    assert payload["data"]["items"][1]["success"] is False
    assert payload["data"]["items"][1]["error"] == "store failed"


def test_search_route_passes_observation_metadata_filters(observation_client):
    filters = {
        "scope": "coding_agent",
        "observation_id": "obs-1",
        "observation_kind": "command_result",
    }

    response = observation_client.post(
        "/api/v1/memories/search",
        headers={"X-API-Key": "secret"},
        json={
            "query": "pytest failed",
            "filters": filters,
            "limit": 10,
        },
    )

    assert response.status_code == 200
    assert observation_client.app.state.search_service.search_kwargs["filters"] == filters
    payload = response.json()
    assert payload["data"]["total"] == 1
    assert payload["data"]["results"][0]["metadata"]["observation_id"] == "obs-1"


def test_observation_batch_route_reports_invalid_item_without_422(monkeypatch):
    monkeypatch.setattr(config, "auth_enabled", True)
    monkeypatch.setattr(config, "api_keys", "secret")
    service = MemoryService.__new__(MemoryService)
    service.create_memory = MagicMock(
        return_value=[
            {
                "id": 123,
                "memory": "valid",
                "metadata": {"observation_id": "obs-valid"},
            }
        ]
    )
    app = FastAPI()
    app.state.memory_service = service
    app.include_router(v1_router)
    client = TestClient(app)

    response = client.post(
        "/api/v1/observations/batch",
        headers={"X-API-Key": "secret"},
        json={
            "observations": [
                {"content": "valid", "observation_id": "obs-valid"},
                {"content": "   ", "observation_id": "obs-invalid"},
            ]
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["data"]["success_count"] == 1
    assert payload["data"]["failed_count"] == 1
    assert payload["data"]["items"][1]["observation_id"] == "obs-invalid"
    assert "Request validation failed" in payload["data"]["items"][1]["error"]
