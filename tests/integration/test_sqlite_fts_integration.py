"""L3 integration test: FTS5 hybrid search through the Memory API layer.

Validates that the FTS5 fulltext + hybrid RRF fusion works end-to-end
when accessed through the public Memory API (not just the raw
SQLiteVectorStore), with SQLite provider + mock embedder + mock LLM.

Written by the tester per coordinator assignment (brief.l3=required).
"""

import logging
import uuid
import pytest
from unittest.mock import MagicMock, patch

from powermem import Memory
from powermem.storage.sqlite.sqlite_vector_store import SQLiteVectorStore


def _make_sqlite_memory():
    """Create a Memory instance with SQLite storage, mock embedder, mock LLM."""
    config = {
        "vector_store": {
            "provider": "sqlite",
            "config": {
                "database_path": ":memory:",
                "collection_name": f"fts_integ_{uuid.uuid4().hex[:8]}",
            },
        },
        "llm": {
            "provider": "openai",
            "config": {"model": "gpt-4o-mini", "api_key": "mock-key"},
        },
        "embedder": {
            "provider": "mock",
            "config": {},
        },
    }

    patcher = patch("powermem.integrations.llm.factory.LLMFactory.create")
    mock_factory = patcher.start()
    mock_llm = MagicMock()
    mock_llm.generate_response.return_value = {"content": "Test memory"}
    mock_factory.return_value = mock_llm

    memory = Memory(config=config)
    return memory, patcher


