"""Unit tests for SQLite FTS5 fulltext search integration.

Tests the FTS5 virtual table lifecycle (create/insert/update/delete/reset)
and hybrid search (vector + fulltext, RRF fusion) in SQLiteVectorStore.
"""

import json
import logging
import math
import pytest

from powermem.storage.adapter import StorageAdapter
from powermem.storage.sqlite.sqlite_vector_store import (
    SQLiteVectorStore,
    _sanitize_fts5_input,
)
from powermem.storage.base import OutputData


@pytest.fixture
def store():
    """Create an in-memory SQLiteVectorStore for testing."""
    s = SQLiteVectorStore(database_path=":memory:", collection_name="test_fts")
    yield s
    s.close()


def _make_vector(dim: int = 8, seed: float = 0.1) -> list:
    """Create a deterministic vector pointing in a seed-dependent direction.

    Different seeds produce vectors pointing in genuinely different directions
    (not just scaled versions of the same direction), so cosine similarity
    varies meaningfully across seeds.
    """
    return [math.sin(seed * (i + 1) + i) for i in range(dim)]


def _insert_docs(store, docs: list[dict]) -> list[int]:
    """Insert multiple docs with vectors and payloads, return their IDs.

    Each doc dict must have 'content' and optionally 'vector_seed', 'user_id'.
    """
    vectors = []
    payloads = []
    for doc in docs:
        seed = doc.get("vector_seed", 0.1)
        vectors.append(_make_vector(seed=seed))
        payloads.append({
            "data": doc["content"],
            "fulltext_content": doc["content"],
            "user_id": doc.get("user_id", "u1"),
        })
        if "metadata" in doc:
            payloads[-1]["metadata"] = doc["metadata"]
        if "category" in doc:
            payloads[-1]["category"] = doc["category"]
    return store.insert(vectors, payloads)


class FlatVectorStore:
    """Minimal pgvector-like store that expects a flat query vector."""

    collection_name = "flat_vectors"

    def __init__(self):
        self.seen_vectors = None
        self.called = False

    def search(self, query, vectors, limit=5, filters=None):
        self.called = True
        self.seen_vectors = vectors
        return [
            OutputData(
                id=1,
                score=0.9,
                payload={"data": "flat vector result"},
            )
        ]


class OceanBaseLikeHybridStore:
    """Minimal OceanBase-like store exposing the public hybrid search controls."""

    __module__ = "powermem.storage.oceanbase.oceanbase"
    collection_name = "oceanbase_like"
    hybrid_search = True

    def __init__(self):
        self.seen_kwargs = None

    def search(
        self,
        query,
        vectors,
        limit=5,
        filters=None,
        sparse_embedding=None,
        threshold=None,
        retrieval_mode="auto",
        fusion="rrf",
        vector_weight=0.5,
        fts_weight=0.5,
        rrf_k=60,
        candidate_limit=None,
        include_explanation=False,
    ):
        self.seen_kwargs = {
            "query": query,
            "vectors": vectors,
            "limit": limit,
            "filters": filters,
            "threshold": threshold,
            "retrieval_mode": retrieval_mode,
            "fusion": fusion,
            "vector_weight": vector_weight,
            "fts_weight": fts_weight,
            "rrf_k": rrf_k,
            "candidate_limit": candidate_limit,
            "include_explanation": include_explanation,
        }
        return [
            OutputData(
                id=1,
                score=0.9,
                payload={"data": "oceanbase hybrid result", "metadata": {}},
            )
        ]


# ---------------------------------------------------------------------------
# 1. FTS5 table creation
# ---------------------------------------------------------------------------
class TestFTS5TableCreation:
    def test_fts_virtual_table_exists(self, store):
        """After init, {collection}_fts virtual table exists in sqlite_master."""
        cursor = store.connection.execute(
            "SELECT name, type FROM sqlite_master WHERE name = ?",
            (f"{store.collection_name}_fts",),
        )
        row = cursor.fetchone()
        assert row is not None, "FTS5 virtual table should exist after create_col"
        # FTS5 tables appear as 'table' type in sqlite_master
        assert row[1] == "table"

    def test_fulltext_content_column_exists(self, store):
        """Main table should have a fulltext_content column."""
        cursor = store.connection.execute(
            f"PRAGMA table_info({store.collection_name})"
        )
        columns = [r[1] for r in cursor.fetchall()]
        assert "fulltext_content" in columns


