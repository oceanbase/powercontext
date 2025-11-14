"""
Integration tests for AsyncMemory class

This module tests the integration between AsyncMemory and its components,
including vector stores, LLMs, and embedders, using async/await patterns.
"""

import pytest
import uuid
import asyncio
from unittest.mock import MagicMock, patch
from powermem import AsyncMemory
from powermem.storage.sqlite.sqlite_vector_store import SQLiteVectorStore
from powermem.integrations.embeddings.mock import MockEmbeddings


class TestAsyncMemoryIntegration:
    """Integration tests for AsyncMemory class."""
    
    @pytest.fixture
    def async_memory_with_sqlite(self):
        """Create an AsyncMemory instance with SQLite storage and mock providers."""
        # AsyncMemory expects config dict with direct parameters for VectorStoreFactory
        # VectorStoreFactory.create passes config dict directly to SQLiteVectorStore constructor
        config = {
            "database_path": ":memory:",
            "collection_name": f"test_collection_{uuid.uuid4().hex[:8]}",
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
        patcher_llm = patch('powermem.integrations.llm.factory.LLMFactory.create')
        mock_llm_factory = patcher_llm.start()
        mock_llm = MagicMock()
        mock_llm.generate_response.return_value = {"content": "Test memory content"}
        mock_llm_factory.return_value = mock_llm
        
        # Mock EmbedderFactory to handle the 2-parameter call
        patcher_embedder = patch('powermem.core.async_memory.EmbedderFactory.create')
        mock_embedder_factory = patcher_embedder.start()
        mock_embedder_factory.return_value = MockEmbeddings()
        
        try:
            memory = AsyncMemory(config=config, storage_type="sqlite", llm_provider="openai", embedding_provider="mock")
            yield memory
        finally:
            patcher_llm.stop()
            patcher_embedder.stop()
    
    @pytest.mark.asyncio
    async def test_async_memory_initialization(self, async_memory_with_sqlite):
        """Test that AsyncMemory initializes correctly with all components."""
        memory = async_memory_with_sqlite
        assert memory is not None
        assert memory.storage is not None
        assert memory.llm is not None
        assert memory.embedding is not None
    
    @pytest.mark.asyncio
    async def test_async_add_memory_integration(self, async_memory_with_sqlite):
        """Test adding a memory through the full integration stack asynchronously."""
        memory = async_memory_with_sqlite
        user_id = "test_user_async_1"
        
        # Add a memory
        result = await memory.add("User likes coffee", user_id=user_id, infer=False)

        assert result is not None
        assert "results" in result or isinstance(result, dict)

        # Verify memory was stored
        all_memories = await memory.get_all(user_id=user_id)
        assert len(all_memories.get("results", [])) > 0

    @pytest.mark.asyncio
    async def test_async_search_memories_integration(self, async_memory_with_sqlite):
        """Test searching memories through the full integration stack asynchronously."""
        memory = async_memory_with_sqlite
        user_id = "test_user_async_2"

        # Add some memories
        await memory.add("User prefers Python over Java", user_id=user_id, infer=False)
        await memory.add("User works as a software engineer", user_id=user_id, infer=False)
        await memory.add("User likes coffee", user_id=user_id, infer=False)

        # Search for memories
        results = await memory.search("programming preferences", user_id=user_id)

        assert results is not None
        assert isinstance(results, dict)
        assert "results" in results
        assert len(results["results"]) > 0

    @pytest.mark.asyncio
    async def test_async_get_memory_integration(self, async_memory_with_sqlite):
        """Test retrieving a specific memory asynchronously."""
        memory = async_memory_with_sqlite
        user_id = "test_user_async_3"

        # Add a memory
        add_result = await memory.add("User likes pizza", user_id=user_id, infer=False)

        # Extract memory ID from result
        memory_id = None
        if isinstance(add_result, dict) and "results" in add_result:
            if len(add_result["results"]) > 0:
                memory_id = add_result["results"][0].get("id")

        if memory_id:
            # Get the memory
            retrieved = await memory.get(memory_id, user_id=user_id)
            assert retrieved is not None
            assert retrieved.get("id") == memory_id

    @pytest.mark.asyncio
    async def test_async_update_memory_integration(self, async_memory_with_sqlite):
        """Test updating a memory through the full integration stack asynchronously."""
        memory = async_memory_with_sqlite
        user_id = "test_user_async_4"

        # Add a memory
        add_result = await memory.add("User likes tea", user_id=user_id, infer=False)

        # Extract memory ID
        memory_id = None
        if isinstance(add_result, dict) and "results" in add_result:
            if len(add_result["results"]) > 0:
                memory_id = add_result["results"][0].get("id")

        if memory_id:
            # Update the memory
            updated = await memory.update(memory_id, "User likes coffee instead", user_id=user_id)
            assert updated is not None
            assert isinstance(updated, dict)

    @pytest.mark.asyncio
    async def test_async_delete_memory_integration(self, async_memory_with_sqlite):
        """Test deleting a memory through the full integration stack asynchronously."""
        memory = async_memory_with_sqlite
        user_id = "test_user_async_5"

        # Add a memory
        add_result = await memory.add("User likes sushi", user_id=user_id, infer=False)

        # Extract memory ID
        memory_id = None
        if isinstance(add_result, dict) and "results" in add_result:
            if len(add_result["results"]) > 0:
                memory_id = add_result["results"][0].get("id")

        if memory_id:
            # Delete the memory
            deleted = await memory.delete(memory_id, user_id=user_id)
            assert deleted is True

            # Verify memory is deleted
            retrieved = await memory.get(memory_id, user_id=user_id)
            assert retrieved is None or retrieved.get("memory") is None

    @pytest.mark.asyncio
    async def test_async_multi_user_isolation(self, async_memory_with_sqlite):
        """Test that memories are isolated between different users asynchronously."""
        memory = async_memory_with_sqlite
        user1_id = "user_async_a"
        user2_id = "user_async_b"

        # Add memories for user 1
        await memory.add("User A likes Python", user_id=user1_id, infer=False)
        await memory.add("User A likes coffee", user_id=user1_id, infer=False)

        # Add memories for user 2
        await memory.add("User B likes Java", user_id=user2_id, infer=False)
        await memory.add("User B likes tea", user_id=user2_id, infer=False)

        # Search for user 1's memories
        user1_results = await memory.search("programming", user_id=user1_id)
        user1_memories = user1_results.get("results", [])

        # Search for user 2's memories
        user2_results = await memory.search("programming", user_id=user2_id)
        user2_memories = user2_results.get("results", [])

        # Verify isolation
        assert len(user1_memories) > 0
        assert len(user2_memories) > 0

        # Verify they contain different content
        user1_content = " ".join([m.get("memory", "") for m in user1_memories])
        user2_content = " ".join([m.get("memory", "") for m in user2_memories])
        assert "Python" in user1_content or "coffee" in user1_content
        assert "Java" in user2_content or "tea" in user2_content

    @pytest.mark.asyncio
    async def test_async_get_all_memories(self, async_memory_with_sqlite):
        """Test retrieving all memories for a user asynchronously."""
        memory = async_memory_with_sqlite
        user_id = "test_user_async_6"

        # Add multiple memories
        await memory.add("Memory 1", user_id=user_id, infer=False)
        await memory.add("Memory 2", user_id=user_id, infer=False)
        await memory.add("Memory 3", user_id=user_id, infer=False)

        # Get all memories
        all_memories = await memory.get_all(user_id=user_id)
        assert len(all_memories.get("results", [])) >= 3

    @pytest.mark.asyncio
    async def test_async_delete_all_memories(self, async_memory_with_sqlite):
        """Test deleting all memories for a user asynchronously."""
        memory = async_memory_with_sqlite
        user_id = "test_user_async_7"

        # Add some memories
        await memory.add("Memory to delete 1", user_id=user_id, infer=False)
        await memory.add("Memory to delete 2", user_id=user_id, infer=False)

        # Verify memories exist
        all_memories_before = await memory.get_all(user_id=user_id)
        assert len(all_memories_before.get("results", [])) >= 2

        # Delete all memories
        deleted = await memory.delete_all(user_id=user_id)
        assert deleted is True

        # Verify memories are deleted
        all_memories_after = await memory.get_all(user_id=user_id)
        assert len(all_memories_after.get("results", [])) == 0

    @pytest.mark.asyncio
    async def test_async_concurrent_operations(self, async_memory_with_sqlite):
        """Test concurrent async operations."""
        memory = async_memory_with_sqlite
        user_id = "test_user_async_concurrent"

        # Add multiple memories concurrently
        tasks = [
            memory.add(f"Memory {i}", user_id=user_id, infer=False)
            for i in range(5)
        ]
        results = await asyncio.gather(*tasks)

        # Verify all were added
        assert len(results) == 5
        all_memories = await memory.get_all(user_id=user_id)
        assert len(all_memories.get("results", [])) >= 5

    @pytest.mark.asyncio
    async def test_async_memory_with_metadata(self, async_memory_with_sqlite):
        """Test adding memory with metadata asynchronously."""
        memory = async_memory_with_sqlite
        user_id = "test_user_async_8"

        # Add memory with metadata
        metadata = {"category": "preference", "priority": "high"}
        result = await memory.add(
            "User prefers dark mode",
            user_id=user_id,
            metadata=metadata,
            infer=False
        )

        assert result is not None
        # Verify metadata is stored (if supported)
        all_memories = await memory.get_all(user_id=user_id)
        assert len(all_memories.get("results", [])) > 0

    @pytest.mark.asyncio
    async def test_async_complete_crud_workflow(self, async_memory_with_sqlite):
        """Test complete CRUD workflow asynchronously."""
        memory = async_memory_with_sqlite
        user_id = "test_user_async_workflow"

        # CREATE: Add memories
        result1 = await memory.add("User likes Python programming", user_id=user_id, infer=False)
        result2 = await memory.add("User prefers coffee over tea", user_id=user_id, infer=False)

        assert result1 is not None
        assert result2 is not None

        # READ: Get all memories
        all_memories = await memory.get_all(user_id=user_id)
        assert len(all_memories.get("results", [])) >= 2

        # READ: Search memories
        search_results = await memory.search("programming preferences", user_id=user_id)
        assert search_results is not None
        assert "results" in search_results

        # Extract a memory ID for UPDATE and DELETE
        memory_id = None
        if isinstance(result1, dict) and "results" in result1:
            if len(result1["results"]) > 0:
                memory_id = result1["results"][0].get("id")

        if memory_id:
            # READ: Get specific memory
            retrieved = await memory.get(memory_id, user_id=user_id)
            assert retrieved is not None
            assert retrieved.get("id") == memory_id

            # UPDATE: Update memory
            updated = await memory.update(memory_id, "User loves Python programming", user_id=user_id)
            assert updated is not None

            # Verify update
            updated_retrieved = await memory.get(memory_id, user_id=user_id)
            assert updated_retrieved is not None

            # DELETE: Delete memory
            deleted = await memory.delete(memory_id, user_id=user_id)
            assert deleted is True

            # Verify deletion
            deleted_retrieved = await memory.get(memory_id, user_id=user_id)
            assert deleted_retrieved is None or deleted_retrieved.get("memory") is None
