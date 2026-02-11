"""
Test cases for list memories with sorting functionality

This module contains tests for the list memories API with sorting support.
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
from powermem import Memory
from powermem.storage.base import OutputData


class TestListMemoriesSorting:
    """Test cases for list memories with sorting."""
    
    @pytest.fixture
    def mock_memory(self):
        """Create a mock Memory instance."""
        with patch('powermem.core.memory.VectorStoreFactory'), \
             patch('powermem.core.memory.LLMFactory'), \
             patch('powermem.core.memory.EmbedderFactory'):
            memory = Memory()
            return memory
    
    def _create_output_data_list(self, memories_data, default_user_id="test_user"):
        """Helper to create OutputData list from memory dicts."""
        output_data_list = []
        for mem in memories_data:
            output_data = OutputData(
                id=mem["id"],
                score=1.0,
                payload={
                    "data": mem.get("memory", ""),
                    "created_at": mem.get("created_at"),
                    "updated_at": mem.get("updated_at"),
                    "user_id": mem.get("user_id", default_user_id),  # Default to test_user if not specified
                    "agent_id": mem.get("agent_id"),
                    "run_id": mem.get("run_id"),
                    "metadata": mem.get("metadata", {}),
                }
            )
            output_data_list.append(output_data)
        return output_data_list
    
    def _create_mock_list_with_sorting(self, output_data_list):
        """Create a mock list function that supports sorting and pagination."""
        def list_side_effect(filters=None, limit=None, offset=None, order_by=None, order="desc"):
            # Start with all data
            result = output_data_list[:]
            
            # Apply sorting if order_by is specified
            if order_by:
                # Extract sort key from payload or object attributes
                def get_sort_key(item):
                    # Special handling for 'id' field - it's on the object itself
                    if order_by == 'id':
                        value = item.id if hasattr(item, 'id') else item.get('id')
                    # For other fields, check payload first
                    elif hasattr(item, 'payload'):
                        value = item.payload.get(order_by)
                    else:
                        value = item.get(order_by)
                    
                    # Handle None values - put them at the end for both asc and desc
                    if value is None:
                        # Use a very large/small value to push None to the end
                        from datetime import datetime
                        if order == "desc":
                            return datetime.min  # None goes to end (smallest)
                        else:
                            return datetime.max  # None goes to end (largest)
                    
                    return value
                
                # Sort the results
                reverse = (order == "desc")
                try:
                    result = sorted(result, key=get_sort_key, reverse=reverse)
                except Exception as e:
                    # If sorting fails, return unsorted
                    print(f"Sorting failed: {e}")
                    pass
            
            # Apply pagination (offset and limit)
            if offset is not None:
                result = result[offset:]
            if limit is not None:
                result = result[:limit]
            
            return result
        
        return list_side_effect
    
    def test_get_all_with_sort_by_updated_at_desc(self, mock_memory):
        """Test get_all with sorting by updated_at in descending order."""
        # Create test data with different update times
        base_time = datetime.now()
        test_memories_data = [
            {
                "id": 1,
                "memory": "Memory 1",
                "updated_at": base_time - timedelta(days=3),
                "created_at": base_time - timedelta(days=5),
            },
            {
                "id": 2,
                "memory": "Memory 2",
                "updated_at": base_time - timedelta(days=1),  # Most recent
                "created_at": base_time - timedelta(days=4),
            },
            {
                "id": 3,
                "memory": "Memory 3",
                "updated_at": base_time - timedelta(days=2),
                "created_at": base_time - timedelta(days=3),
            },
        ]
        
        # Mock vector_store.list to return OutputData objects
        # Need to handle both with filters and without filters calls
        output_data_list = self._create_output_data_list(test_memories_data)
        mock_memory.storage.vector_store.list = MagicMock(
            side_effect=self._create_mock_list_with_sorting(output_data_list)
        )
        
        result = mock_memory.get_all(
            user_id="test_user",
            limit=10,
            sort_by="updated_at",
            order="desc"
        )
        
        assert "results" in result
        results = result["results"]
        
        # Verify results are sorted by updated_at in descending order
        # Memory 2 should be first (most recent update)
        assert len(results) == 3
        assert results[0]["id"] == 2  # Most recently updated
        assert results[1]["id"] == 3
        assert results[2]["id"] == 1  # Least recently updated
    
    def test_get_all_with_sort_by_updated_at_asc(self, mock_memory):
        """Test get_all with sorting by updated_at in ascending order."""
        base_time = datetime.now()
        test_memories_data = [
            {
                "id": 1,
                "memory": "Memory 1",
                "updated_at": base_time - timedelta(days=3),  # Oldest
                "created_at": base_time - timedelta(days=5),
            },
            {
                "id": 2,
                "memory": "Memory 2",
                "updated_at": base_time - timedelta(days=1),  # Most recent
                "created_at": base_time - timedelta(days=4),
            },
            {
                "id": 3,
                "memory": "Memory 3",
                "updated_at": base_time - timedelta(days=2),
                "created_at": base_time - timedelta(days=3),
            },
        ]
        
        output_data_list = self._create_output_data_list(test_memories_data)
        mock_memory.storage.vector_store.list = MagicMock(
            side_effect=self._create_mock_list_with_sorting(output_data_list)
        )
        
        result = mock_memory.get_all(
            user_id="test_user",
            limit=10,
            sort_by="updated_at",
            order="asc"
        )
        
        assert "results" in result
        results = result["results"]
        
        # Verify results are sorted by updated_at in ascending order
        # Memory 1 should be first (oldest update)
        assert len(results) == 3
        assert results[0]["id"] == 1  # Oldest update
        assert results[1]["id"] == 3
        assert results[2]["id"] == 2  # Most recent update
    
    def test_get_all_with_sort_by_created_at_desc(self, mock_memory):
        """Test get_all with sorting by created_at in descending order."""
        base_time = datetime.now()
        test_memories_data = [
            {
                "id": 1,
                "memory": "Memory 1",
                "created_at": base_time - timedelta(days=5),  # Oldest
                "updated_at": base_time - timedelta(days=1),
            },
            {
                "id": 2,
                "memory": "Memory 2",
                "created_at": base_time - timedelta(days=2),
                "updated_at": base_time - timedelta(days=1),
            },
            {
                "id": 3,
                "memory": "Memory 3",
                "created_at": base_time - timedelta(days=1),  # Most recent
                "updated_at": base_time - timedelta(days=1),
            },
        ]
        
        output_data_list = self._create_output_data_list(test_memories_data)
        mock_memory.storage.vector_store.list = MagicMock(
            side_effect=self._create_mock_list_with_sorting(output_data_list)
        )
        
        result = mock_memory.get_all(
            user_id="test_user",
            limit=10,
            sort_by="created_at",
            order="desc"
        )
        
        assert "results" in result
        results = result["results"]
        
        # Verify results are sorted by created_at in descending order
        assert len(results) == 3
        assert results[0]["id"] == 3  # Most recently created
        assert results[1]["id"] == 2
        assert results[2]["id"] == 1  # Oldest created
    
    def test_get_all_with_sort_by_id_desc(self, mock_memory):
        """Test get_all with sorting by id in descending order."""
        test_memories_data = [
            {"id": 1, "memory": "Memory 1", "created_at": None, "updated_at": None},
            {"id": 3, "memory": "Memory 3", "created_at": None, "updated_at": None},
            {"id": 2, "memory": "Memory 2", "created_at": None, "updated_at": None},
        ]
        
        output_data_list = self._create_output_data_list(test_memories_data)
        mock_memory.storage.vector_store.list = MagicMock(
            side_effect=self._create_mock_list_with_sorting(output_data_list)
        )
        
        result = mock_memory.get_all(
            user_id="test_user",
            limit=10,
            sort_by="id",
            order="desc"
        )
        
        assert "results" in result
        results = result["results"]
        
        # Verify results are sorted by id in descending order
        assert len(results) == 3
        assert results[0]["id"] == 3  # Highest ID
        assert results[1]["id"] == 2
        assert results[2]["id"] == 1  # Lowest ID
    
    def test_get_all_without_sorting(self, mock_memory):
        """Test get_all without sorting (should return original order)."""
        test_memories_data = [
            {"id": 1, "memory": "Memory 1", "created_at": None, "updated_at": None},
            {"id": 2, "memory": "Memory 2", "created_at": None, "updated_at": None},
            {"id": 3, "memory": "Memory 3", "created_at": None, "updated_at": None},
        ]
        
        output_data_list = self._create_output_data_list(test_memories_data)
        mock_memory.storage.vector_store.list = MagicMock(
            side_effect=self._create_mock_list_with_sorting(output_data_list)
        )
        
        result = mock_memory.get_all(
            user_id="test_user",
            limit=10,
            sort_by=None
        )
        
        assert "results" in result
        results = result["results"]
        
        # Results should be in original order (no sorting applied)
        assert len(results) == 3
    
    def test_get_all_with_filtering_and_sorting(self, mock_memory):
        """Test get_all with both filtering and sorting."""
        base_time = datetime.now()
        test_memories_data = [
            {
                "id": 1,
                "memory": "Memory 1",
                "user_id": "test_user",
                "updated_at": base_time - timedelta(days=3),
                "created_at": None,
            },
            {
                "id": 2,
                "memory": "Memory 2",
                "user_id": "test_user",
                "updated_at": base_time - timedelta(days=1),  # Most recent
                "created_at": None,
            },
            {
                "id": 3,
                "memory": "Memory 3",
                "user_id": "other_user",
                "updated_at": base_time - timedelta(days=2),
                "created_at": None,
            },
        ]
        
        output_data_list = self._create_output_data_list(test_memories_data)
        mock_memory.storage.vector_store.list = MagicMock(
            side_effect=self._create_mock_list_with_sorting(output_data_list)
        )
        
        result = mock_memory.get_all(
            user_id="test_user",
            agent_id=None,
            limit=10,
            sort_by="updated_at",
            order="desc"
        )
        
        assert "results" in result
        results = result["results"]
        
        # Verify filtering and sorting work together
        # Results should be sorted by updated_at desc
        # Note: Filtering happens at storage level, but we verify sorting is applied
        assert len(results) >= 2  # At least test_user memories
        # Verify sorting: most recent first
        if len(results) >= 2:
            assert results[0]["id"] == 2  # Most recently updated
    
    def test_get_all_with_pagination_and_sorting(self, mock_memory):
        """Test get_all with pagination and sorting."""
        # Create 5 test memories
        base_time = datetime.now()
        test_memories_data = [
            {
                "id": i,
                "memory": f"Memory {i}",
                "updated_at": base_time - timedelta(days=5-i),
                "created_at": None,
            }
            for i in range(1, 6)
        ]
        
        output_data_list = self._create_output_data_list(test_memories_data)
        mock_memory.storage.vector_store.list = MagicMock(
            side_effect=self._create_mock_list_with_sorting(output_data_list)
        )
        
        # Get first page
        result1 = mock_memory.get_all(
            user_id="test_user",
            limit=2,
            offset=0,
            sort_by="updated_at",
            order="desc"
        )
        
        assert "results" in result1
        results1 = result1["results"]
        assert len(results1) == 2
        
        # Get second page
        result2 = mock_memory.get_all(
            user_id="test_user",
            limit=2,
            offset=2,
            sort_by="updated_at",
            order="desc"
        )
        
        assert "results" in result2
        results2 = result2["results"]
        assert len(results2) == 2
        
        # Verify pagination works with sorting
        # Results should be consistent across pages
        assert results1[0]["id"] != results2[0]["id"]
        # First page should have most recent (id=5), second page should have older ones
        assert results1[0]["id"] == 5  # Most recent
        assert results2[0]["id"] == 3  # Third most recent


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