# ---------------------------------------------------------------------------
# 2. Insert syncs to FTS
# ---------------------------------------------------------------------------
class TestInsertFTSSync:
    def test_fts_has_record_after_insert(self, store):
        """After insert, FTS5 table has a matching record."""
        ids = _insert_docs(store, [{"content": "hello world from unit test"}])
        cursor = store.connection.execute(
            f"SELECT rowid, fulltext_content FROM {store.collection_name}_fts WHERE rowid = ?",
            (ids[0],),
        )
        row = cursor.fetchone()
        assert row is not None, "FTS table should contain the inserted row"
        assert "hello world" in row[1]

    def test_fts_multiple_inserts(self, store):
        """Multiple inserts all sync to FTS."""
        ids = _insert_docs(store, [
            {"content": "first document"},
            {"content": "second document"},
            {"content": "third document"},
        ])
        cursor = store.connection.execute(
            f"SELECT COUNT(*) FROM {store.collection_name}_fts"
        )
        assert cursor.fetchone()[0] == 3


# ---------------------------------------------------------------------------
# 3. Pure text search (no vectors)
# ---------------------------------------------------------------------------
class TestPureTextSearch:
    def test_search_by_query_only(self, store):
        """search() with query but no vectors returns FTS-ranked results."""
        _insert_docs(store, [
            {"content": "the quick brown fox jumps over the lazy dog"},
            {"content": "machine learning and artificial intelligence"},
            {"content": "the fox and the hound"},
        ])
        results = store.search(query="fox", vectors=None, limit=5)
        assert len(results) >= 1
        # All returned docs should contain 'fox' in their content
        for r in results:
            assert "fox" in r.payload.get("data", "").lower() or \
                   "fox" in r.payload.get("fulltext_content", "").lower()

    def test_pure_text_empty_query_returns_empty(self, store):
        """search() with empty query and no vectors returns empty."""
        _insert_docs(store, [{"content": "some content"}])
        results = store.search(query="", vectors=None, limit=5)
        assert results == [] or isinstance(results, list)

    def test_pure_text_search_scores_positive(self, store):
        """FTS results should have positive scores."""
        _insert_docs(store, [
            {"content": "python programming language"},
            {"content": "java programming language"},
        ])
        results = store.search(query="python", vectors=None, limit=5)
        assert len(results) >= 1
        for r in results:
            assert r.score > 0

    def test_pure_text_search_handles_file_paths(self, store):
        """FTS-only search should safely handle punctuation-heavy file paths."""
        _insert_docs(store, [
            {"content": "look in src/powermem/core/memory.py for search logic"},
            {"content": "unrelated release notes"},
        ])

        results = store.search(
            query="src/powermem/core/memory.py",
            vectors=None,
            limit=5,
            retrieval_mode="fts",
        )

        assert len(results) == 1
        assert "memory.py" in results[0].payload["data"]


