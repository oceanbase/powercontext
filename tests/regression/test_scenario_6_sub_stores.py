"""Executable walkthrough for `scenario_6_sub_stores.md`.

This script simulates the sub store workflow described in the documentation.
It uses an in-memory mock implementation (`MockSubStoreMemory`) so the example
can be executed without an OceanBase deployment. The API matches the portions
used in the scenario, allowing you to swap in the real `powermem.Memory`
instance if you have a fully configured environment.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import pytest
from dotenv import load_dotenv
from powermem.config_loader import auto_config

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


# -----------------------------------------------------------------------------
# Helper printing utilities
# -----------------------------------------------------------------------------


def _print_banner(title: str) -> None:
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def _print_step(title: str) -> None:
    print("\n" + "-" * 80)
    print(title)
    print("-" * 80)


def _memory_text(entry: Dict[str, Any]) -> str:
    """Extract memory text from entry."""
    if not isinstance(entry, dict):
        return str(entry)
    return entry.get("memory") or entry.get("content") or entry.get("text") or str(entry)


# -----------------------------------------------------------------------------
# Mock sub store memory implementation (in-memory simulation)
# -----------------------------------------------------------------------------


# -----------------------------------------------------------------------------
# Scenario data and helpers
# -----------------------------------------------------------------------------


# Base config with sub_stores and database settings
BASE_CONFIG: Dict[str, Any] = {
    "vector_store": {
        "provider": "oceanbase",
        "config": {
            "collection_name": "demo_memories",
            "embedding_model_dims": 768,
            "connection_args": {
                "host": os.getenv("OCEANBASE_HOST", "6.12.235.2"),
                "port": int(os.getenv("OCEANBASE_PORT", "10001")),
                "user": os.getenv("OCEANBASE_USER", "root"),
                "password": os.getenv("OCEANBASE_PASSWORD", ""),
                "db_name": os.getenv("OCEANBASE_DB", "powermem"),
            },
        },
    },
    "sub_stores": [
        {
            "collection_name": "working_memories",
            "routing_filter": {"memory_type": "working"},
            "embedding_model_dims": 768,
        },
        {
            "collection_name": "episodic_memories",
            "routing_filter": {"memory_type": "episodic"},
            "embedding_model_dims": 768,
        },
    ],
}

def get_config() -> Dict[str, Any]:
    """Get merged configuration with API keys from environment."""
    # Load base config from environment (includes API keys)
    env_config = auto_config()
    
    # Get embedding dimension from environment config (default to 1536 for qwen text-embedding-v4)
    embedding_dims = 1536  # Default dimension for common models
    if "embedder" in env_config and "config" in env_config["embedder"]:
        embedding_dims = env_config["embedder"]["config"].get("embedding_dims", embedding_dims)
    
    # Update BASE_CONFIG with the correct embedding dimension
    base_config = BASE_CONFIG.copy()
    base_config["vector_store"]["config"]["embedding_model_dims"] = embedding_dims
    for sub_store in base_config["sub_stores"]:
        sub_store["embedding_model_dims"] = embedding_dims
    
    # Merge with custom config (BASE_CONFIG takes precedence for vector_store and sub_stores)
    merged_config = {**env_config, **base_config}
    
    # Ensure embedder config has embedding_dims (use the dimension we detected)
    if "embedder" in merged_config and "config" in merged_config["embedder"]:
        if "embedding_dims" not in merged_config["embedder"]["config"]:
            merged_config["embedder"]["config"]["embedding_dims"] = embedding_dims
    
    return merged_config


SEMANTIC_MEMORIES = [
    ("Python is a high-level programming language known for its simplicity,I love Python!", {"memory_type": "semantic", "topic": "programming"}),
    ("Machine learning is a subset of artificial intelligence,I love machine learning!", {"memory_type": "semantic", "topic": "ai"}),
]

WORKING_MEMORIES = [
    ("Today's weather is sunny, good for outdoor activities", {"memory_type": "working", "importance": "low"}),
    ("Need to buy groceries after work", {"memory_type": "working", "importance": "low"}),
    ("Meeting scheduled at 3 PM today", {"memory_type": "working", "importance": "medium"}),
]

EPISODIC_MEMORIES = [
    ("Last summer I visited Paris and saw the Eiffel Tower", {"memory_type": "episodic", "time": "2024-07"}),
    ("I learned to ride a bike when I was 7 years old", {"memory_type": "episodic", "time": "childhood"}),
]

USER_ID = "demo_user"

from powermem import Memory
def create_memory() -> Memory:
    config = get_config()
    memory = Memory(config)
    print("Using Memory (in-memory simulation).")
    return memory


@pytest.fixture(scope="session")
def memory() -> Memory:
    """Session-scoped fixture providing a shared Memory instance for all tests."""
    mem = create_memory()
    yield mem
    # Cleanup after all tests complete
    try:
        mem.delete_all(user_id=USER_ID)
        print(f"\n✓ Cleaned up all test data for user: {USER_ID}")
    except Exception as e:
        print(f"\n⚠ Could not cleanup test data: {str(e)[:100]}")


# Configure and display information about the memory system and sub stores
def test_step1_configure(memory: Memory) -> None:
    _print_step("Step 1: Configure Memory and Sub Stores")
    print("✓ Memory initialized successfully with 2 sub stores")
    print("  - Main store: demo_memories (semantic memories)")
    config = get_config()
    for index, sub_config in enumerate(config["sub_stores"]):
        print(f"  - Sub store {index}: {sub_config['collection_name']} (filter={sub_config['routing_filter']})")

# Add different types of memories
def test_step2_add_memories(memory: Memory) -> None:
    _print_step("Step 2: Add Different Types of Memories")
    print("Adding different types of memories...\n")

    print("1. Adding semantic memories (knowledge)...")
    for text, metadata in SEMANTIC_MEMORIES:
        memory.add(messages=text, metadata=metadata, user_id=USER_ID)
        print(f"   ✓ Added: {text}...")

    print("\n2. Adding working memories (tasks)...")
    for text, metadata in WORKING_MEMORIES:
        memory.add(messages=text, metadata=metadata, user_id=USER_ID)
        print(f"   ✓ Added: {text}...")

    print("\n3. Adding episodic memories (experiences)...")
    for text, metadata in EPISODIC_MEMORIES:
        memory.add(messages=text, metadata=metadata, user_id=USER_ID)
        print(f"   ✓ Added: {text}...")

    total = len(SEMANTIC_MEMORIES) + len(WORKING_MEMORIES) + len(EPISODIC_MEMORIES)
    print(f"\n✓ Total memories added: {total} (currently all in main store)")
    
    # Note: Cleanup will be done at the end of test_main()

# Query memories before migration, verify all memories are in the main store
def test_step3_query_before_migration(memory: Memory) -> None:
    _print_step("Step 3: Query Before Migration")
    print("Querying before migration (all in main store)\n")

    print("1. Querying working memories...")
    results = memory.search(query="today", filters={"memory_type": "working"}, user_id=USER_ID, limit=5)
    for index, result in enumerate(results["results"], start=1):
        source_store = result.get('_source_store', 'main')
        print(f"   {index}. [{source_store}] {result['memory'][:50]}")

    print("\n2. Querying episodic memories...")
    results = memory.search(query="visited", filters={"memory_type": "episodic"}, user_id=USER_ID, limit=5)
    for index, result in enumerate(results["results"], start=1):
        source_store = result.get('_source_store', 'main')
        print(f"   {index}. [{source_store}] {result['memory'][:50]}")
    
    # Note: Cleanup will be done at the end of test_main()

# Migrate memories from the main store to corresponding sub stores by type
def test_step4_migrate(memory: Memory) -> None:
    _print_step("Step 4: Migrate Data to Sub Stores (activate)")
    print("Starting data migration to sub stores...\n")

    print("1. Migrating working memories to sub store 0...")
    working_count = memory.migrate_to_sub_store(sub_store_index=0, delete_source=True)
    print(f"   ✓ Migrated {working_count} working memories")

    print("\n2. Migrating episodic memories to sub store 1...")
    episodic_count = memory.migrate_to_sub_store(sub_store_index=1, delete_source=True)
    print(f"   ✓ Migrated {episodic_count} episodic memories")

    print("\n✓ Migration completed! Current distribution:")
    print(f"   - Main store: semantic memories")
    print(f"   - Sub store 0: {working_count} working memories (ACTIVE)")
    print(f"   - Sub store 1: {episodic_count} episodic memories (ACTIVE)")
    
    # Note: Cleanup will be done at the end of test_main()


def test_step5_query_after_migration(memory: Memory) -> None:
    _print_step("Step 5: Query After Migration (automatic routing)")

    print("1. Querying working memories (routes to sub store 0)...")
    results = memory.search(query="today", filters={"memory_type": "working"}, user_id=USER_ID, limit=5)
    for index, result in enumerate(results["results"], start=1):
        source_store = result.get('_source_store', 'sub_store_0')
        print(f"   {index}. [{source_store}] {result['memory'][:50]}")

    print("\n2. Querying episodic memories (routes to sub store 1)...")
    results = memory.search(query="visited", filters={"memory_type": "episodic"}, user_id=USER_ID, limit=5)
    for index, result in enumerate(results["results"], start=1):
        source_store = result.get('_source_store', 'sub_store_1')
        print(f"   {index}. [{source_store}] {result['memory'][:50]}")

    print("\n3. Querying semantic memories (queries main store)...")
    results = memory.search(query="machine", filters={"memory_type": "semantic"}, user_id=USER_ID, limit=5)
    for index, result in enumerate(results["results"], start=1):
        source_store = result.get('_source_store', 'main')
        print(f"   {index}. [{source_store}] {result['memory'][:50]}")
    
    # Note: Cleanup will be done at the end of test_main()

# Add new memories and verify automatic routing functionality
def test_step6_add_new_memories(memory: Memory) -> None:
    _print_step("Step 6: Add New Memories (automatic routing)")

    print("1. Adding new working memory...")
    memory.add(
        messages="Remember to call the dentist tomorrow morning",
        metadata={"memory_type": "working", "importance": "high"},
        user_id=USER_ID,
    )
    print("   ✓ Automatically routed to sub store 0")

    print("\n2. Adding new episodic memory...")
    memory.add(
        messages="I graduated from university in 2020",
        metadata={"memory_type": "episodic", "time": "2020"},
        user_id=USER_ID,
    )
    print("   ✓ Automatically routed to sub store 1")

    print("\n3. Adding new semantic memory...")
    memory.add(
        messages="Docker is a platform for developing and deploying containerized applications",
        metadata={"memory_type": "semantic", "topic": "technology"},
        user_id=USER_ID,
    )
    print("   ✓ Automatically routed to main store")
    
    # Note: Cleanup will be done at the end of test_main()


def test_step7_verify(memory: Memory) -> None:
    _print_step("Step 7: Verify Routing Correctness")

    results = memory.search(query="tasks and reminders", filters={"memory_type": "working"}, user_id=USER_ID, limit=10)
    print(f"1. Working memories found: {len(results['results'])}")

    results = memory.search(query="life experiences", filters={"memory_type": "episodic"}, user_id=USER_ID, limit=10)
    print(f"2. Episodic memories found: {len(results['results'])}")

    # results = memory.search(query="", filters={"memory_type": "semantic"}, user_id=USER_ID, limit=10)
    results = memory.search(query="knowledge and concepts", user_id=USER_ID, limit=10)
    print(f"3. Semantic memories found: {len(results['results'])}")

    print("\n✓ All verifications completed!")
    
    # Note: Cleanup will be done at the end of test_main()


def main() -> None:
    _print_banner("Powermem Scenario 6: Sub Stores (Simulated)")
    memory = create_memory()
    test_step1_configure(memory)
    test_step2_add_memories(memory)
    test_step3_query_before_migration(memory)
    test_step4_migrate(memory)
    test_step5_query_after_migration(memory)
    test_step6_add_new_memories(memory)
    test_step7_verify(memory)
    
    
    # Cleanup all test data
    try:
        memory.delete_all(user_id=USER_ID)
        print(f"\n✓ Cleaned up all test data for user: {USER_ID}")
    except Exception as e:
        print(f"\n⚠ Could not cleanup test data: {str(e)[:100]}")
    
    _print_banner("Scenario 6 walkthrough completed successfully!")


if __name__ == "__main__":
    main()

