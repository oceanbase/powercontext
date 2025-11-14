"""
End-to-end tests for powermem

This module tests complete user scenarios from initialization to complex workflows,
simulating real-world usage patterns.
"""

import pytest
import uuid
import asyncio
from unittest.mock import MagicMock, patch
from powermem import Memory, AsyncMemory


@pytest.mark.e2e
class TestEndToEndScenarios:
    """End-to-end tests for complete user scenarios."""

    @pytest.fixture
    def memory_instance(self):
        """Create a Memory instance for E2E testing."""
        config = {
            "vector_store": {
                "provider": "sqlite",
                "config": {
                    "database_path": ":memory:",
                    "collection_name": f"e2e_test_{uuid.uuid4().hex[:8]}"
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

        # Mock LLM to return structured responses
        patcher = patch('powermem.integrations.llm.factory.LLMFactory.create')
        mock_llm_factory = patcher.start()
        mock_llm = MagicMock()

        def generate_response_side_effect(messages, **kwargs):
            content = messages[-1]["content"] if messages else ""
            if "fact extraction" in content.lower() or "extract" in content.lower():
                return {"content": '{"facts": ["User likes Python", "User prefers coffee"]}'}
            elif "update" in content.lower() or "memory" in content.lower():
                return {"content": '{"memory": [{"id": "test", "action": "UPDATE"}]}'}
            else:
                return {"content": "Test memory content"}

        mock_llm.generate_response.side_effect = generate_response_side_effect
        mock_llm_factory.return_value = mock_llm

        try:
            memory = Memory(config=config)
            yield memory
        finally:
            patcher.stop()

    @pytest.fixture
    def async_memory_instance(self):
        """Create an AsyncMemory instance for E2E testing."""
        config = {
            "database_path": ":memory:",
            "collection_name": f"e2e_async_test_{uuid.uuid4().hex[:8]}",
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

        # Mock LLM and Embedder
        patcher_llm = patch('powermem.integrations.llm.factory.LLMFactory.create')
        patcher_embedder = patch('powermem.core.async_memory.EmbedderFactory.create')

        mock_llm_factory = patcher_llm.start()
        mock_llm = MagicMock()
        mock_llm.generate_response.return_value = {"content": "Test memory content"}
        mock_llm_factory.return_value = mock_llm

        mock_embedder_factory = patcher_embedder.start()
        from powermem.integrations.embeddings.mock import MockEmbeddings
        mock_embedder_factory.return_value = MockEmbeddings()

        try:
            memory = AsyncMemory(config=config, storage_type="sqlite", llm_provider="openai", embedding_provider="mock")
            yield memory
        finally:
            patcher_llm.stop()
            patcher_embedder.stop()

    def test_e2e_user_conversation_flow(self, memory_instance):
        """Test a complete user conversation flow."""
        memory = memory_instance
        user_id = "e2e_user_1"

        # Simulate a conversation where user shares information
        conversation = [
            "I'm a software engineer working at a tech company.",
            "I prefer Python over Java for backend development.",
            "I like drinking coffee in the morning.",
            "My favorite programming language is Python.",
        ]

        # Add conversation memories
        memory_ids = []
        for message in conversation:
            result = memory.add(message, user_id=user_id, infer=False)
            if isinstance(result, dict) and "results" in result:
                if len(result["results"]) > 0:
                    memory_ids.append(result["results"][0].get("id"))

        # Verify memories were stored
        all_memories = memory.get_all(user_id=user_id)
        assert len(all_memories.get("results", [])) >= len(conversation)

        # Search for relevant memories
        search_results = memory.search("programming preferences", user_id=user_id)
        assert search_results is not None
        assert "results" in search_results
        assert len(search_results["results"]) > 0

        # Verify search results contain relevant information
        results_content = " ".join([r.get("memory", "") for r in search_results["results"]])
        assert "Python" in results_content or "programming" in results_content.lower()

    def test_e2e_multi_user_isolation(self, memory_instance):
        """Test that multiple users have isolated memories."""
        memory = memory_instance

        # User 1 shares information
        user1_memories = [
            "I'm Alice, a data scientist.",
            "I use Python for data analysis.",
            "I prefer tea over coffee.",
        ]

        # User 2 shares information
        user2_memories = [
            "I'm Bob, a web developer.",
            "I use JavaScript for frontend development.",
            "I love coffee.",
        ]

        # Add memories for both users
        for msg in user1_memories:
            memory.add(msg, user_id="alice", infer=False)

        for msg in user2_memories:
            memory.add(msg, user_id="bob", infer=False)

        # Verify isolation
        alice_memories = memory.get_all(user_id="alice")
        bob_memories = memory.get_all(user_id="bob")

        assert len(alice_memories.get("results", [])) >= len(user1_memories)
        assert len(bob_memories.get("results", [])) >= len(user2_memories)

        # Search each user's memories
        alice_results = memory.search("programming", user_id="alice")
        bob_results = memory.search("programming", user_id="bob")

        # Verify they don't see each other's memories
        alice_content = " ".join([r.get("memory", "") for r in alice_results.get("results", [])])
        bob_content = " ".join([r.get("memory", "") for r in bob_results.get("results", [])])

        assert "Alice" in alice_content or "data scientist" in alice_content.lower()
        assert "Bob" in bob_content or "web developer" in bob_content.lower()

    def test_e2e_memory_update_lifecycle(self, memory_instance):
        """Test complete memory lifecycle: add, update, search, delete."""
        memory = memory_instance
        user_id = "e2e_lifecycle_user"

        # 1. Add initial memory
        add_result = memory.add("User likes Python programming", user_id=user_id, infer=False)
        memory_id = None
        if isinstance(add_result, dict) and "results" in add_result:
            if len(add_result["results"]) > 0:
                memory_id = add_result["results"][0].get("id")

        assert memory_id is not None

        # 2. Verify memory was added
        retrieved = memory.get(memory_id, user_id=user_id)
        assert retrieved is not None
        assert retrieved.get("id") == memory_id

        # 3. Update memory
        updated = memory.update(memory_id, "User loves Python programming and uses it daily", user_id=user_id)
        assert updated is not None

        # 4. Verify update
        updated_retrieved = memory.get(memory_id, user_id=user_id)
        assert updated_retrieved is not None

        # 5. Search for updated memory
        search_results = memory.search("Python programming", user_id=user_id)
        assert search_results is not None
        assert len(search_results.get("results", [])) > 0

        # 6. Delete memory
        deleted = memory.delete(memory_id, user_id=user_id)
        assert deleted is True

        # 7. Verify deletion
        deleted_retrieved = memory.get(memory_id, user_id=user_id)
        assert deleted_retrieved is None or deleted_retrieved.get("memory") is None

    def test_e2e_batch_operations(self, memory_instance):
        """Test batch operations: add multiple, get all, delete all."""
        memory = memory_instance
        user_id = "e2e_batch_user"

        # Batch add
        batch_memories = [
            f"Memory item {i}: User information {i}"
            for i in range(10)
        ]

        for mem in batch_memories:
            memory.add(mem, user_id=user_id, infer=False)

        # Verify batch add
        all_memories = memory.get_all(user_id=user_id)
        assert len(all_memories.get("results", [])) >= len(batch_memories)

        # Batch delete all
        deleted = memory.delete_all(user_id=user_id)
        assert deleted is True

        # Verify batch delete
        remaining = memory.get_all(user_id=user_id)
        assert len(remaining.get("results", [])) == 0

    def test_e2e_metadata_management(self, memory_instance):
        """Test memory with metadata management."""
        memory = memory_instance
        user_id = "e2e_metadata_user"

        # Add memory with metadata
        metadata = {
            "category": "preference",
            "priority": "high",
            "source": "user_input",
            "timestamp": "2024-01-01"
        }

        result = memory.add(
            "User prefers dark mode UI",
            user_id=user_id,
            metadata=metadata,
            infer=False
        )

        assert result is not None

        # Verify metadata is stored
        all_memories = memory.get_all(user_id=user_id)
        assert len(all_memories.get("results", [])) > 0

        # Search with metadata filters would be tested here if filters are supported
        search_results = memory.search("UI preferences", user_id=user_id)
        assert search_results is not None

    @pytest.mark.asyncio
    async def test_e2e_async_user_conversation_flow(self, async_memory_instance):
        """Test async version of user conversation flow."""
        memory = async_memory_instance
        user_id = "e2e_async_user"

        # Simulate async conversation
        conversation = [
            "I'm learning machine learning.",
            "I prefer Python for ML projects.",
            "I like working with TensorFlow.",
        ]

        # Add conversation memories asynchronously
        tasks = [memory.add(msg, user_id=user_id, infer=False) for msg in conversation]
        results = await asyncio.gather(*tasks)

        assert len(results) == len(conversation)

        # Verify memories
        all_memories = await memory.get_all(user_id=user_id)
        assert len(all_memories.get("results", [])) >= len(conversation)

        # Search asynchronously
        search_results = await memory.search("machine learning", user_id=user_id)
        assert search_results is not None
        assert "results" in search_results
        assert len(search_results["results"]) > 0

    @pytest.mark.asyncio
    async def test_e2e_async_complete_workflow(self, async_memory_instance):
        """Test complete async workflow."""
        memory = async_memory_instance
        user_id = "e2e_async_workflow"

        # CREATE
        result1 = await memory.add("User likes async programming", user_id=user_id, infer=False)
        result2 = await memory.add("User prefers async/await patterns", user_id=user_id, infer=False)

        assert result1 is not None
        assert result2 is not None

        # READ
        all_memories = await memory.get_all(user_id=user_id)
        assert len(all_memories.get("results", [])) >= 2

        # Search
        search_results = await memory.search("programming patterns", user_id=user_id)
        assert search_results is not None

        # Extract ID for UPDATE and DELETE
        memory_id = None
        if isinstance(result1, dict) and "results" in result1:
            if len(result1["results"]) > 0:
                memory_id = result1["results"][0].get("id")

        if memory_id:
            # UPDATE
            updated = await memory.update(memory_id, "User loves async programming", user_id=user_id)
            assert updated is not None

            # DELETE
            deleted = await memory.delete(memory_id, user_id=user_id)
            assert deleted is True

            # Verify deletion
            deleted_retrieved = await memory.get(memory_id, user_id=user_id)
            assert deleted_retrieved is None or deleted_retrieved.get("memory") is None

    def test_e2e_persistence_across_operations(self, memory_instance):
        """Test that memories persist across multiple operations."""
        memory = memory_instance
        user_id = "e2e_persistence_user"

        # Add memories in sequence
        memory.add("First memory: User likes Python", user_id=user_id, infer=False)
        memory.add("Second memory: User likes coffee", user_id=user_id, infer=False)

        # Verify first memory is still accessible
        all_memories_1 = memory.get_all(user_id=user_id)
        assert len(all_memories_1.get("results", [])) >= 2

        # Add more memories
        memory.add("Third memory: User works as engineer", user_id=user_id, infer=False)

        # Verify all memories persist
        all_memories_2 = memory.get_all(user_id=user_id)
        assert len(all_memories_2.get("results", [])) >= 3

        # Search should return all relevant memories
        search_results = memory.search("User", user_id=user_id)
        assert len(search_results.get("results", [])) >= 3

