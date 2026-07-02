from types import SimpleNamespace

import pytest

pytest.importorskip("pyobvector")

from powermem.storage.base import OutputData
from powermem.storage.oceanbase.oceanbase import OceanBaseVectorStore


class _FakeRow:
    def __init__(self, mapping):
        self._mapping = mapping


class _FakeResult:
    returns_rows = True

    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeTransaction:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeConnection:
    def __init__(self, rows):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def begin(self):
        return _FakeTransaction()

    def execute(self, stmt):
        return _FakeResult(self._rows)


class _FakeEngine:
    def __init__(self, rows):
        self._rows = rows

    def connect(self):
        return _FakeConnection(self._rows)


def _make_store_with_fts_score(raw_score):
    store = OceanBaseVectorStore.__new__(OceanBaseVectorStore)
    store.collection_name = "memories"
    store.fulltext_field = "fulltext_content"
    store.obvector = SimpleNamespace(
        engine=_FakeEngine([_FakeRow({"score": raw_score})])
    )
    store._generate_where_clause = lambda filters: []
    store._get_standard_select_columns = lambda: []
    store._parse_row_to_dict = lambda row, **kwargs: {
        "vector_id": 1,
        "text_content": "needle memory",
        "metadata": {"user_id": "u1", "metadata": {}},
        "score_or_distance": row["score"],
    }
    return store


def _make_fusion_store():
    store = OceanBaseVectorStore.__new__(OceanBaseVectorStore)
    store.vector_weight = 0.5
    store.fts_weight = 0.5
    store.sparse_weight = 0.25
    store.include_sparse = False
    return store


def test_fulltext_search_keeps_raw_fts_score_but_normalizes_quality_score():
    store = _make_store_with_fts_score(0.000001)

    results = store._fulltext_search("needle", limit=5)

    assert len(results) == 1
    payload = results[0].payload
    assert payload["_fts_score"] == pytest.approx(0.000001)
    assert payload["_fts_quality_score"] == 1.0
    assert payload["_quality_score"] == 1.0

    thresholded = store.search(
        "needle",
        vectors=None,
        retrieval_mode="fts",
        threshold=0.3,
    )
    assert len(thresholded) == 1


@pytest.mark.parametrize("fusion_method", ["rrf", "weighted"])
def test_hybrid_quality_score_uses_normalized_fts_quality(fusion_method):
    store = _make_fusion_store()
    fts_result = OutputData(
        id=1,
        score=0.000001,
        payload={
            "data": "needle memory",
            "_fts_score": 0.000001,
            "_fts_quality_score": 1.0,
        },
    )

    results = store._combine_search_results(
        vector_results=[],
        fts_results=[fts_result],
        sparse_results=[],
        limit=1,
        fusion_method=fusion_method,
    )

    assert len(results) == 1
    assert results[0].payload["_fts_score"] == pytest.approx(0.000001)
    assert results[0].payload["_quality_score"] == 1.0


@pytest.mark.parametrize("fusion_method", ["rrf", "weighted"])
def test_hybrid_preserves_fts_quality_when_doc_matches_vector_and_fts(
    fusion_method,
):
    store = _make_fusion_store()
    vector_result = OutputData(
        id=1,
        score=0.2,
        payload={
            "data": "needle memory",
            "_vector_similarity": 0.2,
        },
    )
    fts_result = OutputData(
        id=1,
        score=0.000001,
        payload={
            "data": "needle memory",
            "_fts_score": 0.000001,
            "_fts_quality_score": 1.0,
        },
    )

    results = store._combine_search_results(
        vector_results=[vector_result],
        fts_results=[fts_result],
        sparse_results=[],
        limit=1,
        fusion_method=fusion_method,
    )

    assert len(results) == 1
    assert results[0].payload["_fts_score"] == pytest.approx(0.000001)
    assert results[0].payload["_fts_quality_score"] == 1.0
    assert results[0].payload["_quality_score"] == pytest.approx(0.6)


def test_hybrid_search_applies_threshold_at_oceanbase_store_layer():
    store = _make_fusion_store()
    store.hybrid_search = True
    store.enable_native_hybrid = False
    store.connection_args = {}
    store.reranker = None

    def vector_search(query, vectors, limit, filters):
        return [
            OutputData(
                id=1,
                score=0.2,
                payload={
                    "data": "needle memory",
                    "_vector_similarity": 0.2,
                },
            )
        ]

    def fulltext_search(query, limit, filters):
        return [
            OutputData(
                id=1,
                score=0.000001,
                payload={
                    "data": "needle memory",
                    "_fts_score": 0.000001,
                    "_fts_quality_score": 1.0,
                },
            )
        ]

    store._vector_search = vector_search
    store._fulltext_search = fulltext_search

    kept = store.search(
        "needle",
        vectors=[[0.1, 0.2, 0.3]],
        retrieval_mode="hybrid",
        threshold=0.5,
    )
    filtered = store.search(
        "needle",
        vectors=[[0.1, 0.2, 0.3]],
        retrieval_mode="hybrid",
        threshold=0.7,
    )

    assert len(kept) == 1
    assert kept[0].payload["_quality_score"] == pytest.approx(0.6)
    assert filtered == []
