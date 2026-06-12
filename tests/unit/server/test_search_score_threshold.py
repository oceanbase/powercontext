import inspect

import pytest
from pydantic import ValidationError

from server.api.v1 import search as search_api
from server.models.request import SearchRequest
from server.services.search_service import SearchService
from server.utils.converters import search_result_to_response


class FakeMemory:
    def __init__(self):
        self.search_kwargs = None

    def search(self, **kwargs):
        self.search_kwargs = kwargs
        return {"results": []}


class FakeMetricsCollector:
    def record_memory_operation(self, operation, status):
        pass


def test_search_result_to_response_preserves_zero_score():
    result = search_result_to_response(
        {
            "id": 1,
            "content": "zero score is still a valid score",
            "score": 0.0,
        }
    )

    assert result.score == 0.0


def test_search_result_to_response_falls_back_to_similarity_only_when_score_is_none():
    result = search_result_to_response(
        {
            "id": 1,
            "content": "fallback score",
            "score": None,
            "similarity": 0.42,
        }
    )

    assert result.score == 0.42


def test_search_request_accepts_threshold():
    request = SearchRequest(query="coffee", limit=10, threshold=0.3)

    assert request.threshold == 0.3


def test_search_get_route_exposes_threshold_query_parameter():
    signature = inspect.signature(search_api.search_memories_get)

    assert "threshold" in signature.parameters


@pytest.mark.parametrize("threshold", [-0.1, 1.1])
def test_search_request_rejects_invalid_threshold(threshold):
    with pytest.raises(ValidationError):
        SearchRequest(query="coffee", threshold=threshold)


def test_search_service_passes_threshold_to_memory_search(monkeypatch):
    fake_memory = FakeMemory()
    service = SearchService.__new__(SearchService)
    service.memory = fake_memory
    monkeypatch.setattr(
        "server.services.search_service.get_metrics_collector",
        lambda: FakeMetricsCollector(),
    )

    service.search_memories(query="coffee", threshold=0.3)

    assert fake_memory.search_kwargs["threshold"] == 0.3