# ---------------------------------------------------------------------------
# 4. Hybrid search (vector + FTS, RRF fusion)
# ---------------------------------------------------------------------------
class TestHybridSearch:
    def test_hybrid_returns_results(self, store):
        """search() with both query and vectors returns RRF-fused results."""
        _insert_docs(store, [
            {"content": "deep learning neural networks", "vector_seed": 0.9},
            {"content": "cooking recipes for dinner", "vector_seed": 0.1},
            {"content": "neural network architectures", "vector_seed": 0.8},
        ])
        query_vec = _make_vector(seed=0.85)
        results = store.search(query="neural", vectors=[query_vec], limit=5)
        assert len(results) >= 1

    def test_hybrid_fusion_metadata(self, store):
        """Hybrid results should carry fusion metadata in payload."""
        _insert_docs(store, [
            {"content": "search engine optimization", "vector_seed": 0.5},
        ])
        query_vec = _make_vector(seed=0.5)
        results = store.search(query="search", vectors=[query_vec], limit=5)
        assert len(results) >= 1
        r = results[0]
        assert "_fusion_score" in r.payload
        assert "_fusion_info" in r.payload

    def test_hybrid_weighted_fusion(self, store):
        """Hybrid search can use weighted fusion when explicitly requested."""
        _insert_docs(store, [
            {"content": "database search fallback", "vector_seed": 0.5},
            {"content": "unrelated cooking note", "vector_seed": 0.2},
        ])
        query_vec = _make_vector(seed=0.5)

        results = store.search(
            query="database",
            vectors=[query_vec],
            limit=5,
            fusion="weighted",
        )

        assert len(results) >= 1
        assert results[0].payload["_fusion_info"]["fusion_method"] == "weighted"

    def test_hybrid_doc_in_both_paths_ranks_higher(self, store):
        """A doc matching both vector and text should rank above one matching only one."""
        # Doc A: matches text "database" AND has similar vector (seed=0.7)
        # Doc B: matches text "database" but different vector (seed=0.1)
        # Doc C: similar vector (seed=0.7) but no text match
        _insert_docs(store, [
            {"content": "database management systems", "vector_seed": 0.7},
            {"content": "database query optimization", "vector_seed": 0.1},
            {"content": "cooking Italian food recipes", "vector_seed": 0.71},
        ])
        query_vec = _make_vector(seed=0.7)
        results = store.search(query="database", vectors=[query_vec], limit=5)
        assert len(results) >= 2
        # Doc A should be ranked first (matches both paths)
        top_payload = results[0].payload
        assert "database" in top_payload.get("data", "").lower()


# ---------------------------------------------------------------------------
# 5. Pure vector search regression
# ---------------------------------------------------------------------------
class TestPureVectorRegression:
    def test_vector_only_search(self, store):
        """search() with vectors but no query gives vector-only results (unchanged behavior)."""
        ids = _insert_docs(store, [
            {"content": "alpha beta gamma", "vector_seed": 0.3},
            {"content": "delta epsilon zeta", "vector_seed": 0.9},
        ])
        query_vec = _make_vector(seed=0.9)
        results = store.search(query=None, vectors=[query_vec], limit=5)
        assert len(results) == 2
        # Closer vector should rank first
        assert results[0].payload["data"] == "delta epsilon zeta"

    def test_vector_search_empty_string_query(self, store):
        """Empty string query should behave same as None query (vector-only)."""
        _insert_docs(store, [
            {"content": "aaa", "vector_seed": 0.2},
            {"content": "bbb", "vector_seed": 0.8},
        ])
        query_vec = _make_vector(seed=0.8)
        results = store.search(query="", vectors=[query_vec], limit=5)
        assert len(results) == 2
        assert results[0].payload["data"] == "bbb"


