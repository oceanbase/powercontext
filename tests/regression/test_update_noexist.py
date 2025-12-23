"""
Run `python docs/examples/re_update_noexist.py` to test error handling for non-existent memory IDs.
"""
import os
from typing import Any, Dict, Optional
from dotenv import load_dotenv
from powermem import create_memory

# Check if .env exists and load it
env_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
env_example_path = os.path.join(os.path.dirname(__file__), "..", "..", "env.example")

if not os.path.exists(env_path):
    print(f"\n No .env file found at: {env_path}")
    print(f"To add your API keys:")
    print(f"   1. Copy: cp {env_example_path} {env_path}")
    print(f"   2. Edit {env_path} and add your API keys")
    print(f"\n  For now, using mock providers for demonstration...")
else:
    print(f"Found .env file")
    # Explicitly load configs/.env file
    load_dotenv(env_path, override=True)


def _print_banner(title: str) -> None:
    print("\n" + "=" * 60)
    print(title)
    print("=" * 60)


def _print_step(title: str) -> None:
    print("\n" + "-" * 60)
    print(title)
    print("-" * 60)


def _get_results_list(result: Any) -> list[Dict[str, Any]]:
    if isinstance(result, dict):
        results = result.get("results")
        if isinstance(results, list):
            return results
    return []


def _safe_delete_all(memory, *, user_id: Optional[str] = None) -> None:
    try:
        memory.delete_all(user_id=user_id)
    except Exception:
        pass


def test_get_non_existent_memory() -> None:
    """Test getting non-existent memory"""
    _print_step("Test 1: Get Non-existent Memory")
    memory = create_memory()
    user_id = "user_test_get"
    _safe_delete_all(memory, user_id=user_id)

    # Test with a large non-existent ID
    print("Testing get with non-existent memory_id (99999)...")
    result = memory.get(memory_id=99999, user_id=user_id)
    if result is None:
        print("✓ Non-existent memory returns None (expected)")
    else:
        print(f"✗ Unexpected: Got result: {result}")

    # Test with negative ID
    print("\nTesting get with negative memory_id (-1)...")
    result = memory.get(memory_id=-1, user_id=user_id)
    if result is None:
        print("✓ Negative ID returns None (expected)")
    else:
        print(f"✗ Unexpected: Got result: {result}")

    # Test with zero ID
    print("\nTesting get with zero memory_id (0)...")
    result = memory.get(memory_id=0, user_id=user_id)
    if result is None:
        print("✓ Zero ID returns None (expected)")
    else:
        print(f"✗ Unexpected: Got result: {result}")

    # Test with a very large ID
    print("\nTesting get with very large memory_id (999999999)...")
    result = memory.get(memory_id=999999999, user_id=user_id)
    if result is None:
        print("✓ Very large ID returns None (expected)")
    else:
        print(f"✗ Unexpected: Got result: {result}")

    _safe_delete_all(memory, user_id=user_id)


