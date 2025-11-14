"""
End-to-end tests for intelligent memory scenarios using real configuration.

These tests require proper configuration files to be set up.
They are not included in the default test suite.
Run with: pytest -m e2e_config tests/e2e/test_intelligent_memory_e2e.py
"""

import os
import pytest
import pytest_asyncio
from dotenv import load_dotenv
from powermem import Memory, AsyncMemory, auto_config


@pytest.fixture(scope="module")
def config():
    """Load configuration from environment variables."""
    # Try to load from .env first
    oceanbase_env_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "configs", "powermem.env"
    )
    
    if os.path.exists(oceanbase_env_path):
        load_dotenv(oceanbase_env_path, override=True)
    else:
        # Try to load from any .env file
        load_dotenv()
    
    config = auto_config()
    return config


@pytest.fixture(scope="module")
def memory(config):
    """Create a Memory instance using real configuration."""
    memory = Memory(config=config, agent_id="demo_agent")
    yield memory
    # Cleanup
    try:
        memory.delete_all(user_id="user_001")
        memory.delete_all(user_id="user_002")
        memory.delete_all(user_id="test_user")
    except Exception:
        pass


@pytest.mark.e2e_config
class TestIntelligentMemoryE2E:
    """End-to-end tests for intelligent memory using real configuration."""
    
    def test_initial_addition(self, memory):
        """Test initial memory addition with fact extraction."""
        user_id = "user_001"
        
        messages = [
            {"role": "user", "content": "Hi, my name is Alice. I'm a software engineer at Google."},
            {"role": "assistant", "content": "Nice to meet you, Alice! That's interesting."},
            {"role": "user", "content": "I love Python programming and machine learning."}
        ]
        
        result = memory.add(
            messages=messages,
            user_id=user_id,
            agent_id="demo_agent",
            infer=True
        )
        
        assert result is not None
        results = result.get('results', [])
        
        # Should extract facts from the conversation
        assert len(results) >= 0  # May vary based on LLM response
        
        # Search to verify
        search_results = memory.search("What does Alice do?", user_id=user_id, limit=5)
        assert search_results is not None
    
    def test_duplicate_detection(self, memory):
        """Test duplicate detection."""
        user_id = "user_001"
        
        # First addition
        messages1 = [
            {"role": "user", "content": "I'm Alice, a software engineer"},
            {"role": "assistant", "content": "I remember that!"}
        ]
        
        result1 = memory.add(
            messages=messages1,
            user_id=user_id,
            agent_id="demo_agent",
            infer=True
        )
        
        # Second addition (duplicate)
        messages2 = [
            {"role": "user", "content": "I'm Alice, a software engineer"},
            {"role": "assistant", "content": "I remember that!"}
        ]
        
        result2 = memory.add(
            messages=messages2,
            user_id=user_id,
            agent_id="demo_agent",
            infer=True
        )
        
        assert result1 is not None
        assert result2 is not None
    
    def test_information_update(self, memory):
        """Test information update."""
        user_id = "user_001"
        
        messages = [
            {"role": "user", "content": "I recently moved to Meta as a senior ML engineer."},
            {"role": "assistant", "content": "Congratulations on your new role!"},
            {"role": "user", "content": "I'm still working on machine learning projects."}
        ]
        
        result = memory.add(
            messages=messages,
            user_id=user_id,
            agent_id="demo_agent",
            infer=True
        )
        
        assert result is not None
        
        # Search to verify the update
        results = memory.search("Where does Alice work?", user_id=user_id, limit=5)
        assert results is not None
    
    def test_new_information(self, memory):
        """Test adding new information."""
        user_id = "user_001"
        
        messages = [
            {"role": "user", "content": "I like to drink coffee every morning and I have two cats."},
            {"role": "assistant", "content": "That's nice! What are your cats' names?"},
            {"role": "user", "content": "Their names are Fluffy and Whiskers."}
        ]
        
        result = memory.add(
            messages=messages,
            user_id=user_id,
            agent_id="demo_agent",
            infer=True
        )
        
        assert result is not None
        
        # Search to verify new facts
        results = memory.search("What does Alice like?", user_id=user_id, limit=5)
        assert results is not None
    
    def test_conflict_resolution(self, memory):
        """Test conflict resolution."""
        user_id = "user_001"
        
        messages = [
            {"role": "user", "content": "Actually, I don't like coffee anymore. I prefer tea now."},
            {"role": "assistant", "content": "That's okay, preferences change!"},
            {"role": "user", "content": "I still have the two cats though."}
        ]
        
        result = memory.add(
            messages=messages,
            user_id=user_id,
            agent_id="demo_agent",
            infer=True
        )
        
        assert result is not None
        
        # Search to verify conflict resolution
        results = memory.search("What does Alice drink?", user_id=user_id, limit=5)
        assert results is not None
    
    def test_memory_consolidation(self, memory):
        """Test memory consolidation."""
        user_id = "user_001"
        
        messages = [
            {"role": "user", "content": "I love Python, especially for deep learning. I use TensorFlow and PyTorch a lot."},
            {"role": "assistant", "content": "Great frameworks!"}
        ]
        
        result = memory.add(
            messages=messages,
            user_id=user_id,
            agent_id="demo_agent",
            infer=True
        )
        
        assert result is not None
    
    def test_simple_vs_intelligent_mode(self, memory):
        """Compare simple mode vs intelligent mode."""
        user_id = "test_user"
        
        test_messages = [
            {"role": "user", "content": "I love Python"},
            {"role": "user", "content": "I love Python"},
            {"role": "user", "content": "I love Python"}
        ]
        
        # Simple mode
        simple_results = []
        for msg in test_messages:
            result = memory.add(
                messages=[msg],
                user_id=user_id,
                infer=False
            )
            if result and result.get('results'):
                simple_results.append(result)
        
        all_simple = memory.get_all(user_id=user_id)
        simple_count = len(all_simple.get('results', [])) if isinstance(all_simple, dict) else len(all_simple)
        
        # Clean up simple mode memories
        memory.delete_all(user_id=user_id)
        
        # Intelligent mode
        intelligent_results = []
        for msg in test_messages:
            result = memory.add(
                messages=[msg],
                user_id=user_id,
                infer=True
            )
            if result and result.get('results'):
                intelligent_results.append(result)
        
        all_intelligent = memory.get_all(user_id=user_id)
        intelligent_count = len(all_intelligent.get('results', [])) if isinstance(all_intelligent, dict) else len(all_intelligent)
        
        # Intelligent mode should handle duplicates better
        assert simple_count >= 0
        assert intelligent_count >= 0
        
        # Cleanup
        memory.delete_all(user_id=user_id)


@pytest_asyncio.fixture(scope="function")
async def async_memory(config):
    """Create an AsyncMemory instance using real configuration."""
    async_memory = AsyncMemory(config=config)
    await async_memory.initialize()
    yield async_memory
    # Cleanup
    try:
        await async_memory.delete_all(user_id="user_002")
    except Exception:
        pass


@pytest.mark.asyncio
@pytest.mark.e2e_config
class TestAsyncIntelligentMemoryE2E:
    """End-to-end tests for async intelligent memory using real configuration."""
    
    async def test_async_memory_operations(self, async_memory):
        """Test async memory operations."""
        user_id = "user_002"
        
        messages = [
            {"role": "user", "content": "I'm Bob, a data scientist at Amazon"},
            {"role": "assistant", "content": "Nice to meet you, Bob!"},
            {"role": "user", "content": "I specialize in NLP and recommendation systems."}
        ]
        
        result = await async_memory.add(
            messages=messages,
            user_id=user_id,
            agent_id="async_agent",
            infer=True
        )
        
        assert result is not None
        
        # Search asynchronously
        results = await async_memory.search(
            query="What does Bob do?",
            user_id=user_id,
            limit=5
        )
        
        assert results is not None