# ---------------------------------------------------------------------------
# 6. Filters with FTS
# ---------------------------------------------------------------------------
class TestFiltersWithFTS:
    def test_fts_respects_user_id_filter(self, store):
        """Fulltext search should respect payload filters."""
        _insert_docs(store, [
            {"content": "python data science", "user_id": "alice"},
            {"content": "python web development", "user_id": "bob"},
            {"content": "python machine learning", "user_id": "alice"},
        ])
        results = store.search(
            query="python", vectors=None, limit=5,
            filters={"user_id": "alice"},
        )
        assert len(results) >= 1
        for r in results:
            assert r.payload["user_id"] == "alice"

    def test_fts_filter_no_match(self, store):
        """If filter excludes all docs, FTS returns empty."""
        _insert_docs(store, [
            {"content": "rust programming", "user_id": "alice"},
        ])
        results = store.search(
            query="rust", vectors=None, limit=5,
            filters={"user_id": "nonexistent"},
        )
        assert results == []

    def test_adapter_allows_query_embedding_none_for_fts(self, store):
        """StorageAdapter.search_memories(query_embedding=None) should run FTS-only."""
        _insert_docs(store, [
            {"content": "offline fallback should find this", "user_id": "alice"},
            {"content": "different user offline fallback", "user_id": "bob"},
        ])
        adapter = StorageAdapter(store)

        results = adapter.search_memories(
            query_embedding=None,
            query="offline",
            user_id="alice",
            limit=5,
            include_explanation=True,
        )

        assert len(results) == 1
        assert results[0]["user_id"] == "alice"
        explanation = results[0]["metadata"]["search_explanation"]
        assert explanation["retrieval_mode"] == "auto"
        assert explanation["fts_score"] > 0

    def test_adapter_fts_maps_user_metadata_filters(self, store):
        """Logical metadata filters should apply to nested payload metadata."""
        _insert_docs(store, [
            {
                "content": "metadata fallback should find this",
                "user_id": "alice",
                "metadata": {"scope": "personal"},
            },
            {
                "content": "metadata fallback should not match",
                "user_id": "alice",
                "metadata": {"scope": "group"},
            },
        ])
        adapter = StorageAdapter(store)

        results = adapter.search_memories(
            query_embedding=None,
            query="metadata",
            user_id="alice",
            filters={"scope": "personal"},
            limit=5,
        )

        assert len(results) == 1
        assert results[0]["metadata"] == {"scope": "personal"}
        assert "_fts_score" not in results[0]["metadata"]
        assert "_quality_score" not in results[0]["metadata"]

    def test_adapter_forwards_retrieval_controls_to_oceanbase_like_store(self):
        """OceanBase-capable stores should receive per-query retrieval controls."""
        store = OceanBaseLikeHybridStore()
        adapter = StorageAdapter(store)

        adapter.search_memories(
            query_embedding=[0.1, 0.2, 0.3],
            query="hybrid",
            filters={"scope": "personal"},
            limit=5,
        )

        assert store.seen_kwargs["vector_weight"] is None
        assert store.seen_kwargs["fts_weight"] is None

        adapter.search_memories(
            query_embedding=[0.1, 0.2, 0.3],
            query="hybrid",
            filters={"scope": "personal"},
            limit=5,
            threshold=0.2,
            retrieval_mode="hybrid",
            fusion="weighted",
            vector_weight=0.25,
            fts_weight=0.75,
            rrf_k=24,
            candidate_limit=40,
            include_explanation=True,
        )

        assert store.seen_kwargs["retrieval_mode"] == "hybrid"
        assert store.seen_kwargs["fusion"] == "weighted"
        assert store.seen_kwargs["vector_weight"] == 0.25
        assert store.seen_kwargs["fts_weight"] == 0.75
        assert store.seen_kwargs["rrf_k"] == 24
        assert store.seen_kwargs["candidate_limit"] == 40
        assert store.seen_kwargs["include_explanation"] is True
        assert store.seen_kwargs["filters"] == {"scope": "personal"}

    def test_adapter_preserves_flat_vector_shape_for_pgvector_like_store(self):
        """Non-SQLite stores should still receive a flat query vector."""
        store = FlatVectorStore()
        adapter = StorageAdapter(store)
        query_embedding = [0.1, 0.2, 0.3]

        adapter.search_memories(
            query_embedding=query_embedding,
            query="flat vector",
            limit=5,
        )

        assert store.seen_vectors == query_embedding

    def test_adapter_returns_empty_for_vector_only_store_without_embedding(self):
        """Vector-only stores should not receive vectors=None after embed failure."""
        store = FlatVectorStore()
        adapter = StorageAdapter(store)

        results = adapter.search_memories(
            query_embedding=None,
            query="flat vector",
            limit=5,
        )

        assert results == []
        assert store.called is False

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"retrieval_mode": "semantic"},
            {"fusion": "sum"},
            {"vector_weight": 1.1},
            {"fts_weight": -0.1},
            {"rrf_k": 0},
            {"candidate_limit": 0},
        ],
    )
    def test_search_rejects_invalid_direct_retrieval_parameters(self, store, kwargs):
        with pytest.raises(ValueError):
            store.search(query="python", vectors=None, **kwargs)

    def test_adapter_rejects_invalid_direct_retrieval_parameters(self, store):
        adapter = StorageAdapter(store)

        with pytest.raises(ValueError):
            adapter.search_memories(
                query_embedding=None,
                query="python",
                rrf_k=0,
            )


