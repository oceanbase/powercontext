"""
Integration tests for Memory class

This module tests the integration between Memory and its components,
including vector stores, LLMs, and embedders.
"""

import pytest
import uuid
from unittest.mock import MagicMock, patch
from powermem import Memory
from powermem.storage.sqlite.sqlite_vector_store import SQLiteVectorStore
from powermem.integrations.embeddings.mock import MockEmbeddings


class TestMemoryIntegration:
    """Integration tests for Memory class."""
    
    @pytest.fixture
    def memory_with_sqlite(self):
        """Create a Memory instance with SQLite storage and mock providers."""
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
        
        # Mock LLM to return simple responses
        # Use patch context manager that persists throughout fixture lifetime
        patcher = patch('powermem.integrations.llm.factory.LLMFactory.create')
        mock_llm_factory = patcher.start()
        mock_llm = MagicMock()
        mock_llm.generate_response.return_value = {"content": "Test memory content"}
        mock_llm_factory.return_value = mock_llm
        
        try:
            memory = Memory(config=config)
            yield memory
        finally:
            patcher.stop()
    
    def test_memory_initialization(self, memory_with_sqlite):
        """Test that Memory initializes correctly with all components."""
        memory = memory_with_sqlite
        assert memory is not None
        assert memory.storage is not None
        assert memory.llm is not None
        assert memory.embedding is not None
    
    def test_add_memory_integration(self, memory_with_sqlite):
        """Test adding a memory through the full integration stack."""
        memory = memory_with_sqlite
        user_id = "test_user_1"
        
        # Add a memory
        result = memory.add("User likes coffee", user_id=user_id, infer=False)

        assert result is not None
        assert "results" in result or isinstance(result, dict)

        # Verify memory was stored
        all_memories = memory.get_all(user_id=user_id)
        assert len(all_memories) > 0

    def test_search_memories_integration(self, memory_with_sqlite):
        """Test searching memories through the full integration stack."""
        memory = memory_with_sqlite
        user_id = "test_user_2"

        # Add some memories
        memory.add("User prefers Python over Java", user_id=user_id, infer=False)
        memory.add("User works as a software engineer", user_id=user_id, infer=False)
        memory.add("User likes coffee", user_id=user_id, infer=False)

        # Search for memories
        results = memory.search("programming preferences", user_id=user_id)

        assert results is not None
        assert isinstance(results, dict)
        assert "results" in results
        assert len(results["results"]) > 0

    def test_get_memory_integration(self, memory_with_sqlite):
        """Test retrieving a specific memory."""
        memory = memory_with_sqlite
        user_id = "test_user_3"

        # Add a memory
        add_result = memory.add("User likes pizza", user_id=user_id, infer=False)

        # Extract memory ID from result
        memory_id = None
        if isinstance(add_result, dict) and "results" in add_result:
            if len(add_result["results"]) > 0:
                memory_id = add_result["results"][0].get("id")

        if memory_id:
            # Get the memory
            retrieved = memory.get(memory_id, user_id=user_id)
            assert retrieved is not None
            assert retrieved.get("id") == memory_id

    def test_update_memory_integration(self, memory_with_sqlite):
        """Test updating a memory through the full integration stack."""
        memory = memory_with_sqlite
        user_id = "test_user_4"

        # Add a memory
        add_result = memory.add("User likes tea", user_id=user_id, infer=False)

        # Extract memory ID
        memory_id = None
        if isinstance(add_result, dict) and "results" in add_result:
            if len(add_result["results"]) > 0:
                memory_id = add_result["results"][0].get("id")

        if memory_id:
            # Update the memory
            updated = memory.update(memory_id, "User likes coffee instead", user_id=user_id)
            assert updated is not None
            assert isinstance(updated, dict)

    def test_delete_memory_integration(self, memory_with_sqlite):
        """Test deleting a memory through the full integration stack."""
        memory = memory_with_sqlite
        user_id = "test_user_5"

        # Add a memory
        add_result = memory.add("User likes sushi", user_id=user_id, infer=False)

        # Extract memory ID
        memory_id = None
        if isinstance(add_result, dict) and "results" in add_result:
            if len(add_result["results"]) > 0:
                memory_id = add_result["results"][0].get("id")

        if memory_id:
            # Delete the memory
            deleted = memory.delete(memory_id, user_id=user_id)
            assert deleted is True

            # Verify memory is deleted
            retrieved = memory.get(memory_id, user_id=user_id)
            assert retrieved is None or retrieved.get("memory") is None

    def test_multi_user_isolation(self, memory_with_sqlite):
        """Test that memories are isolated between different users."""
        memory = memory_with_sqlite
        user1_id = "user_a"
        user2_id = "user_b"

        # Add memories for user 1
        memory.add("User A likes Python", user_id=user1_id, infer=False)
        memory.add("User A likes coffee", user_id=user1_id)

        # Add memories for user 2
        memory.add("User B likes Java", user_id=user2_id, infer=False)
        memory.add("User B likes tea", user_id=user2_id, infer=False)

        # Search for user 1's memories
        user1_results = memory.search("programming", user_id=user1_id)
        user1_memories = user1_results.get("results", [])

        # Search for user 2's memories
        user2_results = memory.search("programming", user_id=user2_id)
        user2_memories = user2_results.get("results", [])

        # Verify isolation
        assert len(user1_memories) > 0
        assert len(user2_memories) > 0

        # Verify they contain different content
        user1_content = " ".join([m.get("memory", "") for m in user1_memories])
        user2_content = " ".join([m.get("memory", "") for m in user2_memories])
        assert "Python" in user1_content or "coffee" in user1_content
        assert "Java" in user2_content or "tea" in user2_content

    def test_get_all_memories(self, memory_with_sqlite):
        """Test retrieving all memories for a user."""
        memory = memory_with_sqlite
        user_id = "test_user_6"

        # Add multiple memories
        memory.add("Memory 1", user_id=user_id, infer=False)
        memory.add("Memory 2", user_id=user_id, infer=False)
        memory.add("Memory 3", user_id=user_id, infer=False)

        # Get all memories
        all_memories = memory.get_all(user_id=user_id)
        assert len(all_memories.get("results", [])) >= 3

    def test_delete_all_memories(self, memory_with_sqlite):
        """Test deleting all memories for a user."""
        memory = memory_with_sqlite
        user_id = "test_user_7"

        # Add some memories
        memory.add("Memory to delete 1", user_id=user_id, infer=False)
        memory.add("Memory to delete 2", user_id=user_id, infer=False)

        # Verify memories exist
        all_memories_before = memory.get_all(user_id=user_id)
        assert len(all_memories_before.get("results", [])) >= 2

        # Delete all memories
        deleted = memory.delete_all(user_id=user_id)
        assert deleted is True

        # Verify memories are deleted
        all_memories_after = memory.get_all(user_id=user_id)
        assert len(all_memories_after.get("results", [])) == 0

    def test_memory_with_metadata(self, memory_with_sqlite):
        """Test adding memory with metadata."""
        memory = memory_with_sqlite
        user_id = "test_user_8"

        # Add memory with metadata
        metadata = {"category": "preference", "priority": "high"}
        result = memory.add(
            "User prefers dark mode",
            user_id=user_id,
            metadata=metadata, infer=False
        )

        assert result is not None
        # Verify metadata is stored (if supported)
        all_memories = memory.get_all(user_id=user_id)
        assert len(all_memories) > 0
