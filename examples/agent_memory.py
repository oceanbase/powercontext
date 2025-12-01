"""
Unified Agent Memory Demo

This example demonstrates the new unified AgentMemory interface that provides
a simple, consistent API for all agent memory scenarios:
- Multi-Agent: Multiple agents with collaboration
- Multi-User: Single agent with multiple users
- Hybrid: Dynamic switching between modes
- Auto: Intelligent mode detection

The interface automatically handles the complexity of the underlying implementations
while providing a clean, easy-to-use API.
"""

import os
import sys
from typing import Dict, Any
from dotenv import load_dotenv

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from powermem.agent import AgentMemory
from powermem import auto_config


def load_oceanbase_config():
    """
    Load OceanBase configuration from environment variables.
    
    Uses the auto_config() utility function to automatically load from .env.
    """
    oceanbase_env_path = os.path.join(os.path.dirname(__file__), '..', '.env')
    
    if os.path.exists(oceanbase_env_path):
        load_dotenv(oceanbase_env_path, override=True)
    else:
        # Try to load from any .env file
        load_dotenv()
    
    # Automatically load config from environment variables
    config = auto_config()
    
    # Add review_intervals if not present in intelligent_memory config
    if 'intelligent_memory' in config and 'review_intervals' not in config['intelligent_memory']:
        config['intelligent_memory']['review_intervals'] = [1, 6, 24, 72, 168]  # hours
    
    return config


def demonstrate_auto_mode():
    """Demonstrate automatic mode detection."""
    print("🤖 Auto Mode Detection Demo")
    print("=" * 50)
    
    config = load_oceanbase_config()
    
    # Create AgentMemory with auto mode detection
    agent_memory = AgentMemory(config, mode='auto')
    
    print(f"✅ Detected mode: {agent_memory.get_mode()}")
    
    # Add some memories
    print("\n📝 Adding memories...")
    
    agent_memory.add(
        "User prefers email support over phone calls",
        user_id="customer_123",
        agent_id="support_agent",
        metadata={"priority": "high", "category": "communication_preference"}
    )
    
    agent_memory.add(
        "Customer budget is $1000/month for enterprise solutions",
        user_id="customer_123",
        agent_id="sales_agent",
        metadata={"budget": 1000, "category": "budget_info"}
    )
    
    print("✅ Memories added successfully!")
    
    # Search memories
    print("\n🔍 Searching memories...")
    results = agent_memory.search("customer preferences", user_id="customer_123")
    print(f"Found {len(results)} memories")
    
    # Get statistics
    stats = agent_memory.get_statistics()
    print(f"\n📊 Statistics: {stats}")


def demonstrate_multi_agent_mode():
    """Demonstrate multi-agent mode."""
    print("\n🤖 Multi-Agent Mode Demo")
    print("=" * 50)
    
    config = load_oceanbase_config()
    
    # Create AgentMemory in multi-agent mode
    agent_memory = AgentMemory(config, mode='multi_agent')
    
    print(f"✅ Mode: {agent_memory.get_mode()}")
    
    # Create agents
    support_agent = agent_memory.create_agent("support_agent", "Customer Support")
    sales_agent = agent_memory.create_agent("sales_agent", "Sales Agent")
    
    print("✅ Agents created successfully!")
    
    # Add memories for each agent
    print("\n📝 Adding agent-specific memories...")
    
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
    
    print("✅ Agent memories added successfully!")
    
    # Create a group
    group_result = agent_memory.create_group(
        "customer_team",
        ["support_agent", "sales_agent"],
        permissions={
            "owner": ["read", "write", "delete", "admin"],
            "collaborator": ["read", "write"],
            "viewer": ["read"]
        }
    )
    
    print(f"✅ Group created: {group_result}")
    
    # Search across agents
    print("\n🔍 Cross-agent search...")
    support_results = support_agent.search("customer issues", user_id="customer_123")
    sales_results = sales_agent.search("customer decision", user_id="customer_123")
    
    print(f"Support agent found {len(support_results)} memories")
    print(f"Sales agent found {len(sales_results)} memories")


