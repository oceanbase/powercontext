"""Executable walkthrough for `scenario_4_async_operations.md`.

Run `python docs/examples/scenario_4_async_operations.py` to execute every code
sample from the Scenario 4 documentation in a single asynchronous workflow.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Dict, Optional

import pytest
from dotenv import load_dotenv

try:
    from fastapi import FastAPI, HTTPException  # type: ignore
    from pydantic import BaseModel  # type: ignore
    from contextlib import asynccontextmanager
except Exception:  # pragma: no cover - optional dependencies
    FastAPI = None  # type: ignore
    BaseModel = object  # type: ignore
    asynccontextmanager = None  # type: ignore

from powermem import AsyncMemory, auto_config


def _ensure_env_loaded() -> None:
    current_dir = os.path.dirname(__file__)
    env_path = os.path.join(current_dir, "..", "..", ".env")
    env_example_path = os.path.join(current_dir, "..", "..", ".env.example")

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

CONFIG = auto_config()


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
    return entry.get("memory") or entry.get("content") or entry.get("text") or str(entry)


async def _create_async_memory(agent_id: Optional[str] = None) -> AsyncMemory:
    async_memory = AsyncMemory(config=CONFIG, agent_id=agent_id)
    await async_memory.initialize()
    return async_memory


async def _safe_delete_all(memory: AsyncMemory, *, user_id: Optional[str] = None) -> None:
    try:
        await memory.delete_all(user_id=user_id)
    except Exception:
        pass


@pytest.mark.asyncio
async def test_step1_initialize_async_memory() -> None:
    _print_step("Step 1: Initialize Async Memory")
    async_memory = await _create_async_memory()
    print("✓ AsyncMemory initialized successfully!")
    await _safe_delete_all(async_memory)


@pytest.mark.asyncio
async def test_step2_add_memories_async() -> None:
    _print_step("Step 2: Add Memories Asynchronously")
    async_memory = await _create_async_memory()
    user_id = "scenario4_user_step2"
    await _safe_delete_all(async_memory, user_id=user_id)

    result = await async_memory.add(
        "User likes Python programming",
        user_id=user_id,
    )

    results_list = _get_results(result)
    if results_list:
        memory_id = results_list[0].get("id", "N/A")
        print(f"✓ Memory added! ID: {memory_id}")
    else:
        print("✓ Memory operation completed (may have been deduplicated)")

    await _safe_delete_all(async_memory, user_id=user_id)


@pytest.mark.asyncio
async def test_step3_concurrent_additions() -> None:
    _print_step("Step 3: Concurrent Memory Operations")
    async_memory = await _create_async_memory()
    user_id = "scenario4_user_step3"
    await _safe_delete_all(async_memory, user_id=user_id)

    memories = [
        "User likes Python programming",
        "User prefers email support",
        "User works as a software engineer",
        "User favorite color is blue",
    ]

    tasks = [async_memory.add(mem, user_id=user_id) for mem in memories]
    results = await asyncio.gather(*tasks)
    print(f"✓ Added {len(results)} memories concurrently")

    await _safe_delete_all(async_memory, user_id=user_id)


@pytest.mark.asyncio
async def test_step4_async_search() -> None:
    _print_step("Step 4: Async Search")
    async_memory = await _create_async_memory()
    user_id = "scenario4_user_step4"
    await _safe_delete_all(async_memory, user_id=user_id)

    await async_memory.add("User likes Python", user_id=user_id)
    await async_memory.add("User prefers email", user_id=user_id)

    results = await async_memory.search(
        query="user preferences",
        user_id=user_id,
        limit=5,
    )

    matches = _get_results(results)
    print(f"Found {len(matches)} memories:")
    for result in matches:
        print(f"  - {_memory_text(result)}")

    await _safe_delete_all(async_memory, user_id=user_id)


@pytest.mark.asyncio
async def test_step5_batch_processing() -> None:
    _print_step("Step 5: Batch Processing")
    async_memory = await _create_async_memory()
    user_id = "scenario4_user_step5"
    await _safe_delete_all(async_memory, user_id=user_id)

    memories = [f"Memory {i}: User preference {i}" for i in range(25)]
    batch_size = 10
    for i in range(0, len(memories), batch_size):
        batch = memories[i : i + batch_size]
        tasks = [async_memory.add(mem, user_id=user_id) for mem in batch]
        await asyncio.gather(*tasks)
        print(f"✓ Processed batch {i // batch_size + 1}")

    await _safe_delete_all(async_memory, user_id=user_id)


@pytest.mark.asyncio
async def test_step6_async_intelligent_memory() -> None:
    _print_step("Step 6: Async with Intelligent Memory")
    async_memory = await _create_async_memory()
    user_id = "scenario4_user_step6"
    await _safe_delete_all(async_memory, user_id=user_id)

    result = await async_memory.add(
        messages=[
            {"role": "user", "content": "I'm Alice, a software engineer"},
            {"role": "assistant", "content": "Nice to meet you!"},
        ],
        user_id=user_id,
        infer=True,
    )

    extracted = _get_results(result)
    print(f"✓ Processed conversation, extracted {len(extracted)} memories:")
    for mem in extracted:
        print(f"  - {_memory_text(mem)}")

    await _safe_delete_all(async_memory, user_id=user_id)


@pytest.mark.asyncio
async def test_step7_async_update_delete() -> None:
    _print_step("Step 7: Async Update and Delete")
    async_memory = await _create_async_memory()
    user_id = "scenario4_user_step7"
    await _safe_delete_all(async_memory, user_id=user_id)

    result = await async_memory.add(
        "User likes Python",
        user_id=user_id,
        infer=False,
    )
    print(f"✓ Add memory: {_memory_text(result)}")

    results_list = _get_results(result)
    print(f"✓ Add memory: {results_list[0].get('memory', 'N/A')}")
    
    if not results_list:
        raise RuntimeError("Cannot update/delete: memory was not added")

    memory_id = results_list[0].get("id")
    if not memory_id:
        raise RuntimeError("Cannot update/delete: memory ID not found")

    updated = await async_memory.update(
        memory_id=memory_id,
        content="User loves Python programming",
    )
    print(f"✓ Updated memory: {_memory_text(updated)}")
    print(f"✓ Updated memory: {updated.get('data', 'N/A')}")

    success = await async_memory.delete(memory_id)
    if success:
        print(f"✓ Deleted memory {memory_id}")

    await _safe_delete_all(async_memory, user_id=user_id)


def test_step8_fastapi_integration() -> None:
    _print_step("Step 8: FastAPI Integration Example")
    if FastAPI is None or asynccontextmanager is None or BaseModel is object:  # type: ignore
        print("FastAPI or Pydantic not installed. Skipping FastAPI integration demo.")
        return

    config = CONFIG
    async_memory_holder: Dict[str, AsyncMemory] = {}

    @asynccontextmanager  # type: ignore[arg-type]
    async def lifespan(app: FastAPI):  # pragma: no cover - runtime demo
        async_memory = AsyncMemory(config=config)
        await async_memory.initialize()
        async_memory_holder["instance"] = async_memory
        try:
            yield
        finally:
            async_memory_holder.pop("instance", None)

    app = FastAPI(lifespan=lifespan)

    class MemoryRequest(BaseModel):  # type: ignore[misc]
        memory: str
        user_id: str

    @app.post("/memories")
    async def add_memory(request: MemoryRequest):  # type: ignore[override]
        try:
            async_memory = async_memory_holder.get("instance")
            if async_memory is None:
                raise RuntimeError("AsyncMemory not initialized")
            return await async_memory.add(request.memory, user_id=request.user_id)
        except Exception as exc:  # pragma: no cover - runtime demo
            raise HTTPException(status_code=500, detail=str(exc))

    @app.post("/memories/search")
    async def search_memories(query: str, user_id: str):  # type: ignore[override]
        try:
            async_memory = async_memory_holder.get("instance")
            if async_memory is None:
                raise RuntimeError("AsyncMemory not initialized")
            return await async_memory.search(query=query, user_id=user_id)
        except Exception as exc:  # pragma: no cover - runtime demo
            raise HTTPException(status_code=500, detail=str(exc))

    print("✓ FastAPI app configured. (Server creation demonstrated; not started automatically.)")


@pytest.mark.asyncio
async def test_complete_example() -> None:
    _print_step("Complete Example")
    async_memory = await _create_async_memory()
    user_id = "scenario4_complete_user"
    await _safe_delete_all(async_memory, user_id=user_id)

    print("[Step 1] Adding Memories Concurrently")
    memories = [
        "User likes Python programming",
        "User prefers email support",
        "User works as a software engineer",
    ]
    tasks = [async_memory.add(mem, user_id=user_id) for mem in memories]
    results = await asyncio.gather(*tasks)
    print(f"✓ Added {len(results)} memories concurrently")

    print("\n[Step 2] Searching Memories")
    search_results = await async_memory.search(
        query="user preferences",
        user_id=user_id,
    )
    matches = _get_results(search_results)
    print(f"Found {len(matches)} memories:")
    for result in matches:
        print(f"  - {_memory_text(result)}")

    print("\n[Step 3] Batch Processing")
    batch_memories = [f"Memory {i}" for i in range(20)]
    batch_size = 5
    for i in range(0, len(batch_memories), batch_size):
        batch = batch_memories[i : i + batch_size]
        tasks = [async_memory.add(mem, user_id=user_id) for mem in batch]
        await asyncio.gather(*tasks)
        print(f"✓ Processed batch {i // batch_size + 1}")

    await _safe_delete_all(async_memory, user_id=user_id)


async def extension_concurrent_searches() -> None:
    _print_step("Extension Exercise 1: Concurrent Searches")
    async_memory = await _create_async_memory()
    user_id = "scenario4_extension_user"
    await _safe_delete_all(async_memory, user_id=user_id)

    # Seed some data
    await async_memory.add("User likes Python", user_id=user_id)
    await async_memory.add("User enjoys automation projects", user_id=user_id)

    queries = ["user preferences", "user interests", "user information"]
    tasks = [async_memory.search(query=q, user_id=user_id) for q in queries]
    results = await asyncio.gather(*tasks)

    for idx, res in enumerate(results, start=1):
        print(f"Search {idx} returned {len(_get_results(res))} items")
        for result in _get_results(res):
            print(f"  - {_memory_text(result)}")

    await _safe_delete_all(async_memory, user_id=user_id)


async def extension_async_error_handling() -> None:
    _print_step("Extension Exercise 2: Async with Error Handling")
    async_memory = await _create_async_memory()
    user_id = "scenario4_extension_error"
    await _safe_delete_all(async_memory, user_id=user_id)

    async def safe_add(memory_text: str) -> Optional[Dict[str, Any]]:
        try:
            result = await async_memory.add(memory_text, user_id=user_id)
            print(f"✓ Added memory: {memory_text}")
            return result
        except Exception as exc:  # pragma: no cover - demo
            print(f"Error adding memory: {exc}")
            return None

    await safe_add("User explores async programming")

    await _safe_delete_all(async_memory, user_id=user_id)


async def extension_rate_limiting() -> None:
    _print_step("Extension Exercise 3: Rate Limiting")
    async_memory = await _create_async_memory()
    user_id = "scenario4_extension_rate"
    await _safe_delete_all(async_memory, user_id=user_id)

    memories = [f"Rate limited memory {i}" for i in range(15)]
    semaphore = asyncio.Semaphore(5)

    async def add_with_limit(memory_text: str) -> Dict[str, Any]:
        async with semaphore:
            return await async_memory.add(memory_text, user_id=user_id, infer=False)

    tasks = [add_with_limit(mem) for mem in memories]
    results = await asyncio.gather(*tasks)
    
    # Count actually added memories
    actual_count = 0
    for result in results:
        results_list = _get_results(result)
        actual_count += len(results_list)
    
    print(f"✓ Added {actual_count} out of {len(memories)} memories with rate limiting")

    await _safe_delete_all(async_memory, user_id=user_id)


@pytest.mark.asyncio
async def test_async_get_all() -> None:
    """Test async get_all operation: verify that all memories can be retrieved asynchronously"""
    _print_step("Async Get All: Retrieving All Memories")
    async_memory = await _create_async_memory()
    user_id = "scenario4_getall_user"
    await _safe_delete_all(async_memory, user_id=user_id)

    print("1. Adding multiple memories...")
    memories = [
        "User likes Python",
        "User prefers email",
        "User works as engineer",
        "User favorite color is blue",
    ]
    tasks = [async_memory.add(mem, user_id=user_id) for mem in memories]
    await asyncio.gather(*tasks)
    print(f"   ✓ Added {len(memories)} memories")

    print("\n2. Getting all memories asynchronously...")
    all_memories = await async_memory.get_all(user_id=user_id)
    all_results = _get_results(all_memories)
    print(f"   ✓ Retrieved {len(all_results)} memories:")
    for idx, mem in enumerate(all_results, start=1):
        print(f"      {idx}. {_memory_text(mem)}")

    print("\n3. Testing with limit...")
    limited = await async_memory.get_all(user_id=user_id, limit=2)
    limited_results = _get_results(limited)
    print(f"   ✓ Retrieved {len(limited_results)} memories with limit=2")

    await _safe_delete_all(async_memory, user_id=user_id)


@pytest.mark.asyncio
async def test_async_get() -> None:
    """Test async get operation: verify that a single memory can be retrieved asynchronously"""
    _print_step("Async Get: Retrieving Single Memory")
    async_memory = await _create_async_memory()
    user_id = "scenario4_get_user"
    await _safe_delete_all(async_memory, user_id=user_id)

    print("1. Adding memory...")
    result = await async_memory.add("User likes async operations", user_id=user_id)
    results_list = _get_results(result)
    
    if not results_list:
        print("   ⚠ Memory not added, skipping get test")
        await _safe_delete_all(async_memory, user_id=user_id)
        return

    memory_id = results_list[0].get("id")
    if not memory_id:
        print("   ⚠ Memory ID not found, skipping get test")
        await _safe_delete_all(async_memory, user_id=user_id)
        return

    print(f"   ✓ Added memory with ID: {memory_id}")

    print("\n2. Getting memory asynchronously...")
    retrieved = await async_memory.get(memory_id=memory_id, user_id=user_id)
    if retrieved:
        print(f"   ✓ Retrieved memory: {_memory_text(retrieved)}")
        if isinstance(retrieved, dict):
            print(f"   Memory ID: {retrieved.get('id', 'N/A')}")
            print(f"   User ID: {retrieved.get('user_id', 'N/A')}")
    else:
        print("   ⚠ Memory not found")

    await _safe_delete_all(async_memory, user_id=user_id)


@pytest.mark.asyncio
async def test_async_batch_update() -> None:
    """Test async batch update: verify that multiple memories can be updated concurrently"""
    _print_step("Async Batch Update: Concurrent Updates")
    async_memory = await _create_async_memory()
    user_id = "scenario4_batch_update_user"
    await _safe_delete_all(async_memory, user_id=user_id)

    print("1. Adding memories...")
    memories = [
        "User likes Python",
        "User prefers email",
        "User works as engineer",
    ]
    tasks = [async_memory.add(mem, user_id=user_id, infer=False) for mem in memories]
    results = await asyncio.gather(*tasks)

    memory_ids = []
    for result in results:
        results_list = _get_results(result)
        if results_list and results_list[0].get("id"):
            memory_ids.append(results_list[0].get("id"))

    print(f"   ✓ Added {len(memory_ids)} memories")

    print("\n2. Batch updating memories concurrently...")
    update_tasks = [
        async_memory.update(
            memory_id=mem_id,
            content=f"Updated: {memories[i]}",
            user_id=user_id
        )
        for i, mem_id in enumerate(memory_ids)
    ]
    updated_results = await asyncio.gather(*update_tasks)
    print(f"   ✓ Updated {len(updated_results)} memories concurrently")

    print("\n3. Verifying updates...")
    for mem_id in memory_ids:
        retrieved = await async_memory.get(memory_id=mem_id, user_id=user_id)
        if retrieved:
            print(f"   ✓ Memory {mem_id}: {_memory_text(retrieved)[:50]}...")

    await _safe_delete_all(async_memory, user_id=user_id)


@pytest.mark.asyncio
async def test_async_batch_delete() -> None:
    """Test async batch delete: verify that multiple memories can be deleted concurrently"""
    _print_step("Async Batch Delete: Concurrent Deletions")
    async_memory = await _create_async_memory()
    user_id = "scenario4_batch_delete_user"
    await _safe_delete_all(async_memory, user_id=user_id)

    print("1. Adding memories...")
    memories = [
        "Memory to delete 1",
        "Memory to delete 2",
        "Memory to delete 3",
        "Memory to keep",
    ]
    tasks = [async_memory.add(mem, user_id=user_id, infer=False) for mem in memories]
    results = await asyncio.gather(*tasks)

    memory_ids = []
    for result in results:
        results_list = _get_results(result)
        if results_list and results_list[0].get("id"):
            memory_ids.append(results_list[0].get("id"))

    print(f"   ✓ Added {len(memory_ids)} memories")

    print("\n2. Batch deleting memories concurrently...")
    delete_tasks = [
        async_memory.delete(memory_id=mem_id, user_id=user_id)
        for mem_id in memory_ids[:3]  # Delete first 3
    ]
    delete_results = await asyncio.gather(*delete_tasks)
    deleted_count = sum(1 for r in delete_results if r)
    print(f"   ✓ Deleted {deleted_count} memories concurrently")

    print("\n3. Verifying deletions...")
    all_memories = await async_memory.get_all(user_id=user_id)
    remaining = _get_results(all_memories)
    print(f"   Remaining memories: {len(remaining)}")
    for mem in remaining:
        print(f"      - {_memory_text(mem)}")

    await _safe_delete_all(async_memory, user_id=user_id)


@pytest.mark.asyncio
async def test_async_metadata_filtering() -> None:
    """Test async metadata filtering: verify that memories with metadata can be searched and filtered asynchronously"""
    _print_step("Async Metadata Filtering: Filtering with Metadata")
    async_memory = await _create_async_memory()
    user_id = "scenario4_metadata_user"
    await _safe_delete_all(async_memory, user_id=user_id)

    print("1. Adding memories with metadata...")
    await async_memory.add(
        "User likes Python",
        user_id=user_id,
        metadata={"category": "technical", "priority": "high"}
    )
    await async_memory.add(
        "User prefers email",
        user_id=user_id,
        metadata={"category": "communication", "priority": "medium"}
    )
    await async_memory.add(
        "User works at Google",
        user_id=user_id,
        metadata={"category": "professional", "priority": "high"}
    )
    print("   ✓ Added memories with metadata")

    print("\n2. Searching with metadata filter...")
    filtered_results = await async_memory.search(
        query="user",
        user_id=user_id,
        filters={"metadata.category": "technical"}
    )
    filtered_list = _get_results(filtered_results)
    print(f"   Found {len(filtered_list)} memories with category='technical':")
    for mem in filtered_list:
        print(f"      - {_memory_text(mem)}")

    print("\n3. Using get_all with filters...")
    all_filtered = await async_memory.get_all(
        user_id=user_id,
        filters={"metadata.priority": "high"}
    )
    all_filtered_list = _get_results(all_filtered)
    print(f"   Found {len(all_filtered_list)} memories with priority='high':")
    for mem in all_filtered_list:
        print(f"      - {_memory_text(mem)}")

    await _safe_delete_all(async_memory, user_id=user_id)


@pytest.mark.asyncio
async def test_async_multi_agent() -> None:
    """Test async multi-agent scenario: verify the behavior of async operations in a multi-agent environment"""
    _print_step("Async Multi-Agent: Multi-Agent Async Operations")
    user_id = "scenario4_multi_agent_user"

    agent1 = await _create_async_memory(agent_id="scenario4_agent1")
    agent2 = await _create_async_memory(agent_id="scenario4_agent2")

    await _safe_delete_all(agent1, user_id=user_id)
    await _safe_delete_all(agent2, user_id=user_id)

    print("1. Each agent adding memories concurrently...")
    tasks = [
        agent1.add("Agent1: User prefers email", user_id=user_id),
        agent2.add("Agent2: User likes Python", user_id=user_id),
        agent1.add("Agent1: User works as engineer", user_id=user_id),
        agent2.add("Agent2: User favorite color is blue", user_id=user_id),
    ]
    await asyncio.gather(*tasks)
    print("   ✓ All agents added memories")

    print("\n2. Each agent searching their own memories...")
    search_tasks = [
        agent1.search(query="user", user_id=user_id, agent_id="scenario4_agent1"),
        agent2.search(query="user", user_id=user_id, agent_id="scenario4_agent2"),
    ]
    search_results = await asyncio.gather(*search_tasks)
    
    for idx, results in enumerate(search_results, start=1):
        agent_memories = _get_results(results)
        print(f"   Agent{idx} found {len(agent_memories)} memories:")
        for mem in agent_memories:
            print(f"      - {_memory_text(mem)}")

    print("\n3. Cross-agent search...")
    cross_results = await agent1.search(query="user", user_id=user_id)
    cross_memories = _get_results(cross_results)
    print(f"   Found {len(cross_memories)} memories across all agents")

    await _safe_delete_all(agent1, user_id=user_id)
    await _safe_delete_all(agent2, user_id=user_id)


@pytest.mark.asyncio
async def test_async_concurrent_mixed_operations() -> None:
    """Test async concurrent mixed operations: simultaneously performing add, update, delete, and search operations"""
    _print_step("Async Concurrent Mixed Operations: Add, Update, Delete, Search")
    async_memory = await _create_async_memory()
    user_id = "scenario4_mixed_ops_user"
    await _safe_delete_all(async_memory, user_id=user_id)

    print("1. Concurrent add operations...")
    add_tasks = [
        async_memory.add(f"Memory {i}", user_id=user_id, infer=False)
        for i in range(5)
    ]
    add_results = await asyncio.gather(*add_tasks)
    memory_ids = []
    for result in add_results:
        results_list = _get_results(result)
        if results_list and results_list[0].get("id"):
            memory_ids.append(results_list[0].get("id"))
    print(f"   ✓ Added {len(memory_ids)} memories")

    print("\n2. Concurrent mixed operations (add, update, delete, search)...")
    mixed_tasks = [
        async_memory.add("New memory", user_id=user_id, infer=False),
        async_memory.update(memory_id=memory_ids[0], content="Updated memory", user_id=user_id) if memory_ids else None,
        async_memory.delete(memory_id=memory_ids[1], user_id=user_id) if len(memory_ids) > 1 else None,
        async_memory.search(query="memory", user_id=user_id),
    ]
    # Filter out None tasks
    mixed_tasks = [t for t in mixed_tasks if t is not None]
    mixed_results = await asyncio.gather(*mixed_tasks)
    print(f"   ✓ Completed {len(mixed_results)} mixed operations concurrently")

    print("\n3. Final state...")
    all_memories = await async_memory.get_all(user_id=user_id)
    final_results = _get_results(all_memories)
    print(f"   Total memories: {len(final_results)}")

    await _safe_delete_all(async_memory, user_id=user_id)


@pytest.mark.asyncio
async def test_async_timeout_handling() -> None:
    """Test async timeout handling: verify the handling of async operations in timeout situations"""
    _print_step("Async Timeout Handling: Timeout Management")
    async_memory = await _create_async_memory()
    user_id = "scenario4_timeout_user"
    await _safe_delete_all(async_memory, user_id=user_id)

    print("1. Testing with timeout wrapper...")
    async def add_with_timeout(memory_text: str, timeout: float = 5.0):
        try:
            return await asyncio.wait_for(
                async_memory.add(memory_text, user_id=user_id, infer=False),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            print(f"   ⚠ Timeout while adding: {memory_text}")
            return None

    result = await add_with_timeout("Memory with timeout check")
    if result:
        print("   ✓ Memory added within timeout")
    else:
        print("   ⚠ Memory addition timed out or failed")

    await _safe_delete_all(async_memory, user_id=user_id)


@pytest.mark.asyncio
async def test_async_error_recovery() -> None:
    """Test async error recovery: verify the recovery capability of async operations when errors occur"""
    _print_step("Async Error Recovery: Error Handling and Recovery")
    async_memory = await _create_async_memory()
    user_id = "scenario4_error_recovery_user"
    await _safe_delete_all(async_memory, user_id=user_id)

    print("1. Testing error recovery with invalid operations...")
    async def safe_operation(operation_func, *args, **kwargs):
        try:
            return await operation_func(*args, **kwargs)
        except Exception as e:
            print(f"   ⚠ Error caught: {type(e).__name__}: {e}")
            return None

    # Try to get non-existent memory
    result1 = await safe_operation(async_memory.get, memory_id=99999, user_id=user_id)
    if result1 is None:
        print("   ✓ Handled non-existent memory gracefully")

    # Try to update non-existent memory
    result2 = await safe_operation(
        async_memory.update,
        memory_id=99999,
        content="Updated",
        user_id=user_id
    )
    if result2 is None:
        print("   ✓ Handled update of non-existent memory gracefully")

    # Try to delete non-existent memory
    result3 = await safe_operation(async_memory.delete, memory_id=99999, user_id=user_id)
    if result3 is False or result3 is None:
        print("   ✓ Handled delete of non-existent memory gracefully")

    print("\n2. Continuing with valid operations after errors...")
    valid_result = await async_memory.add("Valid memory after errors", user_id=user_id)
    if valid_result:
        print("   ✓ Successfully added memory after error handling")

    await _safe_delete_all(async_memory, user_id=user_id)


async def main() -> None:
    _print_banner("Powermem Scenario 4: Async Operations")

    await test_step1_initialize_async_memory()
    await test_step2_add_memories_async()
    await test_step3_concurrent_additions()
    await test_step4_async_search()
    await test_step5_batch_processing()
    await test_step6_async_intelligent_memory()
    await test_step7_async_update_delete()
    test_step8_fastapi_integration()
    await test_complete_example()
    await extension_concurrent_searches()
    await extension_async_error_handling()
    await extension_rate_limiting()
    
    # Additional comprehensive tests
    await test_async_get_all()
    await test_async_get()
    await test_async_batch_update()
    await test_async_batch_delete()
    await test_async_metadata_filtering()
    await test_async_multi_agent()
    await test_async_concurrent_mixed_operations()
    await test_async_timeout_handling()
    await test_async_error_recovery()

    _print_banner("Scenario 4 walkthrough completed successfully!")


if __name__ == "__main__":
    asyncio.run(main())

