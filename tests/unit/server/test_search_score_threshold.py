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


def test_search_request_accepts_retrieval_parameters():
    request = SearchRequest(
        query="coffee",
        retrieval_mode="hybrid",
        fusion="weighted",
        vector_weight=0.7,
        fts_weight=0.3,
        rrf_k=30,
        candidate_limit=50,
        include_explanation=True,
    )

    assert request.retrieval_mode == "hybrid"
    assert request.fusion == "weighted"
    assert request.vector_weight == 0.7
    assert request.fts_weight == 0.3
    assert request.rrf_k == 30
    assert request.candidate_limit == 50
    assert request.include_explanation is True


def test_search_request_omits_weights_by_default():
    request = SearchRequest(query="coffee")

    assert request.vector_weight is None
    assert request.fts_weight is None


def test_search_get_route_exposes_retrieval_query_parameters():
    signature = inspect.signature(search_api.search_memories_get)

    assert "threshold" in signature.parameters
    assert "retrieval_mode" in signature.parameters
    assert "fusion" in signature.parameters
    assert "include_explanation" in signature.parameters


@pytest.mark.parametrize("threshold", [-0.1, 1.1])
def test_search_request_rejects_invalid_threshold(threshold):
    with pytest.raises(ValidationError):
        SearchRequest(query="coffee", threshold=threshold)


@pytest.mark.parametrize(
    "field,value",
    [
        ("retrieval_mode", "semantic"),
        ("fusion", "sum"),
        ("vector_weight", 1.1),
        ("fts_weight", -0.1),
        ("rrf_k", 0),
        ("candidate_limit", 0),
    ],
)
def test_search_request_rejects_invalid_retrieval_parameters(field, value):
    with pytest.raises(ValidationError):
        SearchRequest(query="coffee", **{field: value})


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


def test_search_service_passes_retrieval_parameters_to_memory_search(monkeypatch):
    fake_memory = FakeMemory()
    service = SearchService.__new__(SearchService)
    service.memory = fake_memory
    monkeypatch.setattr(
        "server.services.search_service.get_metrics_collector",
        lambda: FakeMetricsCollector(),
    )

    service.search_memories(
        query="coffee",
        retrieval_mode="fts",
        fusion="weighted",
        vector_weight=0.2,
        fts_weight=0.8,
        rrf_k=25,
        candidate_limit=40,
        include_explanation=True,
    )

    assert fake_memory.search_kwargs["retrieval_mode"] == "fts"
    assert fake_memory.search_kwargs["fusion"] == "weighted"
    assert fake_memory.search_kwargs["vector_weight"] == 0.2
    assert fake_memory.search_kwargs["fts_weight"] == 0.8
    assert fake_memory.search_kwargs["rrf_k"] == 25
    assert fake_memory.search_kwargs["candidate_limit"] == 40
    assert fake_memory.search_kwargs["include_explanation"] is True