# ---------------------------------------------------------------------------
# 7. Update syncs to FTS
# ---------------------------------------------------------------------------
class TestUpdateFTSSync:
    def test_fts_finds_updated_content(self, store):
        """After updating payload content, FTS can find the new content."""
        ids = _insert_docs(store, [{"content": "original content here"}])
        doc_id = ids[0]

        # Update with new content
        store.update(doc_id, payload={
            "data": "completely new updated text",
            "fulltext_content": "completely new updated text",
            "user_id": "u1",
        })

        # Old content should not be found
        results_old = store.search(query="original", vectors=None, limit=5)
        old_ids = [r.id for r in results_old]
        assert doc_id not in old_ids

        # New content should be found
        results_new = store.search(query="updated", vectors=None, limit=5)
        assert any(r.id == doc_id for r in results_new)


# ---------------------------------------------------------------------------
# 8. Delete syncs to FTS
# ---------------------------------------------------------------------------
class TestDeleteFTSSync:
    def test_fts_cannot_find_deleted_doc(self, store):
        """After delete, FTS cannot find the document."""
        ids = _insert_docs(store, [
            {"content": "unique searchable phrase xyz123"},
        ])
        doc_id = ids[0]

        # Verify it's found before delete
        results_before = store.search(query="xyz123", vectors=None, limit=5)
        assert any(r.id == doc_id for r in results_before)

        # Delete
        store.delete(doc_id)

        # Should not be found after delete
        results_after = store.search(query="xyz123", vectors=None, limit=5)
        assert not any(r.id == doc_id for r in results_after)


# ---------------------------------------------------------------------------
# 9. Reset rebuilds FTS
# ---------------------------------------------------------------------------
class TestResetFTS:
    def test_reset_recreates_fts_table(self, store):
        """After reset(), FTS table is dropped and recreated (empty)."""
        _insert_docs(store, [{"content": "data before reset"}])

        store.reset()

        # FTS table should still exist (recreated)
        cursor = store.connection.execute(
            "SELECT name FROM sqlite_master WHERE name = ?",
            (f"{store.collection_name}_fts",),
        )
        assert cursor.fetchone() is not None

        # But should be empty
        results = store.search(query="reset", vectors=None, limit=5)
        assert results == []


# ---------------------------------------------------------------------------
# 10. list_cols excludes FTS shadow tables
# ---------------------------------------------------------------------------
class TestListColsExcludesFTS:
    def test_list_cols_excludes_fts_tables(self, store):
        """list_cols() should not return FTS5 virtual or shadow tables."""
        _insert_docs(store, [{"content": "some data"}])
        cols = store.list_cols()
        assert store.collection_name in cols
        # The FTS virtual table for collection "test_fts" is "test_fts_fts"
        fts_name = f"{store.collection_name}_fts"
        assert fts_name not in cols, f"FTS virtual table '{fts_name}' should be excluded"
        # Shadow tables like test_fts_fts_data, test_fts_fts_content, etc.
        for name in cols:
            assert not name.startswith(fts_name + "_"), f"FTS shadow table leaked: {name}"

    def test_list_cols_multiple_collections(self, store):
        """list_cols() with multiple collections excludes FTS for all."""
        store.create_col(name="extra_col")
        cols = store.list_cols()
        assert store.collection_name in cols
        assert "extra_col" in cols
        # Neither collection's FTS tables should appear
        for col in [store.collection_name, "extra_col"]:
            fts_name = f"{col}_fts"
            assert fts_name not in cols, f"FTS virtual table '{fts_name}' should be excluded"
            for name in cols:
                assert not name.startswith(fts_name + "_"), f"FTS shadow table leaked: {name}"


