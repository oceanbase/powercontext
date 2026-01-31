"""
Tests for memory deduplication and compression functionality.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from powermem.core.memory import Memory


class TestMemoryDeduplication:
    """Test cases for memory deduplication functionality."""

    def test_deduplicate_empty_memories(self):
        """Test deduplication with no memories."""
        memory = Mock(spec=Memory)
        memory.get_all.return_value = {"results": []}
        
        # Directly call the method
        result = Memory.deduplicate(memory, user_id="user123")
        
        assert result["duplicates_found"] == 0
        assert result["memories_processed"] == 0

    def test_deduplicate_no_duplicates(self):
        """Test deduplication when no duplicates exist."""
        memory = Mock(spec=Memory)
        memory.get_all.return_value = {
            "results": [
                {"id": 1, "content": "Memory 1", "metadata": {}},
                {"id": 2, "content": "Memory 2", "metadata": {}},
            ]
        }
        memory.embedding.embed_batch.return_value = [[0.1, 0.9], [0.9, 0.1]]
        
        with patch.object(Memory, '_find_duplicate_groups', return_value=[]):
            result = Memory.deduplicate(memory, user_id="user123", threshold=0.95)
        
        assert result["duplicates_found"] == 0
        assert result["memories_processed"] == 2

    def test_deduplicate_with_duplicates(self):
        """Test deduplication with duplicate memories."""
        memory = Mock(spec=Memory)
        memory.get_all.return_value = {
            "results": [
                {"id": 1, "content": "Memory 1", "metadata": {"metadata": {}}},
                {"id": 2, "content": "Memory 1", "metadata": {"metadata": {}}},  # Duplicate
            ]
        }
        
        # Simulate finding duplicates
        with patch.object(Memory, '_find_duplicate_groups', return_value=[
            [
                {"id": 1, "content": "Memory 1", "metadata": {"metadata": {}}},
                {"id": 2, "content": "Memory 1", "metadata": {"metadata": {}}},
            ]
        ]):
            with patch.object(Memory, '_format_bytes', return_value="100 bytes"):
                result = Memory.deduplicate(memory, user_id="user123", threshold=0.95, dry_run=True)
        
        assert result["duplicates_found"] == 1
        assert result["merged"] == 0  # dry_run=True
        assert "dry run" in result["message"].lower()

    def test_deduplicate_dry_run(self):
        """Test deduplication in dry run mode."""
        memory = Mock(spec=Memory)
        memory.get_all.return_value = {
            "results": [
                {"id": 1, "content": "Test", "metadata": {"metadata": {}}},
            ]
        }
        
        result = Memory.deduplicate(memory, user_id="user123", dry_run=True)
        
        assert result["merged"] == 0
        assert result["deleted"] == 0

    def test_deduplicate_threshold_parameter(self):
        """Test deduplication with different thresholds."""
        memory = Mock(spec=Memory)
        memory.get_all.return_value = {"results": []}
        
        # Test with different thresholds
        for threshold in [0.80, 0.90, 0.95, 0.99]:
            result = Memory.deduplicate(memory, user_id="user123", threshold=threshold)
            assert result is not None


class TestMemoryCompression:
    """Test cases for memory compression functionality."""

    def test_compress_empty_memories(self):
        """Test compression with no memories."""
        memory = Mock(spec=Memory)
        memory.get_all.return_value = {"results": []}
        
        result = Memory.compress(memory, user_id="user123")
        
        assert result["memories_analyzed"] == 0
        assert result["compressed"] == 0

    def test_compress_strategy_parameter(self):
        """Test compression with different strategies."""
        memory = Mock(spec=Memory)
        memory.get_all.return_value = {"results": []}
        
        for strategy in ["conservative", "moderate", "aggressive"]:
            result = Memory.compress(memory, user_id="user123", strategy=strategy)
            assert result is not None

    def test_compress_invalid_strategy(self):
        """Test compression with invalid strategy."""
        memory = Mock(spec=Memory)
        memory.get_all.return_value = {"results": []}
        
        with pytest.raises(ValueError):
            Memory.compress(memory, user_id="user123", strategy="invalid")

    def test_compress_dry_run(self):
        """Test compression in dry run mode."""
        memory = Mock(spec=Memory)
        memory.get_all.return_value = {
            "results": [
                {"id": 1, "content": "Test", "metadata": {"metadata": {}}},
            ]
        }
        
        result = Memory.compress(memory, user_id="user123", dry_run=True)
        
        assert result["compressed"] == 0


class TestMemoryOptimization:
    """Test cases for memory optimization functionality."""

    def test_optimize_deduplicate_strategy(self):
        """Test optimization with deduplicate strategy."""
        memory = Mock(spec=Memory)
        memory.get_all.return_value = {"results": []}
        
        with patch.object(Memory, 'deduplicate', return_value={"merged": 0, "deleted": 0}):
            result = Memory.optimize(memory, user_id="user123", strategy="deduplicate")
        
        assert "merged" in result

    def test_optimize_compress_strategy(self):
        """Test optimization with compress strategy."""
        memory = Mock(spec=Memory)
        memory.get_all.return_value = {"results": []}
        
        with patch.object(Memory, 'compress', return_value={"compressed": 0}):
            result = Memory.optimize(memory, user_id="user123", strategy="compress")
        
        assert "compressed" in result

    def test_optimize_all_strategy(self):
        """Test optimization with all strategy."""
        memory = Mock(spec=Memory)
        memory.get_all.return_value = {"results": []}
        
        with patch.object(Memory, 'deduplicate', return_value={"merged": 0, "deleted": 0, "saved_space": "0 bytes"}):
            with patch.object(Memory, 'compress', return_value={"compressed": 0}):
                result = Memory.optimize(memory, user_id="user123", strategy="all")
        
        assert "deduplication" in result
        assert "compression" in result

    def test_optimize_invalid_strategy(self):
        """Test optimization with invalid strategy."""
        memory = Mock(spec=Memory)
        
        with pytest.raises(ValueError):
            Memory.optimize(memory, user_id="user123", strategy="invalid")


class TestEmbeddingSimilarity:
    """Test cases for embedding similarity calculation."""

    def test_identical_embeddings(self):
        """Test similarity of identical embeddings."""
        memory = Mock(spec=Memory)
        
        similarity = Memory._calculate_embedding_similarity(
            memory,
            [1.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
        )
        
        assert similarity == 1.0

    def test_opposite_embeddings(self):
        """Test similarity of opposite embeddings."""
        memory = Mock(spec=Memory)
        
        similarity = Memory._calculate_embedding_similarity(
            memory,
            [1.0, 0.0, 0.0],
            [-1.0, 0.0, 0.0],
        )
        
        assert similarity == 0.0

    def test_perpendicular_embeddings(self):
        """Test similarity of perpendicular embeddings."""
        memory = Mock(spec=Memory)
        
        similarity = Memory._calculate_embedding_similarity(
            memory,
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
        )
        
        assert similarity == 0.0

    def test_empty_embeddings(self):
        """Test similarity with empty embeddings."""
        memory = Mock(spec=Memory)
        
        similarity = Memory._calculate_embedding_similarity(memory, [], [])
        
        assert similarity == 0.0


class TestFormatBytes:
    """Test cases for byte formatting utility."""

    def test_format_bytes(self):
        """Test byte formatting."""
        memory = Mock(spec=Memory)
        
        assert Memory._format_bytes(memory, 0) == "0.00 B"
        assert Memory._format_bytes(memory, 1024) == "1.00 KB"
        assert Memory._format_bytes(memory, 1024 * 1024) == "1.00 MB"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