def demonstrate_multi_user_mode():
    """Demonstrate multi-user mode."""
    print("\n👥 Multi-User Mode Demo")
    print("=" * 50)
    
    config = load_oceanbase_config()
    
    # Create AgentMemory in multi-user mode
    agent_memory = AgentMemory(config, mode='multi_user')
    
    print(f"✅ Mode: {agent_memory.get_mode()}")
    
    # Add memories for different users
    print("\n📝 Adding user-specific memories...")
    
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
    
    agent_memory.add(
        "User Charlie is interested in DevOps and automation",
        user_id="charlie",
        metadata={"interests": ["DevOps", "Automation"], "category": "preferences"}
    )
    
    print("✅ User memories added successfully!")
    
    # Search for each user
    print("\n🔍 User-specific searches...")
    
    alice_results = agent_memory.search("Python", user_id="alice")
    bob_results = agent_memory.search("Java", user_id="bob")
    charlie_results = agent_memory.search("DevOps", user_id="charlie")
    
    print(f"Alice found {len(alice_results)} memories")
    print(f"Bob found {len(bob_results)} memories")
    print(f"Charlie found {len(charlie_results)} memories")
    
    # Get all memories
    all_memories = agent_memory.get_all()
    print(f"\n📊 Total memories across all users: {len(all_memories)}")


def demonstrate_hybrid_mode():
    """Demonstrate hybrid mode with dynamic switching."""
    print("\n🔄 Hybrid Mode Demo")
    print("=" * 50)
    
    config = load_oceanbase_config()
    
    # Create AgentMemory in hybrid mode
    agent_memory = AgentMemory(config, mode='hybrid')
    
    print(f"✅ Mode: {agent_memory.get_mode()}")
    
    # Add memories (will automatically detect context)
    print("\n📝 Adding memories with automatic context detection...")
    
    # This might be detected as multi-agent context
    agent_memory.add(
        "Support agent handled customer complaint about slow response",
        user_id="customer_123",
        agent_id="support_agent",
        metadata={"context": "multi_agent", "category": "support"}
    )
    
    # This might be detected as multi-user context
    agent_memory.add(
        "User Alice requested new features for the mobile app",
        user_id="alice",
        metadata={"context": "multi_user", "category": "feature_request"}
    )
    
    print("✅ Hybrid memories added successfully!")
    
    # Get statistics
    stats = agent_memory.get_statistics()
    print(f"\n📊 Hybrid statistics: {stats}")


def demonstrate_ebbinghaus_algorithm():
    """Demonstrate Ebbinghaus forgetting curve algorithm in detail."""
    print("\n📈 Ebbinghaus Forgetting Curve Algorithm Demo")
    print("=" * 50)
    
    config = load_oceanbase_config()
    agent_memory = AgentMemory(config, mode='auto')
    
    print("🧠 Testing Ebbinghaus algorithm with different content types...")
    
    # Test different types of content to see how importance is evaluated
    test_cases = [
        {
            "content": "URGENT: System down, all users affected, immediate action required",
            "metadata": {"urgency": "critical", "impact": "high", "category": "incident"},
            "expected_type": "long_term"
        },
        {
            "content": "User mentioned they prefer dark mode in the application",
            "metadata": {"preference": "ui", "category": "user_preference"},
            "expected_type": "short_term"
        },
        {
            "content": "Customer said hello and asked about the weather",
            "metadata": {"category": "casual_conversation"},
            "expected_type": "working"
        }
    ]
    
    results = []
    for i, test_case in enumerate(test_cases):
        print(f"\n📝 Test Case {i+1}: {test_case['expected_type']} expected")
        print(f"   Content: {test_case['content']}")
        
        result = agent_memory.add(
            test_case['content'],
            user_id="test_user",
            agent_id="test_agent",
            metadata=test_case['metadata']
        )
        
        results.append(result)
        
        # Extract intelligence data
        intelligence = result.get('metadata', {}).get('intelligence', {})
        if intelligence:
            importance = intelligence.get('importance_score', 0)
            memory_type = intelligence.get('memory_type', 'unknown')
            retention = intelligence.get('initial_retention', 0)
            decay_rate = intelligence.get('decay_rate', 0)
            
            print(f"   🎯 Importance Score: {importance:.3f}")
            print(f"   🧠 Memory Type: {memory_type}")
            print(f"   📊 Initial Retention: {retention:.3f}")
            print(f"   ⚡ Decay Rate: {decay_rate:.3f}")
            print(f"   ✅ Type Match: {'✓' if memory_type == test_case['expected_type'] else '✗'}")
    
    # Show review schedules
    print("\n📅 Review Schedules Comparison:")
    print("-" * 40)
    
    for i, result in enumerate(results):
        intelligence = result.get('metadata', {}).get('intelligence', {})
        memory_type = intelligence.get('memory_type', 'unknown')
        review_schedule = intelligence.get('review_schedule', [])
        
        print(f"\n{memory_type.title()} Memory:")
        print(f"  Next review: {intelligence.get('next_review', 'N/A')}")
        print(f"  Review schedule: {len(review_schedule)} reviews planned")
        
        if review_schedule:
            print("  Review times:")
            for j, review_time in enumerate(review_schedule[:3]):  # Show first 3 reviews
                print(f"    {j+1}. {review_time}")
            if len(review_schedule) > 3:
                print(f"    ... and {len(review_schedule) - 3} more reviews")
    
    print("\n✅ Ebbinghaus algorithm demonstration completed!")


