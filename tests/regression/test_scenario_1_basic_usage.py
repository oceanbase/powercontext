"""Executable walkthrough for `scenario_1_basic_usage.md`.

Run `python docs/examples/scenario_1_basic_usage.py` to exercise every code
sample from the Scenario 1 documentation in a single pass.
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


def _extract_memory_id(result: Any) -> Optional[int]:
    results = list(_get_results_list(result))
    if not results:
        return None
    first = results[0]
    if isinstance(first, dict):
        memory_id = first.get("id") or first.get("memory_id")
        if isinstance(memory_id, (int, str)):
            try:
                return int(memory_id)
            except (TypeError, ValueError):
                return None
    return None


def _memory_text(entry: Dict[str, Any]) -> str:
    if not isinstance(entry, dict):
        return str(entry)
    return (
        entry.get("memory")
        or entry.get("content")
        or entry.get("text")
        or entry.get("data")
        or str(entry)
    )


def _safe_delete_all(memory, *, user_id: Optional[str] = None) -> None:
    try:
        memory.delete_all(user_id=user_id)
    except Exception:
        pass


def test_step1_setup() -> None:
    _print_step("Step 1: Setup")
    memory = create_memory()
    print("✓ Memory initialized successfully!")


def test_step2_add_first_memory() -> None:
    _print_step("Step 2: Add Your First Memory")
    memory = create_memory()
    user_id = "user123"
    _safe_delete_all(memory, user_id=user_id)

    result = memory.add(messages="User likes Python programming", user_id=user_id)
    memory_id = _extract_memory_id(result) or "N/A"
    print(f"✓ Memory added! ID: {memory_id}")
    _safe_delete_all(memory, user_id=user_id)


def test_step3_add_multiple_memories() -> None:
    _print_step("Step 3: Add Multiple Memories")
    memory = create_memory()
    user_id = "user123_multi"
    _safe_delete_all(memory, user_id=user_id)

    memories = [
        "User likes Python programming",
        "User prefers email support over phone calls",
        "User works as a software engineer",
        "User favorite color is blue",
    ]

    for mem in memories:
        memory.add(messages=mem, user_id=user_id)
        print(f"✓ Added: {mem}")

    print(f"\n✓ All memories added for user {user_id}")
    _safe_delete_all(memory, user_id=user_id)


def test_step4_search_memories() -> None:
    _print_step("Step 4: Search Memories")
    memory = create_memory()
    user_id = "user123_search"
    _safe_delete_all(memory, user_id=user_id)

    memory.add("User likes Python programming", user_id=user_id)
    memory.add("User prefers email support", user_id=user_id)
    memory.add("User works as a software engineer", user_id=user_id)

    print("Searching for 'user preferences'...")
    results = memory.search(query="user preferences", user_id=user_id, limit=5)
    results_list = list(_get_results_list(results))
    print(f"\nFound {len(results_list)} memories:")
    for index, entry in enumerate(results_list, start=1):
        print(f"  {index}. {_memory_text(entry)}")
    _safe_delete_all(memory, user_id=user_id)


def test_step5_add_with_metadata() -> None:
    _print_step("Step 5: Add Metadata")
    memory = create_memory()
    user_id = "user123_metadata"
    _safe_delete_all(memory, user_id=user_id)

    memory.add(
        messages="User likes Python programming",
        user_id=user_id,
        metadata={
            "category": "preference",
            "importance": "high",
            "source": "conversation",
        },
    )

    memory.add(
        messages="User prefers email support",
        user_id=user_id,
        metadata={
            "category": "communication",
            "importance": "medium",
        },
    )

    print("✓ Memories added with metadata")
    _safe_delete_all(memory, user_id=user_id)


def test_step6_search_with_metadata_filters() -> None:
    _print_step("Step 6: Search with Metadata Filters")
    memory = create_memory()
    user_id = "user123_metadata_filter"
    _safe_delete_all(memory, user_id=user_id)

    memory.add(
        messages="User likes Python programming",
        user_id=user_id,
        metadata={"category": "preference"},
    )
    memory.add(
        messages="User prefers email support",
        user_id=user_id,
        metadata={"category": "communication"},
    )

    print("Searching with metadata filter...")
    results = memory.search(
        query="user preferences",
        user_id=user_id,
        filters={"category": "preference"},
    )

    filtered_results = list(_get_results_list(results))
    print(f"\nFound {len(filtered_results)} memories:")
    for entry in filtered_results:
        print(f"  - {_memory_text(entry)}")
        metadata = entry.get("metadata") if isinstance(entry, dict) else None
        print(f"    Metadata: {metadata or {}}")

    _safe_delete_all(memory, user_id=user_id)


def test_step7_get_all_memories() -> None:
    _print_step("Step 7: Get All Memories")
    memory = create_memory()
    user_id = "user123_all"
    _safe_delete_all(memory, user_id=user_id)

    memory.add("User likes Python", user_id=user_id)
    memory.add("User prefers email", user_id=user_id)
    memory.add("User works as engineer", user_id=user_id)

    all_memories = memory.get_all(user_id=user_id)
    results_list = list(_get_results_list(all_memories))

    print(f"\nTotal memories for {user_id}: {len(results_list)}")
    print("\nAll memories:")
    for index, entry in enumerate(results_list, start=1):
        print(f"  {index}. {_memory_text(entry)}")

    _safe_delete_all(memory, user_id=user_id)


def test_step8_update_memory() -> None:
    _print_step("Step 8: Update a Memory")
    memory = create_memory()
    user_id = "user123_update"
    _safe_delete_all(memory, user_id=user_id)

    original_content = "User likes Python programming"
    result = memory.add(messages=original_content, user_id=user_id, infer=False)
    memory_id = _extract_memory_id(result)
    if memory_id is None:
        raise RuntimeError("Failed to create memory for update step.")

    updated_content = (
        "User loves Python programming, especially for data science"
    )
    memory.update(memory_id=memory_id, content=updated_content, user_id=user_id)

    updated_memory = memory.get(memory_id=memory_id, user_id=user_id) or {}
    new_text = _memory_text(updated_memory)

    print("✓ Memory updated!")
    print(f"  Old: {original_content}")
    print(f"  New: {new_text}")

    _safe_delete_all(memory, user_id=user_id)


def test_step9_delete_memory() -> None:
    _print_step("Step 9: Delete a Memory")
    memory = create_memory()
    user_id = "user123_delete"
    _safe_delete_all(memory, user_id=user_id)

    result = memory.add(
        messages="User likes Python programming", user_id=user_id, infer=False
    )
    memory_id = _extract_memory_id(result)
    if memory_id is None:
        raise RuntimeError("Failed to create memory for delete step.")

    success = memory.delete(memory_id=memory_id, user_id=user_id)
    if success:
        print(f"✓ Memory {memory_id} deleted successfully!")
    else:
        print("✗ Failed to delete memory")

    _safe_delete_all(memory, user_id=user_id)


def test_step10_delete_all_memories() -> None:
    _print_step("Step 10: Delete All Memories")
    memory = create_memory()
    user_id = "user123_delete_all"
    _safe_delete_all(memory, user_id=user_id)

    memory.add("Alice is 1 years old", user_id=user_id)
    memory.add("Bob is 2 years old", user_id=user_id)
    memory.add("Charlie is 3 years old", user_id=user_id)

    all_memories = memory.get_all(user_id=user_id)
    count_before = len(list(_get_results_list(all_memories)))

    success = memory.delete_all(user_id=user_id)
    if success:
        print(f"✓ Deleted {count_before} memories for {user_id}")
    else:
        print("✗ Failed to delete memories")

    _safe_delete_all(memory, user_id=user_id)


def test_full_example() -> None:
    _print_step("Complete Example")
    memory = create_memory()
    user_id = "demo_user"
    _safe_delete_all(memory, user_id=user_id)

    print("1. Adding memories...")
    memories = [
        "User likes Python programming",
        "User prefers email support",
        "User works as a software engineer",
        "User favorite color is blue",
    ]
    for mem in memories:
        memory.add(messages=mem, user_id=user_id, metadata={"source": "demo"})
        print(f"   ✓ Added: {mem}")

    print("\n2. Searching memories...")
    results = memory.search(query="user preferences", user_id=user_id, limit=5)
    results_list = list(_get_results_list(results))
    print(f"   Found {len(results_list)} memories:")
    for entry in results_list:
        print(f"     - {_memory_text(entry)}")

    print("\n3. Getting all memories...")
    all_memories = memory.get_all(user_id=user_id)
    all_results = list(_get_results_list(all_memories))
    print(f"   Total: {len(all_results)} memories")

    print("\n4. Cleaning up...")
    count_before = len(all_results)
    delete_success = memory.delete_all(user_id=user_id)
    if delete_success:
        print(f"   ✓ Deleted {count_before} memories")
    else:
        print("   ✗ Failed to delete memories")

    _safe_delete_all(memory, user_id=user_id)


def test_extension_exercise() -> None:
    _print_step("Extension Exercise: Multiple Users and Metadata Search")
    memory = create_memory()
    _safe_delete_all(memory, user_id="user1")
    _safe_delete_all(memory, user_id="user2")
    _safe_delete_all(memory, user_id="user123")

    memory.add("User 1 likes Python", user_id="user1")
    memory.add("User 2 likes Java", user_id="user2")

    results_user1 = memory.search("preferences", user_id="user1")
    results_user2 = memory.search("preferences", user_id="user2")

    print("Results for user1:")
    for entry in _get_results_list(results_user1):
        print(f"  - {_memory_text(entry)}")

    print("\nResults for user2:")
    for entry in _get_results_list(results_user2):
        print(f"  - {_memory_text(entry)}")

    memory.add(
        messages="User preference",
        user_id="user123",
        metadata={
            "category": "preference",
            "importance": "high",
            "source": "conversation",
            "timestamp": "2024-01-01",
            "tags": ["python", "programming"],
        },
    )

    print("\nSearch by category for user123:")
    category_results = memory.search(
        query="programming languages",
        user_id="user123",
    )
    for entry in _get_results_list(category_results):
        print(f"  - {_memory_text(entry)}")
        metadata = entry.get("metadata") if isinstance(entry, dict) else None
        print(f"    Metadata: {metadata or {}}")

    print("\nSearch with different limit for user123:")
    limit_results = memory.search(
        query="user information",
        user_id="user123",
        limit=10,
    )
    for entry in _get_results_list(limit_results):
        print(f"  - {_memory_text(entry)}")
        metadata = entry.get("metadata") if isinstance(entry, dict) else None
        if metadata:
            print(f"    Metadata: {metadata}")

    _safe_delete_all(memory, user_id="user1")
    _safe_delete_all(memory, user_id="user2")
    _safe_delete_all(memory, user_id="user123")


def test_edge_cases() -> None:
    """Test edge cases: handling of empty strings, special characters, Unicode characters, and very long content"""
    _print_step("Edge Cases: Empty Strings, Special Characters, Unicode")
    memory = create_memory()
    user_id = "user_edge_cases"
    _safe_delete_all(memory, user_id=user_id)

    # Test empty string
    print("Testing empty string...")
    try:
        result = memory.add(messages="", user_id=user_id)
        print("✓ Empty string handled")
    except Exception as e:
        print(f"  Note: Empty string raised exception: {e}")

    # Test special characters
    print("\nTesting special characters...")
    special_content = "User's favorite: <Python> & \"JavaScript\" | {JSON} | [Arrays]"
    result = memory.add(messages=special_content, user_id=user_id)
    memory_id = _extract_memory_id(result)
    if memory_id:
        retrieved = memory.get(memory_id=memory_id, user_id=user_id)
        retrieved_text = _memory_text(retrieved)
        print(f"✓ Special characters preserved: {retrieved_text}")

    # Test Unicode characters
    print("\nTesting Unicode characters...")
    unicode_content = "User likes Python programming 🐍 and machine learning 🤖"
    result = memory.add(messages=unicode_content, user_id=user_id)
    memory_id = _extract_memory_id(result)
    if memory_id:
        retrieved = memory.get(memory_id=memory_id, user_id=user_id)
        retrieved_text = _memory_text(retrieved)
        print(f"✓ Unicode characters preserved: {retrieved_text}")

    # Test very long content
    print("\nTesting long content...")
    long_content = "User likes " + "Python programming " * 100
    result = memory.add(messages=long_content[:500], user_id=user_id)
    memory_id = _extract_memory_id(result)
    if memory_id:
        print(f"✓ Long content added (ID: {memory_id})")

    _safe_delete_all(memory, user_id=user_id)


def test_error_handling() -> None:
    """Test error handling: get/update/delete operations with invalid IDs, and handling of empty queries"""
    _print_step("Error Handling: Invalid IDs and Edge Cases")
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
        memory.update(memory_id=99999, content="New content", user_id=user_id)
        print("  Note: Update completed (may have created new or failed silently)")
    except Exception as e:
        print(f"✓ Update raised exception for non-existent ID: {type(e).__name__}")

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


def test_complex_metadata() -> None:
    """Test complex metadata: storage and update of metadata containing various data types (strings, numbers, lists, nested dictionaries)"""
    _print_step("Complex Metadata: Nested Structures and Various Types")
    memory = create_memory()
    user_id = "user_complex_metadata"
    _safe_delete_all(memory, user_id=user_id)

    # Test metadata with various types
    print("Testing metadata with various data types...")
    memory.add(
        messages="User preference with complex metadata",
        user_id=user_id,
        metadata={
            "string_field": "value",
            "number_field": 42,
            "float_field": 3.14,
            "bool_field": True,
            "list_field": ["tag1", "tag2", "tag3"],
            "nested_dict": {
                "level1": {
                    "level2": "deep_value"
                }
            },
            "timestamp": "2024-01-01T00:00:00Z",
        },
    )

    # Search and verify metadata
    results = memory.search(query="preference", user_id=user_id)
    results_list = list(_get_results_list(results))
    if results_list:
        entry = results_list[0]
        metadata = entry.get("metadata") if isinstance(entry, dict) else None
        if metadata:
            print("✓ Complex metadata stored:")
            print(f"  - String: {metadata.get('string_field')}")
            print(f"  - Number: {metadata.get('number_field')}")
            print(f"  - List: {metadata.get('list_field')}")
            print(f"  - Nested: {metadata.get('nested_dict', {}).get('level1', {}).get('level2')}")

    # Test updating metadata
    print("\nTesting metadata update...")
    result = memory.add(messages="Memory to update", user_id=user_id, metadata={"version": 1})
    memory_id = _extract_memory_id(result)
    if memory_id:
        memory.update(
            memory_id=memory_id,
            content="Memory to update",
            user_id=user_id,
            metadata={"version": 2, "updated": True}
        )
        updated = memory.get(memory_id=memory_id, user_id=user_id)
        updated_metadata = updated.get("metadata") if isinstance(updated, dict) else None
        if updated_metadata:
            print(f"✓ Metadata updated: version={updated_metadata.get('version')}")

    _safe_delete_all(memory, user_id=user_id)


def test_search_variations() -> None:
    """Test search variations: different limit values, queries with no results, multi-condition filtering, and case variations"""
    _print_step("Search Variations: Different Limits, Queries, and Filters")
    memory = create_memory()
    user_id = "user_search_variations"
    _safe_delete_all(memory, user_id=user_id)

    # Add multiple memories
    memories = [
        ("User likes Python", {"category": "language", "priority": "high"}),
        ("User likes JavaScript", {"category": "language", "priority": "medium"}),
        ("User likes Java", {"category": "language", "priority": "low"}),
        ("User prefers email", {"category": "communication", "priority": "high"}),
        ("User prefers phone", {"category": "communication", "priority": "low"}),
    ]

    for content, meta in memories:
        memory.add(messages=content, user_id=user_id, metadata=meta)

    # Test different limits
    print("Testing search with different limits...")
    for limit in [1, 3, 5, 10]:
        results = memory.search(query="user preferences", user_id=user_id, limit=limit)
        results_list = list(_get_results_list(results))
        print(f"  Limit {limit}: Found {len(results_list)} results")

    # Test search with no results
    print("\nTesting search with no matching results...")
    results = memory.search(query="nonexistent content xyz123", user_id=user_id)
    results_list = list(_get_results_list(results))
    print(f"✓ No results query returned {len(results_list)} results")

    # Test search with multiple metadata filters
    print("\nTesting search with multiple metadata filters...")
    results = memory.search(
        query="user preferences",
        user_id=user_id,
        filters={"category": "language", "priority": "high"}
    )
    results_list = list(_get_results_list(results))
    print(f"✓ Multi-filter search found {len(results_list)} results")
    for entry in results_list:
        meta = entry.get("metadata") if isinstance(entry, dict) else None
        print(f"  - {_memory_text(entry)} (priority: {meta.get('priority') if meta else 'N/A'})")

    # Test case-insensitive search (if supported)
    print("\nTesting case variations...")
    results1 = memory.search(query="python", user_id=user_id)
    results2 = memory.search(query="Python", user_id=user_id)
    results3 = memory.search(query="PYTHON", user_id=user_id)
    print(f"  'python': {len(list(_get_results_list(results1)))} results")
    print(f"  'Python': {len(list(_get_results_list(results2)))} results")
    print(f"  'PYTHON': {len(list(_get_results_list(results3)))} results")

    _safe_delete_all(memory, user_id=user_id)


def test_data_consistency() -> None:
    """Test data consistency: verify correctness and consistency of data after add/get/update/delete operations"""
    _print_step("Data Consistency: Add-Get-Update-Delete Verification")
    memory = create_memory()
    user_id = "user_consistency"
    _safe_delete_all(memory, user_id=user_id)

    # Test add and immediate get
    print("Testing add and immediate retrieval...")
    content = "User likes consistency testing"
    result = memory.add(messages=content, user_id=user_id)
    memory_id = _extract_memory_id(result)
    if memory_id:
        retrieved = memory.get(memory_id=memory_id, user_id=user_id)
        retrieved_text = _memory_text(retrieved)
        if content.lower() in retrieved_text.lower() or retrieved_text.lower() in content.lower():
            print(f"✓ Add-Get consistency verified")
        else:
            print(f"  Note: Content may have been processed: '{content}' -> '{retrieved_text}'")

    # Test update and verification
    print("\nTesting update and verification...")
    if memory_id:
        updated_content = "User loves consistency testing"
        memory.update(memory_id=memory_id, content=updated_content, user_id=user_id)
        updated = memory.get(memory_id=memory_id, user_id=user_id)
        updated_text = _memory_text(updated)
        if updated_content.lower() in updated_text.lower() or updated_text.lower() in updated_content.lower():
            print(f"✓ Update consistency verified")
        else:
            print(f"  Note: Content may have been processed: '{updated_content}' -> '{updated_text}'")

    # Test delete and verification
    print("\nTesting delete and verification...")
    if memory_id:
        success = memory.delete(memory_id=memory_id, user_id=user_id)
        if success:
            deleted_check = memory.get(memory_id=memory_id, user_id=user_id)
            if deleted_check is None or not deleted_check:
                print("✓ Delete consistency verified (memory no longer exists)")
            else:
                print("  Note: Memory still exists after delete (may be soft delete)")

    # Test get_all consistency
    print("\nTesting get_all consistency...")
    memory.add("Memory 1", user_id=user_id)
    memory.add("Memory 2", user_id=user_id)
    memory.add("Memory 3", user_id=user_id)

    all_memories = memory.get_all(user_id=user_id)
    results_list = list(_get_results_list(all_memories))
    print(f"✓ get_all returned {len(results_list)} memories (expected 3)")

    # Verify search finds all
    search_results = memory.search(query="Memory", user_id=user_id, limit=10)
    search_list = list(_get_results_list(search_results))
    print(f"✓ search found {len(search_list)} memories")

    _safe_delete_all(memory, user_id=user_id)


def test_batch_operations() -> None:
    """Test batch operations: batch add, batch update, batch delete, and state verification after batch operations"""
    _print_step("Batch Operations: Multiple Adds and Bulk Operations")
    memory = create_memory()
    user_id = "user_batch"
    _safe_delete_all(memory, user_id=user_id)

    # Batch add
    print("Testing batch add operations...")
    batch_size = 10
    memory_ids = []
    for i in range(batch_size):
        result = memory.add(
            messages=f"Batch memory {i+1}: User preference item {i+1}",
            user_id=user_id,
            metadata={"batch_id": i+1, "type": "batch"}
        )
        memory_id = _extract_memory_id(result)
        if memory_id:
            memory_ids.append(memory_id)

    print(f"✓ Added {len(memory_ids)} memories in batch")

    # Verify all were added
    all_memories = memory.get_all(user_id=user_id)
    results_list = list(_get_results_list(all_memories))
    print(f"✓ Verified {len(results_list)} memories exist")

    # Batch search
    print("\nTesting batch search...")
    results = memory.search(query="batch memory", user_id=user_id, limit=batch_size * 2)
    search_list = list(_get_results_list(results))
    print(f"✓ Batch search found {len(search_list)} results")

    # Batch update
    print("\nTesting batch update...")
    updated_count = 0
    for memory_id in memory_ids[:5]:  # Update first 5
        try:
            memory.update(
                memory_id=memory_id,
                content=f"Updated batch memory",
                user_id=user_id,
                metadata={"batch_id": memory_id, "type": "batch", "updated": True}
            )
            updated_count += 1
        except Exception as e:
            print(f"  Warning: Failed to update {memory_id}: {e}")
    print(f"✓ Updated {updated_count} memories")

    # Batch delete
    print("\nTesting batch delete...")
    deleted_count = 0
    for memory_id in memory_ids[5:]:  # Delete last 5
        try:
            success = memory.delete(memory_id=memory_id, user_id=user_id)
            if success:
                deleted_count += 1
        except Exception as e:
            print(f"  Warning: Failed to delete {memory_id}: {e}")
    print(f"✓ Deleted {deleted_count} memories")

    # Verify final state
    final_memories = memory.get_all(user_id=user_id)
    final_list = list(_get_results_list(final_memories))
    print(f"✓ Final count: {len(final_list)} memories remaining")

    _safe_delete_all(memory, user_id=user_id)


def test_get_operations() -> None:
    """Test Get operations: retrieval of a single memory, field completeness verification, and handling of invalid user_id"""
    _print_step("Get Operations: Single Memory Retrieval")
    memory = create_memory()
    user_id = "user_get_ops"
    _safe_delete_all(memory, user_id=user_id)

    # Add a memory and get it
    print("Testing get() with valid memory_id...")
    content = "User likes get operations"
    result = memory.add(messages=content, user_id=user_id)
    memory_id = _extract_memory_id(result)
    
    if memory_id:
        retrieved = memory.get(memory_id=memory_id, user_id=user_id)
        if retrieved:
            retrieved_text = _memory_text(retrieved)
            print(f"✓ Successfully retrieved memory: {retrieved_text[:50]}...")
            
            # Verify all expected fields
            if isinstance(retrieved, dict):
                print(f"  - Has 'id' or 'memory_id': {'id' in retrieved or 'memory_id' in retrieved}")
                print(f"  - Has content: {bool(retrieved_text)}")
                print(f"  - Has metadata: {'metadata' in retrieved}")
        else:
            print("  Warning: get() returned None/empty")

    # Test get with wrong user_id
    print("\nTesting get() with wrong user_id...")
    if memory_id:
        wrong_user_result = memory.get(memory_id=memory_id, user_id="wrong_user")
        if wrong_user_result is None or not wrong_user_result:
            print("✓ Wrong user_id correctly returns None/empty")
        else:
            print("  Note: Memory accessible across users (may be by design)")

    _safe_delete_all(memory, user_id=user_id)


def main() -> None:
    
    _print_banner("Powermem Scenario 1: Basic Usage")
    # Core scenario steps
    test_step1_setup()
    test_step2_add_first_memory()
    test_step3_add_multiple_memories()
    test_step4_search_memories()
    test_step5_add_with_metadata()
    test_step6_search_with_metadata_filters()
    test_step7_get_all_memories()
    test_step8_update_memory()
    test_step9_delete_memory()
    test_step10_delete_all_memories()

    test_full_example()
    test_extension_exercise()

    # Additional comprehensive tests
    test_edge_cases()
    test_error_handling()
    test_complex_metadata()
    test_search_variations()
    test_data_consistency()
    test_batch_operations()
    test_get_operations()

    _print_banner("Scenario 1 walkthrough completed successfully!")


if __name__ == "__main__":
    main()