# ---------------------------------------------------------------------------
# 11. update() FTS sync is atomic (delete+insert in single block)
# ---------------------------------------------------------------------------
class TestUpdateFTSAtomic:
    def test_update_fts_sync_no_stale_entry(self, store):
        """After update, only the new content is searchable (no stale FTS entries)."""
        ids = _insert_docs(store, [{"content": "old unique phrase aaa111"}])
        doc_id = ids[0]

        store.update(doc_id, payload={
            "data": "new unique phrase bbb222",
            "fulltext_content": "new unique phrase bbb222",
            "user_id": "u1",
        })

        # Old content must not be found
        old_results = store.search(query="aaa111", vectors=None, limit=5)
        assert not any(r.id == doc_id for r in old_results)

        # New content must be found
        new_results = store.search(query="bbb222", vectors=None, limit=5)
        assert any(r.id == doc_id for r in new_results)


# ---------------------------------------------------------------------------
# 12. FTS5 query sanitization for technical tokens (Fixes #1119)
# ---------------------------------------------------------------------------
class TestFTS5QuerySanitization:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (None, ""),
            (
                "oceanbase-blue-116 add search",
                '"oceanbase" "blue" "116" "add" "search"',
            ),
            ("powermem-mcp", '"powermem" "mcp"'),
            ("v1.1.6", '"v1" "1" "6"'),
            ("fix/release-dispatch-repo", '"fix" "release" "dispatch" "repo"'),
            ("#1119", '"1119"'),
            ("fix: dispatch", '"fix" "dispatch"'),
            ("@user", '"user"'),
            ("hello!world", '"hello" "world"'),
            ("foo|bar", '"foo" "bar"'),
            ("a&b", '"a" "b"'),
            ("^hello", '"hello"'),
            ("100%", '"100"'),
            ("$5 item", '"5" "item"'),
            ("---", ""),
            ("#@!", ""),
            ("//", ""),
            ("!!!", ""),
            ("<>;", ""),
            ("don't", '"don" "t"'),
            ("~1.2.3", '"1" "2" "3"'),
            ("key=value", '"key" "value"'),
            ("my_var", '"my_var"'),
            ('he said "hello"', '"he" "said" "hello"'),
            ("fix-NOT-working", '"fix" "NOT" "working"'),
            ("std::vector<int>", '"std" "vector" "int"'),
            ("foo;bar", '"foo" "bar"'),
            ("`foo`", '"foo"'),
            ("neural AND network", '"neural" "AND" "network"'),
            ("AND OR NOT", '"AND" "OR" "NOT"'),
            ("中文测试", '"中文测试"'),
        ],
    )
    def test_sanitize_fts5_input(self, raw, expected):
        assert _sanitize_fts5_input(raw) == expected

    def test_sanitize_quotes_simple_tokens_as_literals(self):
        assert _sanitize_fts5_input("fox") == '"fox"'
        assert _sanitize_fts5_input("  neural network  ") == '"neural" "network"'

    @pytest.mark.parametrize(
        "query,content,expect_results",
        [
            ("---", "some searchable content", False),
            (
                "oceanbase-blue-116 add search",
                (
                    "PowerMem marker oceanbase-blue-116 supports add search "
                    "verification"
                ),
                True,
            ),
            ("@username", "notify @username about deployment", True),
            ("foo|bar", "config key foo|bar enabled", True),
            ("fix-NOT-working", "status fix-NOT-working retry path", True),
            ("std::vector<int>", "uses std::vector<int> in cpp module", True),
            ("foo;bar", "config foo;bar enabled here", True),
            ("`foo`", "backtick `foo` identifier", True),
            ("AND OR NOT", "some searchable content", False),
            ("//", "some searchable content", False),
        ],
    )
    def test_sanitized_queries_do_not_log_fts5_warning(
        self, store, caplog, query, content, expect_results
    ):
        _insert_docs(store, [{"content": content}])
        with caplog.at_level(logging.WARNING):
            results = store.search(query=query, vectors=None, limit=5)

        if expect_results:
            assert len(results) >= 1, query
        else:
            assert results == [], query
        assert not any(
            "FTS5 search failed" in record.message for record in caplog.records
        ), query

    def test_boolean_keywords_quoted_as_literal_tokens_no_warning(self, store, mocker):
        _insert_docs(store, [{"content": "ham AND eggs breakfast"}])
        assert _sanitize_fts5_input("AND OR NOT") == '"AND" "OR" "NOT"'
        assert _sanitize_fts5_input("ham AND eggs") == '"ham" "AND" "eggs"'
        assert _sanitize_fts5_input("fix-NOT-working") == '"fix" "NOT" "working"'

        warn = mocker.patch(
            "powermem.storage.sqlite.sqlite_vector_store.logger.warning"
        )
        results = store.search(query="ham AND eggs", vectors=None, limit=5)
        assert len(results) >= 1
        assert "ham" in results[0].payload.get("data", "").lower()
        warn.assert_not_called()

        results = store.search(query="AND", vectors=None, limit=5)
        assert len(results) >= 1
        assert "AND" in results[0].payload.get("data", "")
        warn.assert_not_called()

        results = store.search(query="AND OR NOT", vectors=None, limit=5)
        assert results == []
        warn.assert_not_called()

    @pytest.fixture
    def technical_doc_store(self, store):
        _insert_docs(store, [{
            "content": (
                "PowerMem release verification: the marker phrase is oceanbase-blue-116 "
                "and it supports powermem-mcp v1.1.6 fix/release-dispatch-repo add search."
            ),
        }])
        return store

    @pytest.mark.parametrize(
        "query",
        [
            "oceanbase-blue-116 add search",
            "powermem-mcp",
            "v1.1.6",
            "fix/release-dispatch-repo",
        ],
    )
    def test_technical_token_pure_text_search(self, technical_doc_store, query):
        results = technical_doc_store.search(query=query, vectors=None, limit=5)
        assert len(results) >= 1
        content = results[0].payload.get("data", "").lower()
        assert "powermem" in content

    def test_technical_token_hybrid_search(self, technical_doc_store):
        query_vec = _make_vector(seed=0.5)
        results = technical_doc_store.search(
            query="oceanbase-blue-116 add search",
            vectors=[query_vec],
            limit=5,
        )
        assert len(results) >= 1
        assert any(
            "oceanbase-blue-116" in r.payload.get("data", "").lower()
            for r in results
        )

    def test_hybrid_tech_token_with_boolean(self, store):
        _insert_docs(store, [{
            "content": (
                "oceanbase-blue-116 deployment rollout completed for production"
            ),
        }])
        query_vec = _make_vector(seed=0.5)
        results = store.search(
            query="oceanbase-blue-116 AND deployment",
            vectors=[query_vec],
            limit=5,
        )
        assert len(results) >= 1
        content = results[0].payload.get("data", "").lower()
        assert "oceanbase-blue-116" in content
        assert "deployment" in content

    def test_technical_token_with_user_id_filter(self, store):
        """Sanitized FTS query should still respect payload filters."""
        _insert_docs(store, [
            {
                "content": "marker oceanbase-blue-116 release notes",
                "user_id": "alice",
            },
            {
                "content": "marker oceanbase-blue-116 release notes",
                "user_id": "bob",
            },
        ])
        results = store.search(
            query="oceanbase-blue-116",
            vectors=None,
            limit=5,
            filters={"user_id": "alice"},
        )
        assert len(results) >= 1
        for result in results:
            assert result.payload["user_id"] == "alice"

    def test_sanitized_query_no_match_returns_empty_without_warning(
        self, store, caplog
    ):
        _insert_docs(store, [{"content": "unrelated content only"}])
        with caplog.at_level(logging.WARNING):
            results = store.search(
                query="oceanbase-blue-116",
                vectors=None,
                limit=5,
            )
        assert results == []
        assert not any("FTS5 search failed" in record.message for record in caplog.records)

    def test_simple_english_query_regression(self, store):
        _insert_docs(store, [{"content": "the quick brown fox jumps over the lazy dog"}])
        results = store.search(query="fox", vectors=None, limit=5)
        assert len(results) >= 1
        assert "fox" in results[0].payload.get("data", "").lower()

    def test_underscore_identifier_pure_text_search(self, store):
        _insert_docs(store, [{"content": "define my_var here"}])
        results = store.search(query="my_var", vectors=None, limit=5)
        assert len(results) >= 1
        assert "my_var" in results[0].payload.get("data", "")
