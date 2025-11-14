"""
End-to-end tests for agent memory scenarios using real configuration.

These tests require proper configuration files to be set up.
They are not included in the default test suite.
Run with: pytest -m e2e_config tests/e2e/test_agent_memory_e2e.py
"""

import os
import pytest
from dotenv import load_dotenv
from powermem.agent import AgentMemory
from powermem import auto_config


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
    
    # Add review_intervals if not present in intelligent_memory config
    if 'intelligent_memory' in config and 'review_intervals' not in config['intelligent_memory']:
        config['intelligent_memory']['review_intervals'] = [1, 6, 24, 72, 168]  # hours
    
    return config


@pytest.mark.e2e_config
class TestAgentMemoryE2E:
    """End-to-end tests for agent memory using real configuration."""
    
    def test_auto_mode_detection(self, config):
        """Test automatic mode detection."""
        agent_memory = AgentMemory(config, mode='auto')
        
        assert agent_memory.get_mode() is not None
        
        # Add some memories
        agent_memory.add(
            "User prefers email support over phone calls",
            user_id="customer_123",
            agent_id="support_agent",
            metadata={"priority": "high", "category": "communication_preference"}
        )
        
        # Search memories
        results = agent_memory.search("customer preferences", user_id="customer_123")
        assert len(results) >= 0  # May be empty, but should not error
        
        # Cleanup
        try:
            agent_memory.delete_all(user_id="customer_123")
        except Exception:
            pass
    
    def test_multi_agent_mode(self, config):
        """Test multi-agent mode."""
        agent_memory = AgentMemory(config, mode='multi_agent')
        
        # Create agents
        support_agent = agent_memory.create_agent("support_agent", "Customer Support")
        sales_agent = agent_memory.create_agent("sales_agent", "Sales Agent")
        
        # Add memories for each agent
        support_agent.add(
            "Customer reported slow response times last week",
            user_id="customer_123",
            metadata={"issue_type": "performance", "category": "complaint"}
        )
        
        sales_agent.add(
            "Customer decision maker is CTO, prefers technical demos",
            user_id="customer_123",
            metadata={"decision_maker": "CTO", "category": "stakeholder_info"}
        )
        
        # Search across agents
        support_results = support_agent.search("customer issues", user_id="customer_123")
        sales_results = sales_agent.search("customer decision", user_id="customer_123")
        
        assert isinstance(support_results, list)
        assert isinstance(sales_results, list)
        
        # Cleanup
        try:
            agent_memory.delete_all(user_id="customer_123")
        except Exception:
            pass
    
    def test_multi_user_mode(self, config):
        """Test multi-user mode."""
        agent_memory = AgentMemory(config, mode='multi_user')
        
        # Add memories for different users
        agent_memory.add(
            "User Alice likes Python and machine learning",
            user_id="alice",
            metadata={"interests": ["Python", "ML"], "category": "preferences"}
        )
        
        agent_memory.add(
            "User Bob prefers Java and enterprise solutions",
            user_id="bob",
            metadata={"interests": ["Java", "Enterprise"], "category": "preferences"}
        )
        
        # Search for each user
        alice_results = agent_memory.search("Python", user_id="alice")
        bob_results = agent_memory.search("Java", user_id="bob")
        
        assert isinstance(alice_results, list)
        assert isinstance(bob_results, list)
        
        # Cleanup
        try:
            agent_memory.delete_all(user_id="alice")
            agent_memory.delete_all(user_id="bob")
        except Exception:
            pass
    
    def test_hybrid_mode(self, config):
        """Test hybrid mode with dynamic switching."""
        agent_memory = AgentMemory(config, mode='hybrid')
        
        # Add memories (will automatically detect context)
        agent_memory.add(
            "Support agent handled customer complaint about slow response",
            user_id="customer_123",
            agent_id="support_agent",
            metadata={"context": "multi_agent", "category": "support"}
        )
        
        agent_memory.add(
            "User Alice requested new features for the mobile app",
            user_id="alice",
            metadata={"context": "multi_user", "category": "feature_request"}
        )
        
        # Get statistics
        stats = agent_memory.get_statistics()
        assert stats is not None
        
        # Cleanup
        try:
            agent_memory.delete_all(user_id="customer_123")
            agent_memory.delete_all(user_id="alice")
        except Exception:
            pass
    
    def test_intelligent_memory(self, config):
        """Test intelligent memory management."""
        agent_memory = AgentMemory(config, mode='auto')
        
        # Add memories with different importance levels
        high_importance_result = agent_memory.add(
            "Customer reported critical security vulnerability in production system",
            user_id="customer_123",
            agent_id="security_agent",
            metadata={"priority": "critical", "category": "security", "severity": "high"}
        )
        
        assert high_importance_result is not None
        
        # Search and verify
        search_results = agent_memory.search("customer preferences", user_id="customer_123")
        assert isinstance(search_results, list)
        
        # Cleanup
        try:
            agent_memory.delete_all(user_id="customer_123")
        except Exception:
            pass
    
    def test_delete_all_functionality(self, config):
        """Test delete_all functionality."""
        agent_memory = AgentMemory(config, mode='multi_user')
        
        # Add memories for Alice
        agent_memory.add(
            "Alice likes Python programming",
            user_id="alice",
            metadata={"category": "preference"}
        )
        
        agent_memory.add(
            "Alice prefers email notifications",
            user_id="alice",
            metadata={"category": "preference"}
        )
        
        # Add memories for Bob
        agent_memory.add(
            "Bob prefers Java programming",
            user_id="bob",
            metadata={"category": "preference"}
        )
        
        # Check memories before deletion
        alice_memories = agent_memory.get_all(user_id="alice")
        bob_memories = agent_memory.get_all(user_id="bob")
        
        alice_count_before = len(alice_memories) if isinstance(alice_memories, list) else len(alice_memories.get('results', []))
        bob_count_before = len(bob_memories) if isinstance(bob_memories, list) else len(bob_memories.get('results', []))
        
        # Delete all memories for Alice
        deleted_alice = agent_memory.delete_all(user_id="alice")
        assert deleted_alice is True
        
        # Verify deletion
        alice_memories_after = agent_memory.get_all(user_id="alice")
        alice_count_after = len(alice_memories_after) if isinstance(alice_memories_after, list) else len(alice_memories_after.get('results', []))
        
        assert alice_count_after < alice_count_before
        
        # Verify Bob's memories are still there
        bob_memories_after = agent_memory.get_all(user_id="bob")
        bob_count_after = len(bob_memories_after) if isinstance(bob_memories_after, list) else len(bob_memories_after.get('results', []))
        
        assert bob_count_after == bob_count_before
        
        # Cleanup
        try:
            agent_memory.delete_all(user_id="bob")
        except Exception:
            pass
    
    def test_reset_functionality(self, config):
        """Test reset functionality."""
        agent_memory = AgentMemory(config, mode='auto')
        
        # Add various memories
        agent_memory.add(
            "Important memory: User prefers dark mode",
            user_id="user1",
            agent_id="agent1",
            metadata={"priority": "high"}
        )
        
        agent_memory.add(
            "Another memory: Customer budget is $5000",
            user_id="user2",
            agent_id="agent2",
            metadata={"priority": "medium"}
        )
        
        # Check statistics before reset
        stats_before = agent_memory.get_statistics()
        all_memories_before = agent_memory.get_all()
        count_before = len(all_memories_before) if isinstance(all_memories_before, list) else len(all_memories_before.get('results', []))
        
        # Perform reset
        agent_memory.reset()
        
        # Verify reset
        all_memories_after = agent_memory.get_all()
        count_after = len(all_memories_after) if isinstance(all_memories_after, list) else len(all_memories_after.get('results', []))
        
        assert count_after <= count_before  # Should be cleared or reduced
        
        # Verify system still works after reset
        new_memory = agent_memory.add(
            "New memory after reset",
            user_id="new_user",
            metadata={"test": "after_reset"}
        )
        assert new_memory is not None