def test_update_non_existent_memory() -> None:
    """Test updating non-existent memory"""
    _print_step("Test 2: Update Non-existent Memory")
    memory = create_memory()
    user_id = "user_test_update"
    _safe_delete_all(memory, user_id=user_id)

    # Test updating non-existent memory with large ID
    print("Testing update with non-existent memory_id (99999)...")
    try:
        result = memory.update(memory_id=99999, content="New content", user_id=user_id)
        if result is None:
            print("✓ Update returns None for non-existent ID (expected)")
        else:
            print(f"  Note: Update returned result: {result}")
    except Exception as e:
        print(f"✓ Update raised exception for non-existent ID: {type(e).__name__}: {e}")

    # Test updating with negative ID
    print("\nTesting update with negative memory_id (-1)...")
    try:
        result = memory.update(memory_id=-1, content="New content", user_id=user_id)
        if result is None:
            print("✓ Update returns None for negative ID (expected)")
        else:
            print(f"  Note: Update returned result: {result}")
    except Exception as e:
        print(f"✓ Update raised exception for negative ID: {type(e).__name__}: {e}")

    # Test updating with zero ID
    print("\nTesting update with zero memory_id (0)...")
    try:
        result = memory.update(memory_id=0, content="New content", user_id=user_id)
        if result is None:
            print("✓ Update returns None for zero ID (expected)")
        else:
            print(f"  Note: Update returned result: {result}")
    except Exception as e:
        print(f"✓ Update raised exception for zero ID: {type(e).__name__}: {e}")

    # Test updating with empty content (should raise ValueError)
    print("\nTesting update with empty content...")
    try:
        # First create a memory to get a valid ID
        add_result = memory.add("Test content", user_id=user_id)
        memory_id = None
        if isinstance(add_result, dict):
            memory_id = add_result.get("id") or add_result.get("memory_id")
        if memory_id:
            try:
                memory.update(memory_id=memory_id, content="", user_id=user_id)
                print("✗ Update with empty content should raise ValueError")
            except ValueError as e:
                print(f"✓ Update with empty content raised ValueError (expected): {e}")
            except Exception as e:
                print(f"  Note: Update raised different exception: {type(e).__name__}: {e}")
    except Exception as e:
        print(f"  Note: Failed to create test memory: {e}")

    _safe_delete_all(memory, user_id=user_id)


def test_delete_non_existent_memory() -> None:
    """Test deleting non-existent memory"""
    _print_step("Test 3: Delete Non-existent Memory")
    memory = create_memory()
    user_id = "user_test_delete"
    _safe_delete_all(memory, user_id=user_id)

    # Test deleting non-existent memory with large ID
    print("Testing delete with non-existent memory_id (99999)...")
    try:
        success = memory.delete(memory_id=99999, user_id=user_id)
        if not success:
            print("✓ Delete returns False for non-existent memory (expected)")
        else:
            print("  Note: Delete returned True (may be idempotent)")
    except Exception as e:
        print(f"  Note: Exception raised: {type(e).__name__}: {e}")

    # Test deleting with negative ID
    print("\nTesting delete with negative memory_id (-1)...")
    try:
        success = memory.delete(memory_id=-1, user_id=user_id)
        if not success:
            print("✓ Delete returns False for negative ID (expected)")
        else:
            print("  Note: Delete returned True")
    except Exception as e:
        print(f"  Note: Exception raised: {type(e).__name__}: {e}")

    # Test deleting with zero ID
    print("\nTesting delete with zero memory_id (0)...")
    try:
        success = memory.delete(memory_id=0, user_id=user_id)
        if not success:
            print("✓ Delete returns False for zero ID (expected)")
        else:
            print("  Note: Delete returned True")
    except Exception as e:
        print(f"  Note: Exception raised: {type(e).__name__}: {e}")

    # Test deleting same non-existent ID multiple times (idempotency)
    print("\nTesting delete idempotency (delete same non-existent ID twice)...")
    try:
        success1 = memory.delete(memory_id=88888, user_id=user_id)
        success2 = memory.delete(memory_id=88888, user_id=user_id)
        print(f"  First delete returned: {success1}")
        print(f"  Second delete returned: {success2}")
        if success1 == success2:
            print("✓ Delete is idempotent (consistent behavior)")
        else:
            print("  Note: Delete behavior differs between calls")
    except Exception as e:
        print(f"  Note: Exception raised: {type(e).__name__}: {e}")

    _safe_delete_all(memory, user_id=user_id)


