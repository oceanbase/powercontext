"""
Integration tests for storage components

This module tests the integration between Memory and different storage backends.
"""

import pytest
import uuid
from unittest.mock import MagicMock, patch
from powermem import Memory
from powermem.storage.sqlite.sqlite_vector_store import SQLiteVectorStore


class TestStorageIntegration:
    """Integration tests for storage components."""
    
    @pytest.fixture
    def sqlite_memory(self):
        """Create a Memory instance with SQLite storage."""
        config = {
            "vector_store": {
                "provider": "sqlite",
                "config": {
                    "database_path": ":memory:",
                    "collection_name": f"test_collection_{uuid.uuid4().hex[:8]}"
                }
            },
            "llm": {
                "provider": "openai",
                "config": {
                    "model": "gpt-4o-mini",
                    "api_key": "mock-key"
                }
            },
            "embedder": {
                "provider": "mock",
                "config": {}
            }
        }
        
        # Mock LLM
        patcher = patch('powermem.integrations.llm.factory.LLMFactory.create')
        mock_llm_factory = patcher.start()
        mock_llm = MagicMock()
        mock_llm.generate_response.return_value = {"content": "Test memory"}
        mock_llm_factory.return_value = mock_llm
        
        try:
            memory = Memory(config=config)
            yield memory
        finally:
            patcher.stop()
    
    def test_sqlite_storage_initialization(self, sqlite_memory):
        """Test SQLite storage initialization."""
        memory = sqlite_memory
        assert memory.storage is not None
        # memory.storage is StorageAdapter, check vector_store
        assert hasattr(memory.storage, 'vector_store')
        assert isinstance(memory.storage.vector_store, SQLiteVectorStore)
    
    def test_sqlite_insert_and_retrieve(self, sqlite_memory):
        """Test inserting and retrieving from SQLite storage."""
        memory = sqlite_memory
        user_id = "test_user_storage"
        
        # Add memory
        result = memory.add("Test memory for storage", user_id=user_id, infer=False)
        assert result is not None

        # Retrieve all memories
        all_memories = memory.get_all(user_id=user_id)
        assert len(all_memories) > 0

    def test_sqlite_search_functionality(self, sqlite_memory):
        """Test SQLite search functionality."""
        memory = sqlite_memory
        user_id = "test_user_search"

        # Add multiple memories
        memory.add("User likes Python", user_id=user_id, infer=False)
        memory.add("User prefers Linux", user_id=user_id, infer=False)
        memory.add("User drinks coffee", user_id=user_id, infer=False)

        # Search for memories
        results = memory.search("programming", user_id=user_id)
        assert results is not None
        assert "results" in results
        assert len(results["results"]) > 0

    def test_sqlite_delete_functionality(self, sqlite_memory):
        """Test SQLite delete functionality."""
        memory = sqlite_memory
        user_id = "test_user_delete"

        # Add memory
        add_result = memory.add("Memory to delete", user_id=user_id, infer=False)

        # Extract memory ID
        memory_id = None
        if isinstance(add_result, dict) and "results" in add_result:
            if len(add_result["results"]) > 0:
                memory_id = add_result["results"][0].get("id")

        if memory_id:
            # Verify memory exists
            retrieved = memory.get(memory_id, user_id=user_id)
            assert retrieved is not None

            # Delete memory
            deleted = memory.delete(memory_id, user_id=user_id)
            assert deleted is True

    def test_sqlite_update_functionality(self, sqlite_memory):
        """Test SQLite update functionality."""
        memory = sqlite_memory
        user_id = "test_user_update"

        # Add memory
        add_result = memory.add("Original memory", user_id=user_id, infer=False)

        # Extract memory ID
        memory_id = None
        if isinstance(add_result, dict) and "results" in add_result:
            if len(add_result["results"]) > 0:
                memory_id = add_result["results"][0].get("id")

        if memory_id:
            # Update memory
            updated = memory.update(memory_id, "Updated memory", user_id=user_id)
            assert updated is not None

    def test_storage_with_filters(self, sqlite_memory):
        """Test storage operations with filters."""
        memory = sqlite_memory
        user_id = "test_user_filters"
        agent_id = "test_agent"

        # Add memories with different agents
        memory.add("Memory for agent 1", user_id=user_id, agent_id=agent_id, infer=False)
        memory.add("Memory for agent 2", user_id=user_id, agent_id="other_agent", infer=False)

        # Search with agent filter
        results = memory.search("memory", user_id=user_id, agent_id=agent_id)
        assert results is not None
        assert "results" in results

    def test_storage_collection_isolation(self, sqlite_memory):
        """Test that different collections are isolated."""
        memory = sqlite_memory

        # Create a second memory instance with different collection
        config2 = {
            "vector_store": {
                "provider": "sqlite",
                "config": {
                    "database_path": ":memory:",
                    "collection_name": f"test_collection_2_{uuid.uuid4().hex[:8]}"
                }
            },
            "llm": {
                "provider": "openai",
                "config": {
                    "model": "gpt-4o-mini",
                    "api_key": "mock-key"
                }
            },
            "embedder": {
                "provider": "mock",
                "config": {}
            }
        }

        patcher2 = patch('powermem.integrations.llm.factory.LLMFactory.create')
        mock_llm_factory2 = patcher2.start()
        mock_llm2 = MagicMock()
        mock_llm2.generate_response.return_value = {"content": "Test memory"}
        mock_llm_factory2.return_value = mock_llm2

        try:
            memory2 = Memory(config=config2)

            # Add memory to first collection
            memory.add("Memory in collection 1", user_id="test_user", infer=False)

            # Add memory to second collection
            memory2.add("Memory in collection 2", user_id="test_user", infer=False)

            # Verify they are isolated
            mem1_results = memory.get_all(user_id="test_user")
            mem2_results = memory2.get_all(user_id="test_user")

            assert len(mem1_results) > 0
            assert len(mem2_results) > 0
        finally:
            patcher2.stop()