class TestFTS5MemoryAPIIntegration:
    """Integration tests: FTS5 hybrid search through Memory.search()."""

    @pytest.fixture(autouse=True)
    def setup(self):
        self.memory, self._patcher = _make_sqlite_memory()
        yield
        self._patcher.stop()

    # -- helpers --
    def _add(self, text, user_id="integ_user"):
        return self.memory.add(text, user_id=user_id, infer=False)

    def _search(self, query, user_id="integ_user", limit=10):
        return self.memory.search(query, user_id=user_id, limit=limit)

    # ------------------------------------------------------------------ #
    # 1. Hybrid search returns results through Memory API
    # ------------------------------------------------------------------ #
    def test_search_returns_results(self):
        """Memory.search() should return results after adding memories."""
        self._add("the quick brown fox jumps over the lazy dog")
        self._add("machine learning and artificial intelligence")
        self._add("the fox and the hound play together")

        results = self._search("fox")
        assert results is not None
        assert "results" in results
        assert len(results["results"]) >= 1

    # ------------------------------------------------------------------ #
    # 2. FTS-matching docs rank higher than non-matching docs
    # ------------------------------------------------------------------ #
    def test_fts_matching_docs_rank_higher(self):
        """Docs containing the query text should rank above unrelated docs.

        The mock embedder returns identical vectors for all inputs, so vector
        scores are tied.  Any ranking differentiation must come from the FTS
        path -- proving that the hybrid pipeline is connected end-to-end.
        """
        self._add("database management systems overview")
        self._add("cooking Italian food recipes")
        self._add("database query optimization techniques")

        results = self._search("database")
        hits = results["results"]
        assert len(hits) >= 2, "Should return at least the two database docs"

        # The top-2 results should both contain "database"
        for r in hits[:2]:
            content = r.get("memory", "").lower()
            assert "database" in content, (
                f"Top result should contain 'database', got: {content}"
            )

    # ------------------------------------------------------------------ #
    # 3. Pure text search works (all vectors are identical anyway)
    # ------------------------------------------------------------------ #
    def test_search_finds_matching_text(self):
        """Search query should find docs with matching text content."""
        self._add("python programming language tutorial")
        self._add("java enterprise application server")

        results = self._search("python")
        hits = results["results"]
        assert len(hits) >= 1

        # At least one result should mention python
        found_python = any("python" in r.get("memory", "").lower() for r in hits)
        assert found_python, "Should find the 'python' memory"

    # ------------------------------------------------------------------ #
    # 4. Search with no matching text still returns vector results
    # ------------------------------------------------------------------ #
    def test_search_nonmatching_text_returns_vector_results(self):
        """When query text matches no FTS doc, vector results are still returned.

        Mock embedder produces identical vectors, so all docs are equidistant.
        """
        self._add("alpha beta gamma")
        self._add("delta epsilon zeta")

        results = self._search("xyznonsense")
        hits = results["results"]
        # Should still get results from vector path (all vectors are identical
        # so cosine sim = 1.0 for everything)
        assert len(hits) >= 1, "Vector results should still appear even when FTS has no match"

    # ------------------------------------------------------------------ #
    # 5. FTS table is populated after add (verify via raw store)
    # ------------------------------------------------------------------ #
    def test_fts_table_populated_after_memory_add(self):
        """After Memory.add(), the FTS5 virtual table should contain the text.

        Reaches into the raw SQLiteVectorStore to verify the FTS data pipeline.
        """
        self._add("unique searchable phrase xyz789")

        store = self.memory.storage.vector_store
        assert isinstance(store, SQLiteVectorStore)

        cursor = store.connection.execute(
            f"SELECT COUNT(*) FROM {store.collection_name}_fts"
        )
        count = cursor.fetchone()[0]
        assert count >= 1, "FTS table should have at least one row after add"

    # ------------------------------------------------------------------ #
    # 6. Delete removes from FTS (via Memory API)
    # ------------------------------------------------------------------ #
    def test_delete_removes_from_fts(self):
        """After Memory.delete(), the doc should not appear in FTS search."""
        add_result = self._add("unique deleteme content abc999")
        memory_id = None
        if isinstance(add_result, dict) and "results" in add_result:
            if add_result["results"]:
                memory_id = add_result["results"][0].get("id")

        if memory_id is None:
            pytest.skip("Could not extract memory_id from add result")

        # Verify it is found before delete
        before = self._search("abc999")
        assert len(before["results"]) >= 1, "Should find the doc before delete"

        # Delete via Memory API
        self.memory.delete(memory_id, user_id="integ_user")

        # Should not be found after delete
        after = self._search("abc999")
        found_ids = [r.get("id") for r in after["results"]]
        assert memory_id not in found_ids, "Deleted doc should not appear in search"

    # Technical tokens with hyphens/slashes (Fixes #1119)
    def test_search_technical_tokens(self, caplog):
        self._add(
            "PowerMem release verification: oceanbase-blue-116 powermem-mcp "
            "v1.1.6 fix/release-dispatch-repo fix-NOT-working std::vector<int> "
            "foo;bar `foo` neural AND network add search."
        )
        composite_queries = [
            "oceanbase-blue-116 add search",
            "powermem-mcp add search",
            "v1.1.6 add search",
            "fix/release-dispatch-repo add search",
        ]
        for query in composite_queries:
            with caplog.at_level(logging.WARNING):
                results = self._search(query)
                assert len(results["results"]) >= 1, query
                assert not any(
                    "FTS5 search failed" in record.message for record in caplog.records
                ), query

        isolated_cases = [
            ("marker oceanbase-blue-116 marker", "oceanbase-blue-116"),
            ("uses powermem-mcp module", "powermem-mcp"),
            ("release v1.1.6 shipped", "v1.1.6"),
            ("path fix/release-dispatch-repo here", "fix/release-dispatch-repo"),
            ("status fix-NOT-working retry", "fix-NOT-working"),
            ("uses std::vector<int> in cpp", "std::vector<int>"),
            ("config foo;bar enabled", "foo;bar"),
            ("backtick `foo` identifier", "`foo`"),
            ("neural AND network layer", "neural AND network"),
        ]
        for content, query in isolated_cases:
            self._add(content)
            with caplog.at_level(logging.WARNING):
                results = self._search(query)
                assert len(results["results"]) >= 1, query
                assert not any(
                    "FTS5 search failed" in record.message for record in caplog.records
                ), query

        with caplog.at_level(logging.WARNING):
            fts_store = self.memory.storage.vector_store
            assert isinstance(fts_store, SQLiteVectorStore)
            slash_results = fts_store.search(query="//", vectors=None, limit=5)
            assert slash_results == []
            assert not any(
                "FTS5 search failed" in record.message for record in caplog.records
            )

    # Update syncs FTS content (via Memory API)
    def test_update_syncs_fts(self):
        """After Memory.update(), FTS should find the new content, not the old."""
        add_result = self._add("original content phrase oldxyz")
        memory_id = None
        if isinstance(add_result, dict) and "results" in add_result:
            if add_result["results"]:
                memory_id = add_result["results"][0].get("id")

        if memory_id is None:
            pytest.skip("Could not extract memory_id from add result")

        # Update via Memory API
        self.memory.update(memory_id, "completely new updated text newxyz", user_id="integ_user")

        # New content should be findable
        after = self._search("newxyz")
        found_ids = [r.get("id") for r in after["results"]]
        assert memory_id in found_ids, "Updated content should appear in FTS search"

    # Filters work with hybrid search
    def test_filters_with_hybrid_search(self):
        """User filters should be respected in hybrid search results."""
        self._add("python data science tutorial", user_id="alice")
        self._add("python web development guide", user_id="bob")

        results = self.memory.search("python", user_id="alice", limit=10)
        hits = results["results"]
        # All results should belong to alice
        for r in hits:
            uid = r.get("user_id")
            if uid is not None:
                assert uid == "alice", f"Filter should restrict to alice, got {uid}"

    # Multiple adds + search returns correct ranking
    def test_multiple_adds_correct_ranking(self):
        """After multiple adds, FTS-matching doc should rank top in hybrid."""
        self._add("neural network deep learning transformer architecture")
        self._add("cooking dinner recipe pasta carbonara")
        self._add("gardening tips for spring planting flowers")

        results = self._search("neural network")
        hits = results["results"]
        assert len(hits) >= 1
        top = hits[0]
        assert "neural" in top.get("memory", "").lower(), (
            f"Top result should be the neural network doc, got: {top.get('memory', '')}"
        )