def test_get_update_delete_workflow() -> None:
    """Test complete get/update/delete workflow"""
    _print_step("Test 4: Get/Update/Delete Workflow with Non-existent IDs")
    memory = create_memory()
    user_id = "user_test_workflow"
    _safe_delete_all(memory, user_id=user_id)

    # Create a real memory first
    print("Creating a real memory for comparison...")
    add_result = memory.add("Real memory content", user_id=user_id)
    real_memory_id = None
    if isinstance(add_result, dict):
        real_memory_id = add_result.get("id") or add_result.get("memory_id")
    
    if real_memory_id:
        print(f"✓ Created memory with ID: {real_memory_id}")
        
        # Verify we can get the real memory
        real_result = memory.get(memory_id=real_memory_id, user_id=user_id)
        if real_result:
            print(f"✓ Successfully retrieved real memory: {real_result.get('content', 'N/A')[:50]}...")
        
        # Now test with a non-existent ID that's close to the real one
        fake_id = real_memory_id + 1000
        print(f"\nTesting operations with fake ID ({fake_id}) close to real ID ({real_memory_id})...")
        
        # Get fake memory
        fake_result = memory.get(memory_id=fake_id, user_id=user_id)
        if fake_result is None:
            print(f"✓ Get fake ID returns None")
        else:
            print(f"✗ Unexpected: Got result for fake ID")
        
        # Update fake memory
        try:
            update_result = memory.update(memory_id=fake_id, content="Fake update", user_id=user_id)
            if update_result is None:
                print(f"✓ Update fake ID returns None")
            else:
                print(f"  Note: Update fake ID returned: {update_result}")
        except Exception as e:
            print(f"✓ Update fake ID raised exception: {type(e).__name__}")
        
        # Delete fake memory
        delete_success = memory.delete(memory_id=fake_id, user_id=user_id)
        if not delete_success:
            print(f"✓ Delete fake ID returns False")
        else:
            print(f"  Note: Delete fake ID returned True")
    
    _safe_delete_all(memory, user_id=user_id)


def test_error_handling() -> None:
    """Test error handling: get/update/delete operations with invalid IDs, and handling of empty queries"""
    _print_step("Test 6: Error Handling Summary")
    memory = create_memory()
    user_id = "user_error_handling"
    _safe_delete_all(memory, user_id=user_id)

    # Test getting non-existent memory
    print("Testing get with non-existent memory_id...")
    try:
        result = memory.get(memory_id=99999, user_id=user_id)
        if result is None or not result:
            print("✓ Non-existent memory returns None/empty")
        else:
            print(f"  Note: Got result: {result}")
    except Exception as e:
        print(f"  Note: Exception raised: {e}")

    # Test updating non-existent memory
    print("\nTesting update with non-existent memory_id...")
    try:
        result = memory.update(memory_id=99999, content="New content", user_id=user_id)
        if result is None:
            print("✓ Update returns None for non-existent ID")
        else:
            print(f"  Note: Update returned: {result}")
    except Exception as e:
        print(f"✓ Update raised exception for non-existent ID: {type(e).__name__}: {e}")

    # Test deleting non-existent memory
    print("\nTesting delete with non-existent memory_id...")
    try:
        success = memory.delete(memory_id=99999, user_id=user_id)
        if not success:
            print("✓ Delete returns False for non-existent memory")
        else:
            print("  Note: Delete returned True (may be idempotent)")
    except Exception as e:
        print(f"  Note: Exception raised: {e}")

    # Test search with empty query
    print("\nTesting search with empty query...")
    memory.add("Some content", user_id=user_id)
    try:
        results = memory.search(query="", user_id=user_id)
        results_list = list(_get_results_list(results))
        print(f"✓ Empty query handled, returned {len(results_list)} results")
    except Exception as e:
        print(f"  Note: Exception raised: {e}")

    _safe_delete_all(memory, user_id=user_id)


def main() -> None:
    """Run all tests"""
    _print_banner("Non-existent Memory ID Operations Test Suite")
    print("This script tests the behavior of get/update/delete operations")
    print("on non-existent memory IDs to verify error handling.\n")

    try:
        test_get_non_existent_memory()
        test_update_non_existent_memory()
        test_delete_non_existent_memory()
        test_get_update_delete_workflow()
        test_error_handling()
        
        _print_banner("All Tests Completed Successfully!")
        print("✓ All test cases have been executed.")
        print("Review the output above to verify expected behavior.")
        
    except Exception as e:
        print(f"\n✗ Test suite failed with error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
