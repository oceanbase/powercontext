"""Unit tests for the NVIDIA NIM rerank provider.

The NIM `/v1/ranking` endpoint is exercised via an injected mock http_client
(no live GPU/NIM container required).
"""
from unittest.mock import Mock

import pytest

from powermem.integrations.rerank.config.base import BaseRerankConfig
from powermem.integrations.rerank.config.providers import NimRerankConfig
from powermem.integrations.rerank.factory import RerankFactory
from powermem.integrations.rerank.nim import NimRerank, _ranking_url


def _mock_client(response_json):
    """Build a mock httpx-like client whose .post() returns response_json."""
    resp = Mock()
    resp.json.return_value = response_json
    resp.raise_for_status.return_value = None
    client = Mock()
    client.post.return_value = resp
    return client, resp


def _make_rerank(client, **overrides):
    cfg = NimRerankConfig(
        api_base_url="http://nim:8002/v1",
        model="baai/bge-reranker-v2-m3",
        api_key="nvapi-test",
        http_client=client,
        **overrides,
    )
    return NimRerank(cfg)


def test_rankings_parsed_and_sorted_desc():
    client, _ = _mock_client({
        "rankings": [
            {"index": 0, "score": 0.03, "logit": -3.5},
            {"index": 1, "score": 0.98, "logit": 4.1},
        ]
    })
    r = _make_rerank(client)
    assert r.rerank("q", ["doc0", "doc1"]) == [(1, 0.98), (0, 0.03)]


def test_request_payload_matches_nim_shape():
    client, _ = _mock_client({"rankings": [{"index": 0, "score": 0.5}]})
    r = _make_rerank(client)
    r.rerank("query", ["a", "b"])

    url = client.post.call_args.args[0]
    payload = client.post.call_args.kwargs["json"]
    headers = client.post.call_args.kwargs["headers"]

    assert url == "http://nim:8002/v1/ranking"
    assert payload["model"] == "baai/bge-reranker-v2-m3"
    assert payload["query"] == {"text": "query"}
    assert payload["passages"] == [{"text": "a"}, {"text": "b"}]
    assert "return_text" not in payload  # real NIM rejects unknown fields
    assert headers["Authorization"] == "Bearer nvapi-test"


def test_top_n_slicing():
    client, _ = _mock_client({
        "rankings": [
            {"index": 0, "score": 0.9},
            {"index": 1, "score": 0.8},
            {"index": 2, "score": 0.7},
        ]
    })
    r = _make_rerank(client)
    assert r.rerank("q", ["a", "b", "c"], top_n=2) == [(0, 0.9), (1, 0.8)]


def test_logit_fallback_when_score_missing():
    client, _ = _mock_client({
        "rankings": [
            {"index": 0, "logit": -2.0},
            {"index": 1, "logit": 3.0},
        ]
    })
    r = _make_rerank(client)
    assert r.rerank("q", ["a", "b"]) == [(1, 3.0), (0, -2.0)]


@pytest.mark.parametrize(
    "base,expected",
    [
        ("http://h:8002", "http://h:8002/v1/ranking"),
        ("http://h:8002/v1", "http://h:8002/v1/ranking"),
        ("http://h:8002/v1/", "http://h:8002/v1/ranking"),
        ("http://h:8002/v1/ranking", "http://h:8002/v1/ranking"),
    ],
)
def test_ranking_url_normalization(base, expected):
    assert _ranking_url(base) == expected


def test_factory_creates_nim_provider():
    client, _ = _mock_client({"rankings": [{"index": 0, "score": 0.1}]})
    reranker = RerankFactory.create(
        "nim",
        {
            "api_base_url": "http://nim:8002/v1",
            "model": "baai/bge-reranker-v2-m3",
            "http_client": client,
        },
    )
    assert isinstance(reranker, NimRerank)
    assert "nim" in BaseRerankConfig._registry


def test_empty_inputs_raise():
    client, _ = _mock_client({"rankings": []})
    r = _make_rerank(client)
    with pytest.raises(ValueError):
        r.rerank("", ["a"])
    with pytest.raises(ValueError):
        r.rerank("q", [])


def test_missing_base_url_or_model_raises():
    with pytest.raises(ValueError):
        NimRerank(NimRerankConfig(model="x"))  # no api_base_url
    with pytest.raises(ValueError):
        NimRerank(NimRerankConfig(api_base_url="http://h:8002/v1", model=""))


def test_unexpected_response_raises():
    client, _ = _mock_client({"not_rankings": []})
    r = _make_rerank(client)
    with pytest.raises(Exception):
        r.rerank("q", ["a"])