def demonstrate_intelligent_memory():
    """Demonstrate intelligent memory management with metadata processing."""
    print("\n🧠 Intelligent Memory Management Demo")
    print("=" * 50)
    
    config = load_oceanbase_config()
    
    # Create AgentMemory with intelligent memory enabled
    agent_memory = AgentMemory(config, mode='auto')
    
    print(f"✅ Intelligent memory enabled: {config['intelligent_memory']['enabled']}")
    
    # Add memories with different importance levels
    print("\n📝 Adding memories with different importance levels...")
    
    # High importance memory
    high_importance_result = agent_memory.add(
        "Customer reported critical security vulnerability in production system",
        user_id="customer_123",
        agent_id="security_agent",
        metadata={"priority": "critical", "category": "security", "severity": "high"}
    )
    
    # Medium importance memory
    medium_importance_result = agent_memory.add(
        "User prefers email notifications over SMS",
        user_id="customer_123",
        agent_id="preference_agent",
        metadata={"priority": "medium", "category": "preference", "type": "notification"}
    )
    
    # Low importance memory
    low_importance_result = agent_memory.add(
        "User mentioned they like the color blue in UI",
        user_id="customer_123",
        agent_id="ui_agent",
        metadata={"priority": "low", "category": "ui_preference", "color": "blue"}
    )
    
    print("✅ Memories added successfully!")
    
    # Display intelligence metadata
    print("\n🔍 Intelligence Analysis Results:")
    print("-" * 40)
    
    memories = [high_importance_result, medium_importance_result, low_importance_result]
    descriptions = ["High Importance", "Medium Importance", "Low Importance"]
    
    for i, (memory, desc) in enumerate(zip(memories, descriptions)):
        print(f"\n{desc} Memory:")
        print(f"  Content: {memory.get('content', 'N/A')[:50]}...")
        
        # Extract intelligence metadata
        metadata = memory.get('metadata', {})
        intelligence = metadata.get('intelligence', {})
        
        if intelligence:
            print(f"  🎯 Importance Score: {intelligence.get('importance_score', 'N/A')}")
            print(f"  🧠 Memory Type: {intelligence.get('memory_type', 'N/A')}")
            print(f"  📊 Initial Retention: {intelligence.get('initial_retention', 'N/A')}")
            print(f"  ⏰ Next Review: {intelligence.get('next_review', 'N/A')}")
            print(f"  📅 Review Schedule: {len(intelligence.get('review_schedule', []))} reviews planned")
            
            # Memory management flags
            memory_mgmt = metadata.get('memory_management', {})
            print(f"  🔄 Should Promote: {memory_mgmt.get('should_promote', False)}")
            print(f"  🗑️ Should Forget: {memory_mgmt.get('should_forget', False)}")
            print(f"  📦 Should Archive: {memory_mgmt.get('should_archive', False)}")
        else:
            print("  ⚠️ No intelligence metadata found")
    
    # Search and show how intelligence affects results
    print("\n🔍 Intelligent Search Results:")
    print("-" * 40)
    
    search_results = agent_memory.search("customer preferences", user_id="customer_123")
    print(f"Found {len(search_results)} memories for 'customer preferences'")
    
    for i, result in enumerate(search_results):
        intelligence = result.get('metadata', {}).get('intelligence', {})
        importance = intelligence.get('importance_score', 0)
        memory_type = intelligence.get('memory_type', 'unknown')
        
        print(f"  {i+1}. [{memory_type}] Importance: {importance:.2f} - {result.get('content', '')[:60]}...")
    
    # Show statistics
    stats = agent_memory.get_statistics()
    print(f"\n📊 Memory Statistics: {stats}")


