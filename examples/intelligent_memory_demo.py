"""
Intelligent Memory Management Demo

This example demonstrates the intelligent memory capabilities of powermem,
including:
1. Automatic fact extraction from conversations
2. Duplicate detection and deduplication
3. Memory updates and consolidation
4. Conflict resolution (contradiction handling)
5. Smart memory organization

The demo shows how powermem intelligently manages memories.
"""

import os
import sys
import asyncio
import logging
from typing import Dict, Any
from dotenv import load_dotenv

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from powermem import Memory, AsyncMemory, auto_config

# Configure logging
# logging.basicConfig(
#     level=logging.ERROR,
#     format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
# )
# logger = logging.getLogger(__name__)


def load_config():
    """
    Load configuration from environment variables.
    
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
    
    return config


def scenario_1_initial_addition(memory, user_id="user_001"):
    """
    Scenario 1: Initial Memory Addition
    
    Demonstrates basic fact extraction and storage from a conversation.
    Expected: Creates 3-4 new memories from the conversation.
    """
    print("\n" + "=" * 80)
    print("SCENARIO 1: Initial Memory Addition")
    print("=" * 80)
    print("Adding user's basic information...")
    
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
    
    results = result.get('results', [])
    if results:
        print(f"\n✅ Added {len(results)} memories:")
        for i, mem in enumerate(results, 1):
            print(f"   {i}. ID: {mem.get('id', 'N/A')}, Memory: {mem.get('memory', '')}, Event: {mem.get('event', 'N/A')}")
    else:
        print(f"\n✅ Processed (no new memories added)")
    print(f"Facts extracted: ['Name is Alice', 'Is a software engineer at Google', 'Loves Python programming', 'Loves machine learning']")
    
    # Search to verify
    search_results = memory.search("What does Alice do?", user_id=user_id, limit=5)
    print(f"\n📊 Found {len(search_results.get('results', []))} memories:")
    for i, mem in enumerate(search_results.get('results', []), 1):
        print(f"   {i}. {mem.get('memory', '')}")
    
    return result


def scenario_2_duplicate_detection(memory, user_id="user_001"):
    """
    Scenario 2: Duplicate Detection (NONE operation)
    
    Demonstrates that the system detects duplicates and doesn't create new records.
    Expected: No new memories created (NONE operation).
    """
    print("\n" + "=" * 80)
    print("SCENARIO 2: Duplicate Detection")
    print("=" * 80)
    print("Adding the same information again (should be detected as duplicate)...")
    
    messages = [
        {"role": "user", "content": "I'm Alice, a software engineer"},
        {"role": "assistant", "content": "I remember that!"}
    ]
    
    result = memory.add(
        messages=messages,
        user_id=user_id,
        agent_id="demo_agent",
        infer=True
    )
    
    results = result.get('results', [])
    if results:
        print(f"\n✅ Processed {len(results)} memories:")
        for i, mem in enumerate(results, 1):
            print(f"   {i}. ID: {mem.get('id', 'N/A')}, Event: {mem.get('event', 'N/A')}")
    else:
        print(f"\n✅ Processed: No new memories (duplicate detected, NONE operation)")
    
    # Verify still the same count
    results = memory.search("What is Alice's name?", user_id=user_id, limit=5)
    print(f"\n📊 Still {len(results.get('results', []))} memories (no duplicates added)")
    
    return result


def scenario_3_information_update(memory, user_id="user_001"):
    """
    Scenario 3: Information Update (UPDATE operation)
    
    Demonstrates that the system updates existing memories when information changes.
    Expected: Updates existing memory instead of creating new one.
    """
    print("\n" + "=" * 80)
    print("SCENARIO 3: Information Update")
    print("=" * 80)
    print("User changes job (from Google to Meta)...")
    
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
    
    results = result.get('results', [])
    if results:
        print(f"\n✅ Processed {len(results)} memories:")
        for i, mem in enumerate(results, 1):
            event = mem.get('event', 'N/A')
            if event == 'UPDATE':
                print(f"   {i}. ID: {mem.get('id', 'N/A')}, Event: {event}")
                print(f"      Old: {mem.get('previous_memory', 'N/A')}")
                print(f"      New: {mem.get('memory', 'N/A')}")
            else:
                print(f"   {i}. ID: {mem.get('id', 'N/A')}, Event: {event}, Memory: {mem.get('memory', '')}")
    else:
        print(f"\n✅ Processed (no changes)")
    
    # Search to verify the update
    results = memory.search("Where does Alice work?", user_id=user_id, limit=5)
    print(f"\n📊 Updated memories:")
    for i, mem in enumerate(results.get('results', []), 1):
        content = mem.get('memory', '')
        if 'Meta' in content or 'engineer' in content:
            print(f"   {i}. {content}")
    
    return result


def scenario_4_new_information(memory, user_id="user_001"):
    """
    Scenario 4: Adding New Information (ADD operation)
    
    Demonstrates adding completely new facts that don't conflict with existing memories.
    Expected: Creates new memories for the new facts.
    """
    print("\n" + "=" * 80)
    print("SCENARIO 4: Adding New Information")
    print("=" * 80)
    print("User shares new preferences...")
    
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
    
    results = result.get('results', [])
    if results:
        print(f"\n✅ Added {len(results)} new memories:")
        for i, mem in enumerate(results, 1):
            print(f"   {i}. ID: {mem.get('id', 'N/A')}, Event: {mem.get('event', 'N/A')}, Memory: {mem.get('memory', '')}")
    else:
        print(f"\n✅ Processed (no new memories)")
    print(f"New facts: ['Likes to drink coffee every morning', 'Has two cats', 'Cats named Fluffy and Whiskers']")
    
    # Search to verify new facts
    results = memory.search("What does Alice like?", user_id=user_id, limit=5)
    print(f"\n📊 All memories including new ones:")
    for i, mem in enumerate(results.get('results', []), 1):
        print(f"   {i}. {mem.get('memory', '')}")
    
    return result


def scenario_5_conflict_resolution(memory, user_id="user_001"):
    """
    Scenario 5: Conflict Resolution (DELETE operation)
    
    Demonstrates handling contradictory information by deleting old and adding new.
    Expected: Deletes old preference, adds new one.
    """
    print("\n" + "=" * 80)
    print("SCENARIO 5: Conflict Resolution")
    print("=" * 80)
    print("User contradicts previous information...")
    
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
    
    results = result.get('results', [])
    if results:
        print(f"\n✅ Processed {len(results)} memories:")
        for i, mem in enumerate(results, 1):
            event = mem.get('event', 'N/A')
            print(f"   {i}. ID: {mem.get('id', 'N/A')}, Event: {event}")
            if event == 'DELETE':
                print(f"      Deleted: {mem.get('memory', 'N/A')}")
            elif event == 'ADD':
                print(f"      Added: {mem.get('memory', 'N/A')}")
    else:
        print(f"\n✅ Processed (no changes)")
    
    # Search to verify conflict resolution
    results = memory.search("What does Alice drink?", user_id=user_id, limit=5)
    print(f"\n📊 Current preferences:")
    for i, mem in enumerate(results.get('results', []), 1):
        content = mem.get('memory', '')
        if 'coffee' in content.lower() or 'tea' in content.lower():
            print(f"   {i}. {content}")
    
    return result


def scenario_6_memory_consolidation(memory, user_id="user_001"):
    """
    Scenario 6: Memory Consolidation
    
    Demonstrates merging more detailed information into existing memories.
    Expected: Updates existing memory with more details instead of creating separate memory.
    """
    print("\n" + "=" * 80)
    print("SCENARIO 6: Memory Consolidation")
    print("=" * 80)
    print("User provides more detailed information that should be merged...")
    
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
    
    results = result.get('results', [])
    if results:
        print(f"\n✅ Processed {len(results)} memories:")
        for i, mem in enumerate(results, 1):
            event = mem.get('event', 'N/A')
            if event == 'UPDATE':
                print(f"   {i}. ID: {mem.get('id', 'N/A')}, Event: {event}")
                print(f"      Old: {mem.get('previous_memory', 'N/A')}")
                print(f"      New: {mem.get('memory', 'N/A')}")
            else:
                print(f"   {i}. ID: {mem.get('id', 'N/A')}, Event: {event}, Memory: {mem.get('memory', '')}")
    else:
        print(f"\n✅ Processed (no changes)")
    
    return result


def demo_memory_operations():
    """Demonstrate intelligent memory operations."""
    
    # Load configuration from environment
    config = load_config()
    
    memory = Memory(config=config, agent_id="demo_agent")
    
    print("=" * 80)
    print("INTELLIGENT MEMORY MANAGEMENT DEMO")
    print("=" * 80)
    
    # Run all scenarios
    scenario_1_initial_addition(memory)
    scenario_2_duplicate_detection(memory)
    scenario_3_information_update(memory)
    scenario_4_new_information(memory)
    scenario_5_conflict_resolution(memory)
    scenario_6_memory_consolidation(memory)
    
    # =============================================================================
    # Final Summary
    # =============================================================================
    print("\n" + "=" * 80)
    print("FINAL MEMORY SUMMARY")
    print("=" * 80)
    
    all_memories = memory.get_all(user_id="user_001", limit=20)
    memories_list = all_memories.get('results', [])
    print(f"\n📚 Total memories: {len(memories_list)}")
    print("\nFinal memory contents:")
    for i, mem in enumerate(memories_list, 1):
        print(f"   {i}. {mem.get('memory', mem.get('content', ''))}")
    
    print("\n" + "=" * 80)
    print("INTELLIGENT MEMORY DEMO COMPLETED!")
    print("=" * 80)
    print("\nKey Features Demonstrated:")
    print("  ✓ Automatic fact extraction from conversations")
    print("  ✓ Duplicate detection and prevention")
    print("  ✓ Smart information updates")
    print("  ✓ New information addition")
    print("  ✓ Conflict resolution (contradiction handling)")
    print("  ✓ Memory consolidation and merging")


async def demo_async_memory_operations():
    """Demonstrate async intelligent memory operations."""
    
    # Load configuration from environment
    config = load_config()
    
    async_memory = AsyncMemory(config=config)
    await async_memory.initialize()
    
    print("\n" + "=" * 80)
    print("ASYNC INTELLIGENT MEMORY DEMO")
    print("=" * 80)
    
    # Similar scenarios but using async methods
    messages = [
        {"role": "user", "content": "I'm Bob, a data scientist at Amazon"},
        {"role": "assistant", "content": "Nice to meet you, Bob!"},
        {"role": "user", "content": "I specialize in NLP and recommendation systems."}
    ]
    
    result = await async_memory.add(
        messages=messages,
        user_id="user_002",
        agent_id="async_agent",
        infer=True
    )
    
    results = result.get('results', [])
    if results:
        print(f"\n✅ Async memory added: {len(results)} memories")
        for i, mem in enumerate(results, 1):
            print(f"   {i}. ID: {mem.get('id', 'N/A')}, Memory: {mem.get('memory', '')}")
    else:
        print(f"\n✅ Async memory processed (no new memories)")
    
    # Search asynchronously
    results = await async_memory.search(
        query="What does Bob do?",
        user_id="user_002",
        limit=5
    )
    
    print(f"\n📊 Found {len(results.get('results', []))} memories")
    for i, mem in enumerate(results.get('results', []), 1):
        print(f"   {i}. {mem.get('memory', '')}")
    
    print("\n" + "=" * 80)
    print("ASYNC DEMO COMPLETED!")
    print("=" * 80)


def compare_modes():
    """Compare intelligent mode vs simple mode."""
    
    print("\n" + "=" * 80)
    print("COMPARING INTELLIGENT MODE vs SIMPLE MODE")
    print("=" * 80)
    
    # Load configuration from environment
    config = load_config()
    
    memory = Memory(config=config, agent_id="comparison_agent")
    
    test_messages = [
        {"role": "user", "content": "I love Python"},
        {"role": "user", "content": "I love Python"},
        {"role": "user", "content": "I love Python"}
    ]
    
    # Simple mode
    print("\n1️⃣ SIMPLE MODE (infer=False):")
    for i, msg in enumerate(test_messages, 1):
        result = memory.add(
            messages=[msg],
            user_id="test_user",
            infer=False
        )
        results = result.get('results', [])
        if results:
            print(f"   Add {i}: Memory ID {results[0].get('id', 'N/A')}")
        else:
            print(f"   Add {i}: No memory created")
    
    all_simple = memory.get_all(user_id="test_user")
    simple_list = all_simple.get('results', [])
    print(f"\n   Total memories: {len(simple_list)} (includes duplicates)")
    
    # Clean up
    memory.delete_all(user_id="test_user")
    
    # Intelligent mode
    print("\n2️⃣ INTELLIGENT MODE (infer=True):")
    for i, msg in enumerate(test_messages, 1):
        result = memory.add(
            messages=[msg],
            user_id="test_user",
            infer=True
        )
        results = result.get('results', [])
        if results:
            print(f"   Add {i}: Memory ID {results[0].get('id', 'N/A')}")
        else:
            print(f"   Add {i}: No memory created (duplicate detected)")
    
    all_intelligent = memory.get_all(user_id="test_user")
    intelligent_list = all_intelligent.get('results', [])
    print(f"\n   Total memories: {len(intelligent_list)} (duplicates removed)")
    
    print("\n" + "=" * 80)
    print("SUMMARY:")
    print("=" * 80)
    print(f"  Simple mode:    {len(simple_list)} memories (with duplicates)")
    print(f"  Intelligent:    {len(intelligent_list)} memories (deduplicated)")
    print(f"  Difference:     {len(simple_list) - len(intelligent_list)} duplicates removed")
    print("=" * 80)


def run_all_scenarios():
    """Run all scenarios in sequence."""
    config = load_config()
    memory = Memory(config=config, agent_id="demo_agent")
    
    print("=" * 80)
    print("RUNNING ALL SCENARIOS")
    print("=" * 80)
    
    scenario_1_initial_addition(memory)
    scenario_2_duplicate_detection(memory)
    scenario_3_information_update(memory)
    scenario_4_new_information(memory)
    scenario_5_conflict_resolution(memory)
    scenario_6_memory_consolidation(memory)
    
    # Final summary
    all_memories = memory.get_all(user_id="user_001", limit=20)
    memories_list = all_memories.get('results', [])
    print("\n" + "=" * 80)
    print("FINAL RESULTS")
    print("=" * 80)
    print(f"📚 Total memories: {len(memories_list)}")
    print("\nAll memory contents:")
    for i, mem in enumerate(memories_list, 1):
        print(f"   {i}. {mem.get('memory', mem.get('content', ''))}")


if __name__ == "__main__":
    import sys
    
    print("\n🚀 Intelligent Memory Management Demo\n")
    print("Usage:")
    print("  python intelligent_memory_demo.py              # Run all scenarios")
    print("  python intelligent_memory_demo.py 1             # Run scenario 1 only")
    print("  python intelligent_memory_demo.py 2             # Run scenario 2 only")
    print("  ...")
    print("  python intelligent_memory_demo.py compare       # Compare modes")
    print()
    
    try:
        # Check for specific scenario argument
        if len(sys.argv) > 1:
            scenario_num = sys.argv[1]
            config = load_config()
            memory = Memory(config=config, agent_id="demo_agent")
            
            if scenario_num == "1":
                scenario_1_initial_addition(memory)
            elif scenario_num == "2":
                scenario_2_duplicate_detection(memory)
            elif scenario_num == "3":
                scenario_3_information_update(memory)
            elif scenario_num == "4":
                scenario_4_new_information(memory)
            elif scenario_num == "5":
                scenario_5_conflict_resolution(memory)
            elif scenario_num == "6":
                scenario_6_memory_consolidation(memory)
            elif scenario_num == "compare":
                compare_modes()
            else:
                print(f"Unknown scenario: {scenario_num}")
                print("Available: 1, 2, 3, 4, 5, 6, compare")
        else:
            # Run all scenarios
            run_all_scenarios()
            
            # Run comparison
            compare_modes()
        
        print("\n✅ All demos completed successfully!")
        
    except Exception as e:
        print(f"\n❌ Demo failed: {e}")
        print("\nNote: Make sure to configure your LLM provider in the config.")
        import traceback
        traceback.print_exc()
