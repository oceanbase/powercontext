"""Executable walkthrough for `scenario_2_intelligent_memory.md`.

Run `python docs/examples/scenario_2_intelligent_memory.py` to exercise every
code sample from the Scenario 2 documentation in a single pass.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from powermem import create_memory


def _ensure_env_loaded() -> None:
    current_dir = os.path.dirname(__file__)
    env_path = os.path.join(current_dir, "..", "..", ".env")
    env_example_path = os.path.join(current_dir, "..", "..", "configs", "env.example")

    if not os.path.exists(env_path):
        print(f"\nNo .env file found at: {env_path}")
        print("To add your API keys:")
        print(f"  1. Copy: cp {env_example_path} {env_path}")
        print(f"  2. Edit {env_path} and add your API keys")
        print("\nUsing mock providers for demonstration (if available)...\n")
    else:
        print(f"Found .env file at {env_path}")
        load_dotenv(env_path, override=True)


_ensure_env_loaded()


def _print_banner(title: str) -> None:
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def _print_step(title: str) -> None:
    print("\n" + "-" * 80)
    print(title)
    print("-" * 80)


def _get_results(result: Any) -> list[Dict[str, Any]]:
    if isinstance(result, dict):
        items = result.get("results")
        if isinstance(items, list):
            return items
    return []


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


def test_step1_enable_intelligent_processing() -> None:
    _print_step("Step 1: Enable Intelligent Processing")
    memory = create_memory()
    user_id = "scenario2_test_step1_user"
    _safe_delete_all(memory, user_id=user_id)

    conversation = [
        {"role": "user", "content": "Hi, my name is Alice. I'm a software engineer at Google."},
        {"role": "assistant", "content": "Nice to meet you, Alice! That's interesting."},
        {"role": "user", "content": "I love Python programming and machine learning."},
    ]

    result = memory.add(messages=conversation, user_id=user_id, infer=True)
    extracted = _get_results(result)

    print(f"✓ Processed conversation, extracted {len(extracted)} memories:")
    for idx, mem in enumerate(extracted, start=1):
        print(f"  {idx}. {_memory_text(mem)}")

    _safe_delete_all(memory, user_id=user_id)


def test_step2_duplicate_detection() -> None:
    _print_step("Step 2: Duplicate Detection")
    memory = create_memory()
    user_id = "scenario2_test_step2_user"
    _safe_delete_all(memory, user_id=user_id)

    print("1. Adding initial memory...")
    result1 = memory.add(
        messages=[
            {"role": "user", "content": "I'm Alice, a software engineer"},
            {"role": "assistant", "content": "I remember that!"},
        ],
        user_id=user_id,
        infer=True,
    )
    print(f"   Added {len(_get_results(result1))} memories")

    print("\n2. Attempting to add duplicate...")
    result2 = memory.add(
        messages=[
            {"role": "user", "content": "I'm Alice, a software engineer"},
            {"role": "assistant", "content": "I know!"},
        ],
        user_id=user_id,
        infer=True,
    )

    results = _get_results(result2)
    if results:
        event = results[0].get("event", "N/A")
        print(f"   Event: {event}")
        if event == "NONE":
            print("   ✓ Duplicate detected, no new memory created")
    else:
        print("   ✓ Duplicate detected, skipped")

    _safe_delete_all(memory, user_id=user_id)


def test_step3_information_updates() -> None:
    _print_step("Step 3: Information Updates")
    memory = create_memory()
    user_id = "scenario2_test_step3_user"
    _safe_delete_all(memory, user_id=user_id)

    print("1. Adding initial information...")
    memory.add(
        messages=[
            {"role": "user", "content": "I work at Google as a software engineer"},
        ],
        user_id=user_id,
        infer=True,
    )

    print("\n2. Updating information...")
    result = memory.add(
        messages=[
            {"role": "user", "content": "I recently moved to Meta as a senior ML engineer"},
        ],
        user_id=user_id,
        infer=True,
    )

    print("\n3. Checking results...")
    for mem in _get_results(result):
        event = mem.get("event", "N/A")
        print(f"   Event: {event}")
        if event == "UPDATE":
            print("   Updated memory:")
            print(f"     Old: {mem.get('previous_memory', 'N/A')}")
            print(f"     New: {mem.get('memory', 'N/A')}")
    _safe_delete_all(memory, user_id=user_id)


def test_step4_add_new_information() -> None:
    _print_step("Step 4: Adding New Information")
    memory = create_memory()
    user_id = "scenario2_test_step4_user"
    _safe_delete_all(memory, user_id=user_id)

    memory.add(
        messages=[
            {"role": "user", "content": "I'm Alice, a software engineer"},
        ],
        user_id=user_id,
        infer=True,
    )

    print("Adding new information...")
    result = memory.add(
        messages=[
            {"role": "user", "content": "I like to drink coffee every morning and I have two cats."},
            {"role": "assistant", "content": "That's nice! What are your cats' names?"},
            {"role": "user", "content": "Their names are Fluffy and Whiskers."},
        ],
        user_id=user_id,
        infer=True,
    )

    extracted = _get_results(result)
    print(f"\n✓ Added {len(extracted)} new memories:")
    for mem in extracted:
        event = mem.get("event", "N/A")
        print(f"  [{event}] {_memory_text(mem)}")

    _safe_delete_all(memory, user_id=user_id)


def test_step5_conflict_resolution() -> None:
    _print_step("Step 5: Conflict Resolution")
    memory = create_memory()
    user_id = "scenario2_test_step5_user"
    _safe_delete_all(memory, user_id=user_id)

    print("1. Adding initial preference...")
    memory.add(
        messages=[
            {"role": "user", "content": "I like to drink coffee every morning"},
        ],
        user_id=user_id,
        infer=True,
    )

    print("\n2. Adding contradictory information...")
    result = memory.add(
        messages=[
            {"role": "user", "content": "Actually, I don't like coffee anymore. I prefer tea now."},
        ],
        user_id=user_id,
        infer=True,
    )

    print("\n3. Conflict resolution results:")
    for mem in _get_results(result):
        event = mem.get("event", "N/A")
        print(f"   Event: {event}")
        if event == "DELETE":
            print(f"     Deleted: {_memory_text(mem)}")
        elif event == "ADD":
            print(f"     Added: {_memory_text(mem)}")

    _safe_delete_all(memory, user_id=user_id)


def test_step6_memory_consolidation() -> None:
    _print_step("Step 6: Memory Consolidation")
    memory = create_memory()
    user_id = "scenario2_test_step6_user"
    _safe_delete_all(memory, user_id=user_id)

    print("1. Adding initial memory...")
    memory.add(
        messages=[
            {"role": "user", "content": "I love Python programming"},
        ],
        user_id=user_id,
        infer=True,
    )

    print("\n2. Adding more detailed information...")
    result = memory.add(
        messages=[
            {
                "role": "user",
                "content": "I love Python, especially for deep learning. I use TensorFlow and PyTorch a lot.",
            }
        ],
        user_id=user_id,
        infer=True,
    )

    print("\n3. Consolidation results:")
    for mem in _get_results(result):
        event = mem.get("event", "N/A")
        if event == "UPDATE":
            print("   Updated memory:")
            print(f"     Old: {mem.get('previous_memory', 'N/A')}")
            print(f"     New: {mem.get('memory', 'N/A')}")
        else:
            print(f"   [{event}] {_memory_text(mem)}")

    _safe_delete_all(memory, user_id=user_id)


def test_complete_example() -> None:
    _print_step("Complete Example")
    memory = create_memory()
    user_id = "scenario2_complete_user"
    _safe_delete_all(memory, user_id=user_id)

    print("[Scenario 1] Initial Memory Addition")
    result = memory.add(
        messages=[
            {"role": "user", "content": "Hi, my name is Alice. I'm a software engineer at Google."},
            {"role": "assistant", "content": "Nice to meet you, Alice!"},
            {"role": "user", "content": "I love Python programming and machine learning."},
        ],
        user_id=user_id,
        infer=True,
    )
    print(f"✓ Extracted {len(_get_results(result))} memories")

    print("\n[Scenario 2] Duplicate Detection")
    result = memory.add(
        messages=[
            {"role": "user", "content": "I'm Alice, a software engineer"},
        ],
        user_id=user_id,
        infer=True,
    )
    for mem in _get_results(result):
        if mem.get("event", 'N/A') == "NONE":
            print("✓ Duplicate detected, skipped")

    print("\n[Scenario 3] Information Update")
    result = memory.add(
        messages=[
            {"role": "user", "content": "I recently moved to Meta as a senior ML engineer"},
        ],
        user_id=user_id,
        infer=True,
    )
    for mem in _get_results(result):
        if mem.get("event") == "UPDATE":
            print(f"✓ Updated: {mem.get('previous_memory')} → {mem.get('memory')}")

    print("\n[Scenario 4] Adding New Information")
    result = memory.add(
        messages=[
            {"role": "user", "content": "I like to drink coffee every morning and I have two cats."},
        ],
        user_id=user_id,
        infer=True,
    )
    print(f"✓ Added {len(_get_results(result))} new memories")

    print("\n[Scenario 5] Conflict Resolution")
    result = memory.add(
        messages=[
            {"role": "user", "content": "Actually, I prefer tea instead of coffee now."},
        ],
        user_id=user_id,
        infer=True,
    )
    for mem in _get_results(result):
        event = mem.get("event")
        if event == "DELETE":
            print("✓ Deleted conflicting memory")
        elif event == "ADD":
            print("✓ Added new preference")

    print("\nFinal Memory Summary")
    all_memories = memory.get_all(user_id=user_id)
    results = _get_results(all_memories)
    print(f"Total memories: {len(results)}")
    for idx, mem in enumerate(results, start=1):
        print(f"  {idx}. {_memory_text(mem)}")

    _safe_delete_all(memory, user_id=user_id)


def test_extension_exercise_compare_modes() -> None:
    _print_step("Extension Exercise 1: Compare Simple vs Intelligent Mode")
    memory = create_memory()
    user_id = "scenario2_extension_compare"
    _safe_delete_all(memory, user_id=user_id)

    print("1. Simple mode (infer=False):")
    simple_result = memory.add("User likes Python", user_id=user_id, infer=False)
    simple_text = _memory_text(_get_results(simple_result)[0]) if _get_results(simple_result) else "N/A"
    print(f"   Added memory directly: {simple_text}")

    print("\n2. Intelligent mode (infer=True):")
    intelligent_result = memory.add(
        messages=[{"role": "user", "content": "I like Python programming"}],
        user_id=user_id,
        infer=True,
    )
    print("   Extracted memories:")
    for mem in _get_results(intelligent_result):
        event = mem.get("event", "N/A")
        print(f"   - [{event}] {_memory_text(mem)}")

    print("\n✓ Comparison completed. Intelligent mode extracts facts automatically!")

    _safe_delete_all(memory, user_id=user_id)


def test_extension_exercise_track_events() -> None:
    _print_step("Extension Exercise 2: Track Memory Events")
    memory = create_memory()
    user_id = "scenario2_extension_events"
    _safe_delete_all(memory, user_id=user_id)

    result = memory.add(
        messages=[
            {"role": "user", "content": "I love working with machine learning"},
        ],
        user_id=user_id,
        infer=True,
    )

    print("Processing results:")
    for mem in _get_results(result):
        event = mem.get("event")
        if event == "ADD":
            print(f"✓ New memory added: {_memory_text(mem)}")
        elif event == "UPDATE":
            print(f"✓ Memory updated: {mem.get('previous_memory', '')} → {mem.get('memory', '')}")
        elif event == "DELETE":
            print(f"✓ Memory deleted: {_memory_text(mem)}")
        elif event == "NONE":
            print("✓ Duplicate detected, skipped")

    _safe_delete_all(memory, user_id=user_id)


def test_extension_exercise_complex_conversation() -> None:
    _print_step("Extension Exercise 3: Complex Conversations")
    memory = create_memory()
    user_id = "scenario2_extension_complex"
    _safe_delete_all(memory, user_id=user_id)

    long_conversation = [
        {"role": "user", "content": "I'm Alice, a software engineer at Google."},
        {"role": "assistant", "content": "Nice to meet you!"},
        {"role": "user", "content": "I work on machine learning projects."},
        {"role": "assistant", "content": "That's interesting!"},
        {"role": "user", "content": "I use Python, TensorFlow, and PyTorch."},
    ]

    print("Processing long conversation...")
    result = memory.add(messages=long_conversation, user_id=user_id, infer=True)

    extracted = _get_results(result)
    print(f"\n✓ Extracted {len(extracted)} memories:")
    for idx, mem in enumerate(extracted, start=1):
        event = mem.get("event", "N/A")
        print(f"  {idx}. [{event}] {_memory_text(mem)}")

    _safe_delete_all(memory, user_id=user_id)


def test_multiple_updates() -> None:
    """Test multiple updates: verify the handling logic when the same information is updated multiple times"""
    _print_step("Multiple Updates: Sequential Information Changes")
    memory = create_memory()
    user_id = "scenario2_multiple_updates"
    _safe_delete_all(memory, user_id=user_id)

    print("1. Initial information...")
    memory.add(
        messages=[{"role": "user", "content": "I work at Google as a software engineer"}],
        user_id=user_id,
        infer=True,
    )

    print("\n2. First update...")
    result1 = memory.add(
        messages=[{"role": "user", "content": "I moved to Meta as a senior engineer"}],
        user_id=user_id,
        infer=True,
    )
    for mem in _get_results(result1):
        if mem.get("event") == "UPDATE":
            print(f"   ✓ Updated: {mem.get('previous_memory', 'N/A')} → {mem.get('memory', 'N/A')}")

    print("\n3. Second update...")
    result2 = memory.add(
        messages=[{"role": "user", "content": "I'm now at OpenAI working on AI research"}],
        user_id=user_id,
        infer=True,
    )
    for mem in _get_results(result2):
        if mem.get("event") == "UPDATE":
            print(f"   ✓ Updated again: {mem.get('previous_memory', 'N/A')} → {mem.get('memory', 'N/A')}")

    print("\n4. Final state:")
    all_memories = memory.get_all(user_id=user_id)
    results = _get_results(all_memories)
    print(f"   Total memories: {len(results)}")
    for idx, mem in enumerate(results, start=1):
        print(f"   {idx}. {_memory_text(mem)}")

    _safe_delete_all(memory, user_id=user_id)


def test_multiple_conflicts() -> None:
    """Test multiple conflicts: resolving multiple conflicting pieces of information simultaneously"""
    _print_step("Multiple Conflicts: Resolving Multiple Contradictions")
    memory = create_memory()
    user_id = "scenario2_multiple_conflicts"
    _safe_delete_all(memory, user_id=user_id)

    print("1. Adding initial preferences...")
    memory.add(
        messages=[
            {"role": "user", "content": "I like coffee and I prefer Python over Java"},
        ],
        user_id=user_id,
        infer=True,
    )

    print("\n2. Adding multiple contradictory statements...")
    result = memory.add(
        messages=[
            {"role": "user", "content": "Actually, I don't like coffee anymore, I prefer tea. Also, I've changed my mind and now I prefer Java over Python."},
        ],
        user_id=user_id,
        infer=True,
    )

    print("\n3. Conflict resolution results:")
    delete_count = 0
    add_count = 0
    for mem in _get_results(result):
        event = mem.get("event", "N/A")
        if event == "DELETE":
            delete_count += 1
            print(f"   [DELETE] {_memory_text(mem)}")
        elif event == "ADD":
            add_count += 1
            print(f"   [ADD] {_memory_text(mem)}")
        elif event == "UPDATE":
            print(f"   [UPDATE] {mem.get('previous_memory', 'N/A')} → {mem.get('memory', 'N/A')}")

    print(f"\n✓ Resolved conflicts: {delete_count} deleted, {add_count} added")

    _safe_delete_all(memory, user_id=user_id)


def test_search_after_intelligent_add() -> None:
    """Test search in intelligent mode: verify that memories added can be correctly searched"""
    _print_step("Search After Intelligent Add: Verify Search Functionality")
    memory = create_memory()
    user_id = "scenario2_search_test"
    _safe_delete_all(memory, user_id=user_id)

    print("1. Adding memories with intelligent processing...")
    result = memory.add(
        messages=[
            {"role": "user", "content": "I'm Bob, a data scientist. I work with Python and machine learning. I love reading books."},
        ],
        user_id=user_id,
        infer=True,
    )
    extracted = _get_results(result)
    print(f"   ✓ Extracted {len(extracted)} memories")

    print("\n2. Searching for 'Python'...")
    search_results = memory.search(query="Python", user_id=user_id, limit=5)
    search_list = _get_results(search_results)
    print(f"   Found {len(search_list)} memories:")
    for idx, mem in enumerate(search_list, start=1):
        print(f"   {idx}. {_memory_text(mem)}")

    print("\n3. Searching for 'books'...")
    search_results2 = memory.search(query="books", user_id=user_id, limit=5)
    search_list2 = _get_results(search_results2)
    print(f"   Found {len(search_list2)} memories:")
    for idx, mem in enumerate(search_list2, start=1):
        print(f"   {idx}. {_memory_text(mem)}")

    _safe_delete_all(memory, user_id=user_id)


def test_mixed_events() -> None:
    """Test mixed events: multiple event types (ADD, UPDATE, DELETE) generated in a single operation"""
    _print_step("Mixed Events: Multiple Event Types in One Operation")
    memory = create_memory()
    user_id = "scenario2_mixed_events"
    _safe_delete_all(memory, user_id=user_id)

    print("1. Setting up initial state...")
    memory.add(
        messages=[
            {"role": "user", "content": "I like coffee. I work at Google. I love Python."},
        ],
        user_id=user_id,
        infer=True,
    )

    print("\n2. Adding mixed update (update some, add new, conflict with old)...")
    result = memory.add(
        messages=[
            {"role": "user", "content": "I work at Meta now (not Google). I prefer tea over coffee. I also love JavaScript."},
        ],
        user_id=user_id,
        infer=True,
    )

    print("\n3. Event breakdown:")
    event_counts = {}
    for mem in _get_results(result):
        event = mem.get("event", "N/A")
        event_counts[event] = event_counts.get(event, 0) + 1
        
        if event == "ADD":
            print(f"   [ADD] {_memory_text(mem)}")
        elif event == "UPDATE":
            print(f"   [UPDATE] {mem.get('previous_memory', 'N/A')} → {mem.get('memory', 'N/A')}")
        elif event == "DELETE":
            print(f"   [DELETE] {_memory_text(mem)}")
        elif event == "NONE":
            print(f"   [NONE] Duplicate skipped")

    print(f"\n✓ Event summary: {event_counts}")

    _safe_delete_all(memory, user_id=user_id)


def test_partial_update() -> None:
    """Test partial update: only update part of the information in a memory, keeping other information unchanged"""
    _print_step("Partial Update: Updating Only Part of Information")
    memory = create_memory()
    user_id = "scenario2_partial_update"
    _safe_delete_all(memory, user_id=user_id)

    print("1. Adding comprehensive information...")
    memory.add(
        messages=[
            {"role": "user", "content": "I'm Alice. I work at Google as a software engineer. I love Python and machine learning."},
        ],
        user_id=user_id,
        infer=True,
    )

    print("\n2. Updating only job information...")
    result = memory.add(
        messages=[
            {"role": "user", "content": "I got promoted to senior software engineer at Google"},
        ],
        user_id=user_id,
        infer=True,
    )

    print("\n3. Update results:")
    for mem in _get_results(result):
        event = mem.get("event", "N/A")
        if event == "UPDATE":
            print(f"   ✓ Updated: {mem.get('previous_memory', 'N/A')}")
            print(f"      → {mem.get('memory', 'N/A')}")
        elif event == "ADD":
            print(f"   ✓ Added: {_memory_text(mem)}")

    print("\n4. Verifying other information remains:")
    all_memories = memory.get_all(user_id=user_id)
    results = _get_results(all_memories)
    for mem in results:
        text = _memory_text(mem)
        if "Python" in text or "machine learning" in text:
            print(f"   ✓ Found: {text}")

    _safe_delete_all(memory, user_id=user_id)


def test_complex_consolidation() -> None:
    """Test complex consolidation: merging and integrating multiple related memories"""
    _print_step("Complex Consolidation: Merging Related Memories")
    memory = create_memory()
    user_id = "scenario2_complex_consolidation"
    _safe_delete_all(memory, user_id=user_id)

    print("1. Adding fragmented information...")
    memory.add(
        messages=[{"role": "user", "content": "I love Python"}],
        user_id=user_id,
        infer=True,
    )
    memory.add(
        messages=[{"role": "user", "content": "I use Python for data science"}],
        user_id=user_id,
        infer=True,
    )

    print("\n2. Adding comprehensive information that should consolidate...")
    result = memory.add(
        messages=[
            {"role": "user", "content": "I love Python programming, especially for data science and machine learning. I use libraries like pandas, numpy, and scikit-learn."},
        ],
        user_id=user_id,
        infer=True,
    )

    print("\n3. Consolidation results:")
    update_count = 0
    add_count = 0
    for mem in _get_results(result):
        event = mem.get("event", "N/A")
        if event == "UPDATE":
            update_count += 1
            print(f"   [UPDATE] Consolidated:")
            print(f"      Old: {mem.get('previous_memory', 'N/A')}")
            print(f"      New: {mem.get('memory', 'N/A')}")
        elif event == "ADD":
            add_count += 1
            print(f"   [ADD] {_memory_text(mem)}")

    print(f"\n✓ Consolidation: {update_count} updated, {add_count} added")

    _safe_delete_all(memory, user_id=user_id)


def test_edge_case_single_message() -> None:
    """Test edge case: intelligent processing of a single message"""
    _print_step("Edge Case: Single Message Processing")
    memory = create_memory()
    user_id = "scenario2_edge_single"
    _safe_delete_all(memory, user_id=user_id)

    print("Testing single message with infer=True...")
    result = memory.add(
        messages=[{"role": "user", "content": "I'm Charlie, a developer"}],
        user_id=user_id,
        infer=True,
    )

    extracted = _get_results(result)
    print(f"✓ Processed single message, extracted {len(extracted)} memories:")
    for idx, mem in enumerate(extracted, start=1):
        event = mem.get("event", "N/A")
        print(f"  {idx}. [{event}] {_memory_text(mem)}")

    _safe_delete_all(memory, user_id=user_id)


def test_sequential_duplicates() -> None:
    """Test sequential duplicates: handling when the same or similar content is added multiple times consecutively"""
    _print_step("Sequential Duplicates: Handling Repeated Similar Content")
    memory = create_memory()
    user_id = "scenario2_sequential_duplicates"
    _safe_delete_all(memory, user_id=user_id)

    print("1. Adding first message...")
    result1 = memory.add(
        messages=[{"role": "user", "content": "I'm David, a software engineer"}],
        user_id=user_id,
        infer=True,
    )
    print(f"   Added {len(_get_results(result1))} memories")

    print("\n2. Adding similar message (should be duplicate)...")
    result2 = memory.add(
        messages=[{"role": "user", "content": "I'm David, a software engineer"}],
        user_id=user_id,
        infer=True,
    )
    results2 = _get_results(result2)
    if results2:
        for mem in results2:
            if mem.get("event", 'N/A') == "NONE":
                print("✓ Duplicate detected, skipped")
    else:
        print("✓ Duplicate detected, skipped")

    print("\n3. Adding slightly different message...")
    result3 = memory.add(
        messages=[{"role": "user", "content": "I'm David, I work as a software engineer"}],
        user_id=user_id,
        infer=True,
    )
    results3 = _get_results(result3)
    if results3:
        for mem in results3:
            if mem.get("event", 'N/A') == "NONE":
                print("✓ Duplicate detected, skipped")
    else:
        print("✓ Duplicate detected, skipped")

    _safe_delete_all(memory, user_id=user_id)


def main() -> None:
    _print_banner("Powermem Scenario 2: Intelligent Memory")
    test_step1_enable_intelligent_processing()
    test_step2_duplicate_detection()
    test_step3_information_updates()
    test_step4_add_new_information()
    test_step5_conflict_resolution()
    test_step6_memory_consolidation()
    test_complete_example()
    test_extension_exercise_compare_modes()
    test_extension_exercise_track_events()
    test_extension_exercise_complex_conversation()
    
    # Additional comprehensive tests
    test_multiple_updates()
    test_multiple_conflicts()
    test_search_after_intelligent_add()
    test_mixed_events()
    test_partial_update()
    test_complex_consolidation()
    test_edge_case_single_message()
    test_sequential_duplicates()
    
    _print_banner("Scenario 2 walkthrough completed successfully!")


if __name__ == "__main__":
    main()