def demonstrate_unified_api():
    """Demonstrate the unified API across all modes."""
    print("\n🎯 Unified API Demo")
    print("=" * 50)
    
    config = load_oceanbase_config()
    
    # Test the same API across different modes
    modes = ['auto', 'multi_agent', 'multi_user', 'hybrid']
    
    for mode in modes:
        print(f"\n📋 Testing {mode} mode...")
        
        try:
            # Create agent memory
            agent_memory = AgentMemory(config, mode=mode)
            
            # Test basic operations
            agent_memory.add(
                f"Test memory for {mode} mode",
                user_id="test_user",
                agent_id="test_agent",
                metadata={"mode": mode, "test": True}
            )
            
            # Test search
            results = agent_memory.search("test", user_id="test_user")
            
            # Test statistics
            stats = agent_memory.get_statistics()
            
            print(f"  ✅ {mode}: Added memory, found {len(results)} results, stats: {len(stats)} fields")
            
        except Exception as e:
            print(f"  ❌ {mode}: Error - {e}")


def demonstrate_delete_all():
    """Demonstrate delete_all functionality."""
    print("\n🗑️ Delete All Memories Demo")
    print("=" * 50)
    
    config = load_oceanbase_config()
    agent_memory = AgentMemory(config, mode='multi_user')
    
    print("📝 Adding memories for different users...")
    
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
    
    print("✅ Memories added successfully!")
    
    # Check memories before deletion
    print("\n🔍 Checking memories before deletion...")
    alice_memories = agent_memory.get_all(user_id="alice")
    bob_memories = agent_memory.get_all(user_id="bob")
    all_memories = agent_memory.get_all()
    
    print(f"  Alice has {len(alice_memories)} memories")
    print(f"  Bob has {len(bob_memories)} memories")
    print(f"  Total memories: {len(all_memories)}")
    
    # Delete all memories for Alice
    print("\n🗑️ Testing delete_all for user 'alice'...")
    try:
        deleted_alice = agent_memory.delete_all(user_id="alice")
        print(f"  ✅ Delete all for Alice: {'Success' if deleted_alice else 'Failed'}")
    except Exception as e:
        print(f"  ❌ Error during delete_all: {e}")
        import traceback
        traceback.print_exc()
    
    # Verify deletion
    print("\n🔍 Verifying deletion...")
    alice_memories_after = agent_memory.get_all(user_id="alice")
    bob_memories_after = agent_memory.get_all(user_id="bob")
    all_memories_after = agent_memory.get_all()
    
    print(f"  Alice has {len(alice_memories_after)} memories (deleted: {len(alice_memories) - len(alice_memories_after)})")
    print(f"  Bob has {len(bob_memories_after)} memories (unchanged: {len(bob_memories) == len(bob_memories_after)})")
    print(f"  Total memories: {len(all_memories_after)} (reduced by {len(all_memories) - len(all_memories_after)})")
    
    # Test delete_all with agent_id
    print("\n🗑️ Testing delete_all with agent_id...")
    agent_memory.add(
        "Test memory with agent_id",
        user_id="test_user",
        agent_id="test_agent",
        metadata={"test": True}
    )
    
    memories_before = agent_memory.get_all(agent_id="test_agent")
    print(f"  Memories with test_agent before: {len(memories_before)}")
    
    try:
        deleted_agent = agent_memory.delete_all(agent_id="test_agent")
        print(f"  ✅ Delete all for test_agent: {'Success' if deleted_agent else 'Failed'}")
    except Exception as e:
        print(f"  ❌ Error during delete_all with agent_id: {e}")
    
    memories_after = agent_memory.get_all(agent_id="test_agent")
    print(f"  Memories with test_agent after: {len(memories_after)}")
    
    print("\n✅ Delete all demonstration completed!")


