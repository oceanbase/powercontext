"""
End-to-end tests for basic usage scenarios using real configuration.

These tests require proper configuration files to be set up.
They are not included in the default test suite.
Run with: pytest -m e2e_config tests/e2e/test_basic_usage_e2e.py
"""

import os
import pytest
from dotenv import load_dotenv
from powermem import Memory, auto_config


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
    memory = create_memory(config=config)
    yield memory
    # Cleanup: delete test data if needed
    try:
        memory.delete_all(user_id="test_user_123")
    except Exception:
        pass


@pytest.mark.e2e_config
class TestBasicUsageE2E:
    """End-to-end tests for basic memory operations using real configuration."""
    
    def test_basic_memory_operations(self, memory):
        """Test basic memory add, search, and get_all operations."""
        user_id = "test_user_123"
        
        # Add some memories
        memory.add("User likes coffee", user_id=user_id)
        memory.add("User prefers Python over Java", user_id=user_id)
        memory.add("User works as a software engineer", user_id=user_id)
        
        # Search memories
        search_response = memory.search("user preferences", user_id=user_id)
        results = search_response.get('results', [])
        assert len(results) > 0, "Should find at least one memory"
        
        # Get all memories
        all_memories = memory.get_all(user_id=user_id)
        assert len(all_memories.get('results', [])) >= 3, "Should have at least 3 memories"
        
        # Cleanup
        memory.delete_all(user_id=user_id)
    
    def test_memory_isolation(self, memory):
        """Test that memories are isolated by user_id."""
        user1_id = "test_user_1"
        user2_id = "test_user_2"
        
        # Add memories for user 1
        memory.add("User 1 likes Python", user_id=user1_id)
        memory.add("User 1 prefers coffee", user_id=user1_id)
        
        # Add memories for user 2
        memory.add("User 2 likes Java", user_id=user2_id)
        memory.add("User 2 prefers tea", user_id=user2_id)
        
        # Verify isolation
        user1_memories = memory.get_all(user_id=user1_id)
        user2_memories = memory.get_all(user_id=user2_id)
        
        assert len(user1_memories.get('results', [])) >= 2
        assert len(user2_memories.get('results', [])) >= 2
        
        # Search should only return user-specific results
        user1_results = memory.search("Python", user_id=user1_id)
        user2_results = memory.search("Java", user_id=user2_id)
        
        assert len(user1_results.get('results', [])) > 0
        assert len(user2_results.get('results', [])) > 0
        
        # Cleanup
        memory.delete_all(user_id=user1_id)
        memory.delete_all(user_id=user2_id)
    
    def test_memory_with_metadata(self, memory):
        """Test adding memories with metadata."""
        user_id = "test_user_metadata"
        
        metadata = {
            "category": "preference",
            "priority": "high",
            "source": "test"
        }
        
        result = memory.add(
            "User prefers dark mode UI",
            user_id=user_id,
            metadata=metadata
        )
        
        assert result is not None
        
        # Verify memory was stored
        all_memories = memory.get_all(user_id=user_id)
        assert len(all_memories.get('results', [])) > 0
        
        # Cleanup
        memory.delete_all(user_id=user_id)

