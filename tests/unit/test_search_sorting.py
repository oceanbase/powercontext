"""
Test for enhanced search result sorting functionality
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from datetime import datetime, timedelta
from powermem.core.memory import Memory


class TestSearchSorting:
    """Test cases for search result sorting functionality"""

    def test_sort_by_relevance(self):
        """Test sorting by relevance (default behavior)"""
        memory = Mock(spec=Memory)
        
        # Create test results with different quality scores
        test_results = [
            {"memory": "test1", "score": 0.5, "metadata": {"_quality_score": 0.5}},
            {"memory": "test2", "score": 0.9, "metadata": {"_quality_score": 0.9}},
            {"memory": "test3", "score": 0.3, "metadata": {"_quality_score": 0.3}},
        ]
        
        # Sort by relevance (should be descending)
        sorted_results = memory._sort_search_results(test_results, "relevance")
        
        # Check that results are sorted by quality score descending
        assert sorted_results[0]["metadata"]["_quality_score"] == 0.9
        assert sorted_results[1]["metadata"]["_quality_score"] == 0.5
        assert sorted_results[2]["metadata"]["_quality_score"] == 0.3
    
    def test_sort_by_date_desc(self):
        """Test sorting by date descending (newest first)"""
        memory = Mock(spec=Memory)
        
        # Create test results with different dates
        test_results = [
            {"memory": "oldest", "created_at": "2024-01-01T10:00:00", "metadata": {}},
            {"memory": "newest", "created_at": "2024-03-01T10:00:00", "metadata": {}},
            {"memory": "middle", "created_at": "2024-02-01T10:00:00", "metadata": {}},
        ]
        
        sorted_results = memory._sort_search_results(test_results, "date_desc")
        
        # Check that newest is first
        assert sorted_results[0]["memory"] == "newest"
        assert sorted_results[1]["memory"] == "middle"
        assert sorted_results[2]["memory"] == "oldest"
    
    def test_sort_by_date_asc(self):
        """Test sorting by date ascending (oldest first)"""
        memory = Mock(spec=Memory)
        
        test_results = [
            {"memory": "oldest", "created_at": "2024-01-01T10:00:00", "metadata": {}},
            {"memory": "newest", "created_at": "2024-03-01T10:00:00", "metadata": {}},
            {"memory": "middle", "created_at": "2024-02-01T10:00:00", "metadata": {}},
        ]
        
        sorted_results = memory._sort_search_results(test_results, "date_asc")
        
        # Check that oldest is first
        assert sorted_results[0]["memory"] == "oldest"
        assert sorted_results[1]["memory"] == "middle"
        assert sorted_results[2]["memory"] == "newest"
    
    def test_sort_by_importance_desc(self):
        """Test sorting by importance score descending"""
        memory = Mock(spec=Memory)
        
        test_results = [
            {"memory": "low", "metadata": {"metadata": {"importance_score": 0.2}}},
            {"memory": "high", "metadata": {"metadata": {"importance_score": 0.9}}},
            {"memory": "medium", "metadata": {"metadata": {"importance_score": 0.5}}},
        ]
        
        sorted_results = memory._sort_search_results(test_results, "importance_desc")
        
        # Check that highest importance is first
        assert sorted_results[0]["memory"] == "high"
        assert sorted_results[1]["memory"] == "medium"
        assert sorted_results[2]["memory"] == "low"
    
    def test_sort_by_access_count_desc(self):
        """Test sorting by access count descending"""
        memory = Mock(spec=Memory)
        
        test_results = [
            {"memory": "low", "metadata": {"metadata": {"access_count": 5}}},
            {"memory": "high", "metadata": {"metadata": {"access_count": 100}}},
            {"memory": "medium", "metadata": {"metadata": {"access_count": 25}}},
        ]
        
        sorted_results = memory._sort_search_results(test_results, "access_count_desc")
        
        # Check that highest access count is first
        assert sorted_results[0]["memory"] == "high"
        assert sorted_results[1]["memory"] == "medium"
        assert sorted_results[2]["memory"] == "low"
    
    def test_sort_by_retention_desc(self):
        """Test sorting by retention score descending"""
        memory = Mock(spec=Memory)
        
        test_results = [
            {"memory": "low", "metadata": {"metadata": {"retention_score": 0.3}}},
            {"memory": "high", "metadata": {"metadata": {"retention_score": 0.95}}},
            {"memory": "medium", "metadata": {"metadata": {"retention_score": 0.6}}},
        ]
        
        sorted_results = memory._sort_search_results(test_results, "retention_desc")
        
        # Check that highest retention is first
        assert sorted_results[0]["memory"] == "high"
        assert sorted_results[1]["memory"] == "medium"
        assert sorted_results[2]["memory"] == "low"
    
    def test_sort_multi_criteria(self):
        """Test multi-criteria sorting (relevance first, then date)"""
        memory = Mock(spec=Memory)
        
        # Results with same relevance but different dates
        test_results = [
            {"memory": "old_high", "score": 0.9, "created_at": "2024-01-01", "metadata": {"_quality_score": 0.9}},
            {"memory": "new_high", "score": 0.9, "created_at": "2024-03-01", "metadata": {"_quality_score": 0.9}},
            {"memory": "low", "score": 0.5, "created_at": "2024-02-01", "metadata": {"_quality_score": 0.5}},
        ]
        
        sorted_results = memory._sort_search_results(test_results, ["relevance", "date_desc"])
        
        # Check that high relevance items come first, then sorted by date
        assert sorted_results[0]["score"] == 0.9
        assert sorted_results[1]["score"] == 0.9
        assert sorted_results[2]["score"] == 0.5
        
        # Among same relevance, newer date should come first
        assert sorted_results[0]["memory"] == "new_high"
        assert sorted_results[1]["memory"] == "old_high"
    
    def test_sort_with_missing_metadata(self):
        """Test sorting handles missing metadata gracefully"""
        memory = Mock(spec=Memory)
        
        test_results = [
            {"memory": "test1", "metadata": {}},  # Missing quality_score
            {"memory": "test2", "metadata": {"_quality_score": 0.8}},
            {"memory": "test3"},  # Missing metadata entirely
        ]
        
        # Should not raise error
        sorted_results = memory._sort_search_results(test_results, "relevance")
        
        assert len(sorted_results) == 3
    
    def test_sort_unknown_criterion(self):
        """Test sorting with unknown criterion falls back to relevance"""
        memory = Mock(spec=Memory)
        
        test_results = [
            {"memory": "test1", "score": 0.5, "metadata": {"_quality_score": 0.5}},
            {"memory": "test2", "score": 0.9, "metadata": {"_quality_score": 0.9}},
        ]
        
        sorted_results = memory._sort_search_results(test_results, "unknown_criterion")
        
        # Should fall back to relevance sorting
        assert sorted_results[0]["metadata"]["_quality_score"] == 0.9
        assert sorted_results[1]["metadata"]["_quality_score"] == 0.5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