def demonstrate_reset():
    """Demonstrate reset functionality."""
    print("\n🔄 Reset Memory Store Demo")
    print("=" * 50)
    
    config = load_oceanbase_config()
    agent_memory = AgentMemory(config, mode='auto')
    
    print("📝 Adding memories before reset...")
    
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
    
    agent_memory.add(
        "Third memory: System configuration details",
        user_id="user3",
        agent_id="agent3",
        metadata={"priority": "low"}
    )
    
    print("✅ Memories added successfully!")
    
    # Check statistics before reset
    print("\n📊 Statistics before reset:")
    stats_before = agent_memory.get_statistics()
    all_memories_before = agent_memory.get_all()
    
    print(f"  Total memories: {len(all_memories_before)}")
    print(f"  Statistics: {stats_before}")
    
    # Perform reset
    print("\n🔄 Resetting memory store...")
    try:
        agent_memory.reset()
        print("  ✅ Reset completed successfully!")
    except Exception as e:
        print(f"  ❌ Error during reset: {e}")
        import traceback
        traceback.print_exc()
        return
    
    # Verify reset
    print("\n🔍 Verifying reset...")
    all_memories_after = agent_memory.get_all()
    stats_after = agent_memory.get_statistics()
    
    print(f"  Total memories after reset: {len(all_memories_after)}")
    print(f"  Statistics after reset: {stats_after}")
    
    if len(all_memories_after) == 0:
        print("  ✅ Reset successful - all memories cleared!")
    else:
        print(f"  ⚠️ Warning: {len(all_memories_after)} memories still exist after reset")
    
    # Add new memory after reset to verify system still works
    print("\n📝 Adding new memory after reset...")
    try:
        new_memory = agent_memory.add(
            "New memory after reset",
            user_id="new_user",
            metadata={"test": "after_reset"}
        )
        print(f"  ✅ New memory added successfully: {new_memory.get('id', 'N/A')}")
        
        # Verify new memory can be retrieved
        search_results = agent_memory.search("new memory", user_id="new_user")
        print(f"  ✅ Found {len(search_results)} memories after reset")
    except Exception as e:
        print(f"  ❌ Error adding memory after reset: {e}")
    
    print("\n✅ Reset demonstration completed!")


def main():
    """Main function to run the unified agent memory demo."""
    print("🚀 Unified Agent Memory Management Demo")
    print("=" * 60)
    print("Database: OceanBase")
    print("LLM: Qwen")
    print("Embedding: Qwen text-embedding-v4")
    print("=" * 60)
    
    try:
        # Demonstrate different modes
        demonstrate_auto_mode()
        demonstrate_multi_agent_mode()
        demonstrate_multi_user_mode()
        demonstrate_hybrid_mode()
        demonstrate_intelligent_memory()
        demonstrate_ebbinghaus_algorithm()
        demonstrate_unified_api()
        demonstrate_delete_all()
        demonstrate_reset()
        
        print("\n🎉 Unified Agent Memory Demo Completed Successfully!")
        print("=" * 60)
        print("✅ All features demonstrated:")
        print("  • Automatic mode detection")
        print("  • Multi-agent collaboration")
        print("  • Multi-user isolation")
        print("  • Hybrid dynamic switching")
        print("  • Intelligent memory management with Ebbinghaus algorithm")
        print("  • Metadata-based intelligence processing")
        print("  • Detailed Ebbinghaus forgetting curve demonstration")
        print("  • Review schedule generation and decay calculation")
        print("  • Unified API across all modes")
        print("  • Delete all memories functionality")
        print("  • Reset memory store functionality")
        print("  • Simple, consistent interface")
        
    except Exception as e:
        print(f"❌ Error during demo: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
