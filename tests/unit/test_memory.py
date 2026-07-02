"""
Basic tests for powermem

This module contains basic unit tests for the memory system.
"""

import pytest
from unittest.mock import MagicMock, patch, Mock
from powermem import Memory
from powermem.core.async_memory import AsyncMemory
from powermem.core.base import MemoryBase
from powermem.core.memory import _HTTPMemoryClient


class TestMemory:
    """Test cases for Memory class."""
    
    @patch('powermem.core.memory.VectorStoreFactory')
    @patch('powermem.core.memory.LLMFactory')
    @patch('powermem.core.memory.EmbedderFactory')
    def test_memory_initialization(self, mock_embedder_factory, mock_llm_factory, mock_vector_factory):
        """Test memory initialization."""
        # Mock the factories
        mock_vector_store = MagicMock()
        mock_vector_factory.create.return_value = mock_vector_store
        
        mock_llm = MagicMock()
        mock_llm_factory.create.return_value = mock_llm
        
        mock_embedder = MagicMock()
        mock_embedder_factory.create.return_value = mock_embedder
        
        memory = Memory()
        assert isinstance(memory, MemoryBase)
    
    @patch('powermem.core.memory.VectorStoreFactory')
    @patch('powermem.core.memory.LLMFactory')
    @patch('powermem.core.memory.EmbedderFactory')
    def test_add_memory(self, mock_embedder_factory, mock_llm_factory, mock_vector_factory):
        """Test adding a memory."""
        # Mock the factories
        mock_vector_store = MagicMock()
        mock_vector_store.insert.return_value = ["test_id_1"]
        mock_vector_store.search.return_value = []
        mock_vector_factory.create.return_value = mock_vector_store
        
        mock_llm = MagicMock()
        mock_llm.generate_response.return_value = {"content": "Test memory content"}
        mock_llm_factory.create.return_value = mock_llm
        
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1, 0.2, 0.3]
        mock_embedder_factory.create.return_value = mock_embedder
        
        memory = Memory()
        
        # Mock the add method's internal behavior
        with patch.object(memory, '_extract_facts', return_value=["Test memory content"]):
            result = memory.add("Test memory content", user_id="test_user")
        
        assert "results" in result or isinstance(result, dict)
    
    @patch('powermem.core.memory.VectorStoreFactory')
    @patch('powermem.core.memory.LLMFactory')
    @patch('powermem.core.memory.EmbedderFactory')
    @patch('powermem.core.memory.IntelligenceManager')
    def test_search_memories(self, mock_intelligence_manager, mock_embedder_factory, mock_llm_factory, mock_vector_factory):
        """Test searching memories."""
        # Mock the factories
        mock_vector_store = MagicMock()
        mock_vector_factory.create.return_value = mock_vector_store
        
        mock_llm = MagicMock()
        mock_llm_factory.create.return_value = mock_llm
        
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1, 0.2, 0.3]
        mock_embedder_factory.create.return_value = mock_embedder
        
        # Mock IntelligenceManager
        mock_intelligence = MagicMock()
        mock_intelligence_plugin = MagicMock()
        mock_intelligence_plugin.on_search.return_value = ([], [])
        mock_intelligence.plugin = mock_intelligence_plugin
        mock_intelligence_manager.return_value = mock_intelligence
        
        memory = Memory()
        
        # Mock storage.search_memories to return proper format
        with patch.object(memory.storage, 'search_memories', return_value={"results": []}):
            # Search for memories
            results = memory.search("user preferences", user_id="test_user")
            
            assert isinstance(results, dict)
            assert "results" in results

    @patch('powermem.core.memory.VectorStoreFactory')
    @patch('powermem.core.memory.LLMFactory')
    @patch('powermem.core.memory.EmbedderFactory')
    @patch('powermem.core.memory.IntelligenceManager')
    def test_search_falls_back_to_fts_when_embedding_fails(
        self,
        mock_intelligence_manager,
        mock_embedder_factory,
        mock_llm_factory,
        mock_vector_factory,
    ):
        """Embedding failure should not force Memory.search() to return empty."""
        mock_vector_store = MagicMock()
        mock_vector_factory.create.return_value = mock_vector_store

        mock_llm = MagicMock()
        mock_llm_factory.create.return_value = mock_llm

        mock_embedder = MagicMock()
        mock_embedder.embed.side_effect = RuntimeError("embedder offline")
        mock_embedder_factory.create.return_value = mock_embedder

        mock_intelligence = MagicMock()
        mock_intelligence.enabled = False
        mock_intelligence.plugin = None
        mock_intelligence_manager.return_value = mock_intelligence

        memory = Memory()
        storage_results = [
            {
                "id": 1,
                "memory": "offline fallback result",
                "metadata": {},
                "score": 0.2,
                "user_id": "test_user",
            }
        ]

        with patch.object(
            memory.storage, 'search_memories', return_value=storage_results
        ) as mock_search:
            results = memory.search("offline fallback", user_id="test_user")

        assert results["results"][0]["memory"] == "offline fallback result"
        call_kwargs = mock_search.call_args.kwargs
        assert call_kwargs["query_embedding"] is None
        assert call_kwargs["query"] == "offline fallback"
        assert call_kwargs["retrieval_mode"] == "auto"

    def test_memory_search_passes_retrieval_parameters_to_http_client(self):
        """HTTP-backed SDK search should forward retrieval controls."""
        memory = Memory.__new__(Memory)
        fake_http_client = MagicMock()
        fake_http_client.search.return_value = {"results": [], "relations": []}
        memory._http_client = fake_http_client
        memory.agent_id = None

        memory.search(
            "coffee",
            retrieval_mode="fts",
            fusion="weighted",
            vector_weight=0.2,
            fts_weight=0.8,
            rrf_k=25,
            candidate_limit=40,
            include_explanation=True,
            threshold=0.3,
        )

        call_kwargs = fake_http_client.search.call_args.kwargs
        assert call_kwargs["retrieval_mode"] == "fts"
        assert call_kwargs["fusion"] == "weighted"
        assert call_kwargs["vector_weight"] == 0.2
        assert call_kwargs["fts_weight"] == 0.8
        assert call_kwargs["rrf_k"] == 25
        assert call_kwargs["candidate_limit"] == 40
        assert call_kwargs["include_explanation"] is True
        assert call_kwargs["threshold"] == 0.3

    def test_http_memory_client_search_sends_retrieval_parameters(self, monkeypatch):
        """HTTP client payload should include retrieval controls accepted by API."""
        captured = {}

        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"success": True, "data": {"results": []}}

        def fake_request(method, url, **kwargs):
            captured["method"] = method
            captured["url"] = url
            captured["json"] = kwargs["json"]
            return FakeResponse()

        monkeypatch.setattr("httpx.request", fake_request)

        client = _HTTPMemoryClient("http://example.test")
        client.search(
            "coffee",
            retrieval_mode="fts",
            fusion="weighted",
            vector_weight=0.2,
            fts_weight=0.8,
            rrf_k=25,
            candidate_limit=40,
            include_explanation=True,
            threshold=0.3,
        )

        assert captured["method"] == "POST"
        assert captured["url"] == "http://example.test/api/v1/memories/search"
        payload = captured["json"]
        assert payload["retrieval_mode"] == "fts"
        assert payload["fusion"] == "weighted"
        assert payload["vector_weight"] == 0.2
        assert payload["fts_weight"] == 0.8
        assert payload["rrf_k"] == 25
        assert payload["candidate_limit"] == 40
        assert payload["include_explanation"] is True
        assert payload["threshold"] == 0.3

    @pytest.mark.asyncio
    @patch('powermem.core.async_memory.VectorStoreFactory')
    @patch('powermem.core.async_memory.LLMFactory')
    @patch('powermem.core.async_memory.EmbedderFactory')
    @patch('powermem.core.async_memory.IntelligenceManager')
    async def test_async_search_falls_back_to_fts_when_embedding_fails(
        self,
        mock_intelligence_manager,
        mock_embedder_factory,
        mock_llm_factory,
        mock_vector_factory,
    ):
        """AsyncMemory.search should mirror sync embedding fallback behavior."""
        mock_vector_store = MagicMock()
        mock_vector_factory.create.return_value = mock_vector_store

        mock_llm = MagicMock()
        mock_llm_factory.create.return_value = mock_llm

        mock_embedder = MagicMock()
        mock_embedder.embed.side_effect = RuntimeError("embedder offline")
        mock_embedder_factory.create.return_value = mock_embedder

        mock_intelligence = MagicMock()
        mock_intelligence.enabled = False
        mock_intelligence.plugin = None
        mock_intelligence_manager.return_value = mock_intelligence

        memory = AsyncMemory()
        storage_results = [
            {
                "id": 1,
                "memory": "async offline fallback result",
                "metadata": {},
                "score": 0.2,
                "user_id": "test_user",
            }
        ]

        with patch.object(
            memory.storage, 'search_memories_async', return_value=storage_results
        ) as mock_search:
            results = await memory.search("async fallback", user_id="test_user")

        assert results["results"][0]["memory"] == "async offline fallback result"
        call_kwargs = mock_search.call_args.kwargs
        assert call_kwargs["query_embedding"] is None
        assert call_kwargs["query"] == "async fallback"
        assert call_kwargs["retrieval_mode"] == "auto"
    
    @patch('powermem.core.memory.VectorStoreFactory')
    @patch('powermem.core.memory.LLMFactory')
    @patch('powermem.core.memory.EmbedderFactory')
    def test_get_memory(self, mock_embedder_factory, mock_llm_factory, mock_vector_factory):
        """Test getting a specific memory."""
        # Mock the factories
        mock_vector_store = MagicMock()
        mock_vector_factory.create.return_value = mock_vector_store
        
        mock_llm = MagicMock()
        mock_llm_factory.create.return_value = mock_llm
        
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1, 0.2, 0.3]
        mock_embedder_factory.create.return_value = mock_embedder
        
        memory = Memory()
        
        # Mock storage.get_memory to return proper format
        with patch.object(memory.storage, 'get_memory', return_value={"id": "test_id", "memory": "Test memory"}):
            # Get the memory
            retrieved = memory.get("test_id", user_id="test_user")
            
            assert retrieved is not None
            assert retrieved["id"] == "test_id"
    
    @patch('powermem.core.memory.VectorStoreFactory')
    @patch('powermem.core.memory.LLMFactory')
    @patch('powermem.core.memory.EmbedderFactory')
    @patch('powermem.core.memory.IntelligenceManager')
    def test_update_memory(self, mock_intelligence_manager, mock_embedder_factory, mock_llm_factory, mock_vector_factory):
        """Test updating a memory."""
        # Mock the factories
        mock_vector_store = MagicMock()
        mock_vector_factory.create.return_value = mock_vector_store
        
        mock_llm = MagicMock()
        mock_llm.generate_response.return_value = {"content": "Updated content"}
        mock_llm_factory.create.return_value = mock_llm
        
        mock_embedder = MagicMock()
        mock_embedder.embed.return_value = [0.1, 0.2, 0.3]
        mock_embedder_factory.create.return_value = mock_embedder
        
        # Mock IntelligenceManager
        mock_intelligence = MagicMock()
        mock_intelligence.process_content.return_value = ("Updated content", {})
        mock_intelligence_manager.return_value = mock_intelligence
        
        memory = Memory()
        
        # Mock storage methods
        with patch.object(memory.storage, 'get_memory', return_value={"id": "test_id", "memory": "Original content"}), \
             patch.object(memory.storage, 'update_memory', return_value={"id": "test_id", "memory": "Updated content"}):
            # Update the memory
            updated = memory.update("test_id", "Updated content", user_id="test_user")
            
            assert updated is not None
            assert isinstance(updated, dict)
            assert updated["id"] == "test_id"
    
    @patch('powermem.core.memory.VectorStoreFactory')
    @patch('powermem.core.memory.LLMFactory')
    @patch('powermem.core.memory.EmbedderFactory')
    def test_delete_memory(self, mock_embedder_factory, mock_llm_factory, mock_vector_factory):
        """Test deleting a memory."""
        # Mock the factories
        mock_vector_store = MagicMock()
        mock_vector_factory.create.return_value = mock_vector_store
        
        mock_llm = MagicMock()
        mock_llm_factory.create.return_value = mock_llm
        
        mock_embedder = MagicMock()
        mock_embedder_factory.create.return_value = mock_embedder
        
        memory = Memory()
        
        # Mock storage.delete_memory to return True
        with patch.object(memory.storage, 'delete_memory', return_value=True):
            # Delete the memory
            deleted = memory.delete("test_id", user_id="test_user")
            
            assert deleted is True
    
    @patch('powermem.core.memory.VectorStoreFactory')
    @patch('powermem.core.memory.LLMFactory')
    @patch('powermem.core.memory.EmbedderFactory')
    @patch('powermem.core.memory.IntelligenceManager')
    def test_clear_memories(self, mock_intelligence_manager, mock_embedder_factory, mock_llm_factory, mock_vector_factory):
        """Test clearing memories."""
        # Mock the factories
        mock_vector_store = MagicMock()
        mock_vector_factory.create.return_value = mock_vector_store
        
        mock_llm = MagicMock()
        mock_llm_factory.create.return_value = mock_llm
        
        mock_embedder = MagicMock()
        mock_embedder_factory.create.return_value = mock_embedder
        
        # Mock IntelligenceManager
        mock_intelligence = MagicMock()
        mock_intelligence_plugin = MagicMock()
        mock_intelligence_plugin.on_search.return_value = ([], [])
        mock_intelligence.plugin = mock_intelligence_plugin
        mock_intelligence_manager.return_value = mock_intelligence
        
        memory = Memory()
        
        # Mock storage methods
        with patch.object(memory.storage, 'clear_memories', return_value=True), \
             patch.object(memory.storage, 'search_memories', return_value={"results": []}):
            # Clear memories
            cleared = memory.delete_all(user_id="test_user")
            
            assert cleared is True
            
            # Verify memories are cleared
            results = memory.search("", user_id="test_user")
            assert isinstance(results, dict)
            assert "results" in results
            assert len(results["results"]) == 0
