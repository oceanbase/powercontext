"""
Test script for UserMemory functionality

This script tests the main features of UserMemory:
1. Initialization
2. Adding conversations and extracting profiles
3. Searching memories with optional profile inclusion
4. Getting user profiles

This script is modularized with separate functions for each test feature.
"""
import os
import logging
import argparse

import pytest
import sys
sys.path.append(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
from powermem import UserMemory
from powermem import auto_config

def init_user_memory(config=None, agent_id="test_agent"):
    """
    Initialize UserMemory instance
    
    Args:
        config: Optional configuration dictionary. If None, uses auto_config()
        agent_id: Agent ID for the UserMemory instance
    
    Returns:
        UserMemory instance
    """
    if config is None:
        config = auto_config()
    
    user_memory = UserMemory(config=config, agent_id=agent_id)
    print("✓ UserMemory initialized successfully")
    return user_memory


@pytest.fixture(scope="session")
def user_memory():
    """Session-scoped fixture providing a shared UserMemory instance for all tests."""
    config = auto_config()
    um = UserMemory(config=config, agent_id="test_agent")
    yield um
    # Cleanup after all tests complete
    try:
        um.delete_all(user_id="user_001", agent_id="test_agent", delete_profile=True)
        print(f"\n✓ Cleaned up all test data for user: user_001")
    except Exception as e:
        print(f"\n⚠ Could not cleanup test data: {str(e)[:100]}")


@pytest.mark.skip(reason="Helper function, not meant to be run directly by pytest")
def test_add_conversation(user_memory, conversation, user_id="user_001", agent_id="test_agent", test_name="Add conversation and extract profile"):
    """
    Test adding conversation and extracting profile
    Args:
        user_memory: UserMemory instance
        conversation: List of conversation messages (dict with 'role' and 'content')
        user_id: User ID
        agent_id: Agent ID
        test_name: Test name for display
    
    Returns:
        Result dictionary from add operation
    """
    print(f"\n=== {test_name} ===")
    
    result = user_memory.add(
        messages=conversation,
        user_id=user_id,
        agent_id=agent_id,
    )
    
    print(f"✓ Conversation added successfully")
    print(f"  - Profile extracted: {result.get('profile_extracted', False)}")
    if result.get('profile_content'):
        print(f"  - Profile content: {result['profile_content']}")
    print(f"  - Memory results count: {len(result.get('results', []))}")
    
    return result


@pytest.mark.skip(reason="Helper function, not meant to be run directly by pytest")
def test_get_profile(user_memory, user_id="user_001", agent_id=None, run_id=None, test_name="Get user profile"):
    """
    Test getting user profile
    
    Args:
        user_memory: UserMemory instance
        user_id: User ID
        agent_id: Optional agent ID
        run_id: Optional run ID
        test_name: Test name for display
    
    Returns:
        Profile dictionary or None
    """
    print(f"\n=== {test_name} ===")
    
    profile = user_memory.profile(
        user_id=user_id,
    )
    
    if profile:
        print(f"✓ Profile retrieved successfully")
        print(f"  - Profile ID: {profile.get('id')}")
        print(f"  - User ID: {profile.get('user_id')}")
        print(f"  - Profile content: {profile.get('profile_content', '')}")
        print(f"  - Created at: {profile.get('created_at')}")
        print(f"  - Updated at: {profile.get('updated_at')}")
    else:
        print("✗ No profile found")
    
    return profile


@pytest.mark.skip(reason="Helper function, not meant to be run directly by pytest")
def test_delete_all(user_memory, user_id="user_001", agent_id="test_agent", run_id=None, delete_profile=True, test_name="Delete all memories and profile"):
    """
    Test deleting all memories and profile
    
    Args:
        user_memory: UserMemory instance
        user_id: User ID
        agent_id: Agent ID
        run_id: Optional run ID
        delete_profile: Whether to delete profile
        test_name: Test name for display
    
    Returns:
        True if deleted successfully, False otherwise
    """
    print(f"\n=== {test_name} ===")
    
    result = user_memory.delete_all(
        user_id=user_id,
        agent_id=agent_id,
        run_id=run_id,
        delete_profile=delete_profile
    )
    
    print(f"✓ Deleted all memories and profile: {result}")
    return result


@pytest.mark.skip(reason="Helper function, not meant to be run directly by pytest")
def test_search_memories(user_memory, user_id="user_001", agent_id="test_agent", query_without_profile="", query_with_profile="", limit=5):
    """
    Test searching memories with and without profile
    
    Args:
        user_memory: UserMemory instance
        user_id: User ID for search
        agent_id: Agent ID for search
        query_without_profile: Query string for search without profile
        query_with_profile: Query string for search with profile
        limit: Maximum number of results to return
    """
    print("\n=== Search memories without profile ===")
    search_result = user_memory.search(
        query=query_without_profile,
        user_id=user_id,
        agent_id=agent_id,
        limit=limit,
        add_profile=False
    )

    print(f"✓ Search completed")
    print(f"  - Results count: {len(search_result.get('results', []))}")
    print(f"  - Profile included: {'profile' in search_result}")

    if search_result.get('results'):
        for i, res in enumerate(search_result['results'], 1):
            print(f"  - Result {i}: {res.get('memory', '')}")

    print("\n=== Search memories with profile ===")
    search_result_with_profile = user_memory.search(
        query=query_with_profile,
        user_id=user_id,
        agent_id=agent_id,
        limit=limit,
        add_profile=True
    )

    print(f"✓ Search with profile completed")
    print(f"  - Results count: {len(search_result_with_profile.get('results', []))}")
    print(f"  - Profile included: {'profile_content' in search_result_with_profile}")


def test_user_memory(user_memory):
    """Test UserMemory functionality with modular test functions"""
    
    # Clean up before starting tests
    test_delete_all(user_memory, user_id="user_001", agent_id="test_agent", delete_profile=True, test_name="Test 0: Clean up before tests")

    # Test 1: Add conversation and extract profile
    conversation1 = [
        {"role": "user",
        "content": "I am Wang Tao, a 35-year-old history professor at Peking University, from the ancient capital Xi'an, and in my leisure time, I love reading history books the most."},
        {"role": "assistant",
        "content": "Nice to meet you, Wang Tao! That's great to hear about your work at Peking University and your passion for history books. Your background in Xi'an must give you unique insights into Chinese history."},
        {"role": "user",
        "content": "Actually, I've made some changes recently. I now work at OceanBase as a software engineer, and I've shifted my hobby from reading books to playing basketball."},
        {"role": "assistant",
        "content": "That's quite a career transition! Moving from being a history professor to a software engineer at OceanBase is a significant change. And it's interesting that you've also shifted your interests from reading history books to playing basketball. Both are great ways to stay active and engaged. How are you enjoying your new role and hobby?"}
    ]
    test_add_conversation(user_memory, conversation1, user_id="user_001", agent_id="test_agent", test_name="Test 1: Add conversation and extract profile")

    # Test 2: Get user profile
    test_get_profile(user_memory, user_id="user_001", test_name="Test 2: Get user profile")

    # Test 3: Search memories
    test_search_memories(user_memory, user_id="user_001", agent_id="test_agent", query_without_profile="", query_with_profile="hobbies", limit=10)

    # Test 4: Delete all memories and profile
    test_delete_all(user_memory, user_id="user_001", agent_id="test_agent", delete_profile=True, test_name="Test 4: Delete all memories and profile")

    # Test 5: Add another conversation
    conversation2 = [
        {"role": "user",
        "content": "I am Wang Tao, a 35-year-old history professor at Peking University, from the ancient capital Xi'an. I'm married and live near the campus. In my leisure time, I love reading history books the most, and I used to spend hours writing in the Peking University Library."},
        {"role": "assistant", 
        "content": "Nice to meet you, Wang Tao! That's great to hear about your work and hobbies."}
    ]
    test_add_conversation(user_memory, conversation2, user_id="user_001", agent_id="test_agent", test_name="Test 5: Add conversation and extract profile")

    # Test 6: Search memories again
    test_search_memories(user_memory, user_id="user_001", agent_id="test_agent", query_without_profile="", query_with_profile="hobbies", limit=10)

    # Test 7: Delete all memories and profile again
    test_delete_all(user_memory, user_id="user_001", agent_id="test_agent", delete_profile=True, test_name="Test 7: Delete all memories and profile")

    # Test 8: Search after deletion (should return empty)
    test_search_memories(user_memory, user_id="user_001", agent_id="test_agent", query_without_profile="", query_with_profile="hobbies", limit=10)

    print("\n=== All tests completed ===")


if __name__ == "__main__":
    try:
        test_user_memory()

    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        import traceback

        traceback.print_exc()

