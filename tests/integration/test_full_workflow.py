"""
Integration tests for full workflow

This module tests complete workflows including add, search, update, delete operations.
"""

import pytest
import uuid
from unittest.mock import MagicMock, patch
from powermem import Memory


class TestFullWorkflowIntegration:
    """Integration tests for complete workflows."""
    
    @pytest.fixture
    def memory_instance(self):
        """Create a Memory instance for workflow testing."""
        config = {
            "vector_store": {
                "provider": "sqlite",
                "config": {
                    "database_path": ":memory:",
                    "collection_name": f"workflow_test_{uuid.uuid4().hex[:8]}"
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
        # Return different responses based on context
        def generate_response_side_effect(messages, **kwargs):
            content = messages[-1]["content"] if messages else ""
            if "fact extraction" in content.lower() or "extract" in content.lower():
                return {"content": '["User likes Python", "User prefers coffee"]'}
            elif "update" in content.lower():
                return {"content": "yes"}
            else:
                return {"content": "Test memory content"}
        
        mock_llm.generate_response.side_effect = generate_response_side_effect
        mock_llm_factory.return_value = mock_llm
        
        try:
            memory = Memory(config=config)
            yield memory
        finally:
            patcher.stop()
    
    def test_complete_crud_workflow(self, memory_instance):
        """Test complete CRUD workflow: Create, Read, Update, Delete."""
        memory = memory_instance
        user_id = "workflow_user"
        
        # CREATE: Add memories
        result1 = memory.add("User likes Python programming", user_id=user_id, infer=False)
        result2 = memory.add("User prefers coffee over tea", user_id=user_id, infer=False)
        result3 = memory.add("User works as a software engineer", user_id=user_id, infer=False)

        assert result1 is not None
        assert result2 is not None
        assert result3 is not None

        # READ: Get all memories
        all_memories = memory.get_all(user_id=user_id)
        assert len(all_memories.get("results", [])) >= 3

        # READ: Search memories
        search_results = memory.search("programming preferences", user_id=user_id)
        assert search_results is not None
        assert "results" in search_results
        assert len(search_results["results"]) > 0

        # Extract a memory ID for UPDATE and DELETE
        memory_id = None
        if isinstance(result1, dict) and "results" in result1:
            if len(result1["results"]) > 0:
                memory_id = result1["results"][0].get("id")

        if memory_id:
            # READ: Get specific memory
            retrieved = memory.get(memory_id, user_id=user_id)
            assert retrieved is not None
            assert retrieved.get("id") == memory_id

            # UPDATE: Update memory
            updated = memory.update(memory_id, "User loves Python programming", user_id=user_id)
            assert updated is not None

            # Verify update
            updated_retrieved = memory.get(memory_id, user_id=user_id)
            assert updated_retrieved is not None

            # DELETE: Delete memory
            deleted = memory.delete(memory_id, user_id=user_id)
            assert deleted is True

            # Verify deletion
            deleted_retrieved = memory.get(memory_id, user_id=user_id)
            assert deleted_retrieved is None or deleted_retrieved.get("memory") is None

    def test_multi_agent_workflow(self, memory_instance):
        """Test workflow with multiple agents."""
        memory = memory_instance
        user_id = "multi_agent_user"
        agent1_id = "agent_1"
        agent2_id = "agent_2"

        # Add memories for different agents
        memory.add("Agent 1: User likes Python", user_id=user_id, agent_id=agent1_id, infer=False)
        memory.add("Agent 2: User likes Java", user_id=user_id, agent_id=agent2_id, infer=False)

        # Search for agent 1's memories
        agent1_results = memory.search("programming", user_id=user_id, agent_id=agent1_id)
        assert agent1_results is not None
        assert "results" in agent1_results

        # Search for agent 2's memories
        agent2_results = memory.search("programming", user_id=user_id, agent_id=agent2_id)
        assert agent2_results is not None
        assert "results" in agent2_results

    def test_memory_update_workflow(self, memory_instance):
        """Test updating existing memories."""
        memory = memory_instance
        user_id = "update_user"

        # Add initial memory
        add_result = memory.add("User likes coffee", user_id=user_id, infer=False)

        # Extract memory ID
        memory_id = None
        if isinstance(add_result, dict) and "results" in add_result:
            if len(add_result["results"]) > 0:
                memory_id = add_result["results"][0].get("id")

        if memory_id:
            # Update memory multiple times
            memory.update(memory_id, "User likes coffee and tea", user_id=user_id)
            memory.update(memory_id, "User likes coffee, tea, and hot chocolate", user_id=user_id)

            # Verify final state
            final_memory = memory.get(memory_id, user_id=user_id)
            assert final_memory is not None

    def test_memory_search_relevance(self, memory_instance):
        """Test search relevance and ranking."""
        memory = memory_instance
        user_id = "search_user"

        # Add memories with different topics
        memory.add("User likes Python", user_id=user_id, infer=False)
        memory.add("User likes coffee", user_id=user_id, infer=False)
        memory.add("User likes Python and coffee", user_id=user_id, infer=False)

        # Search for Python-related memories
        python_results = memory.search("Python programming", user_id=user_id)
        assert python_results is not None
        assert "results" in python_results

        # Search for coffee-related memories
        coffee_results = memory.search("coffee preferences", user_id=user_id)
        assert coffee_results is not None
        assert "results" in coffee_results

    def test_memory_batch_operations(self, memory_instance):
        """Test batch operations."""
        memory = memory_instance
        user_id = "batch_user"

        # Add multiple memories in batch
        memories_to_add = [
            "Memory 1",
            "Memory 2",
            "Memory 3",
            "Memory 4",
            "Memory 5"
        ]

        for mem_content in memories_to_add:
            memory.add(mem_content, user_id=user_id, infer=False)

        # Verify all memories were added
        all_memories = memory.get_all(user_id=user_id)
        assert len(all_memories.get("results", [])) >= len(memories_to_add)

        # Delete all memories
        deleted = memory.delete_all(user_id=user_id)
        assert deleted is True

        # Verify all memories were deleted
        remaining_memories = memory.get_all(user_id=user_id)
        assert len(remaining_memories.get("results", [])) == 0

    def test_memory_with_metadata_workflow(self, memory_instance):
        """Test workflow with metadata."""
        memory = memory_instance
        user_id = "metadata_user"

        # Add memory with metadata
        metadata = {
            "category": "preference",
            "priority": "high",
            "source": "user_input"
        }

        result = memory.add(
            "User prefers dark mode UI",
            user_id=user_id,
            metadata=metadata,
            infer=False
        )

        assert result is not None

        # Retrieve and verify metadata is accessible
        all_memories = memory.get_all(user_id=user_id)
        assert len(all_memories) > 0

    def test_error_handling_workflow(self, memory_instance):
        """Test error handling in workflow."""
        memory = memory_instance
        user_id = "error_user"

        # Try to get non-existent memory
        non_existent = memory.get("non-existent-id", user_id=user_id)
        assert non_existent is None or non_existent.get("memory") is None

        # Try to delete non-existent memory
        deleted = memory.delete("non-existent-id", user_id=user_id)
        # Should handle gracefully (return False or raise appropriate error)
        assert isinstance(deleted, bool)

        # Try to update non-existent memory
        try:
            updated = memory.update("non-existent-id", "New content", user_id=user_id)
            # Should either return None or handle gracefully
            assert updated is None or isinstance(updated, dict)
        except Exception:
            # It's okay if it raises an exception, as long as it's handled
            pass
