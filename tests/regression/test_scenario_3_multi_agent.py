"""Executable walkthrough for `scenario_3_multi_agent.md`.

Run `python docs/examples/scenario_3_multi_agent.py` to execute every code
sample from the Scenario 3 documentation in a single pass.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from powermem import auto_config, create_memory


def _ensure_env_loaded() -> None:
    current_dir = os.path.dirname(__file__)
    env_path = os.path.join(current_dir, "..", "..", ".env")
    env_example_path = os.path.join(current_dir, "..", "..", "env.example")

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

# Configuration shared across examples
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


def _safe_delete_all(memory, *, user_id: Optional[str] = None, run_id: Optional[str] = None) -> None:
    try:
        memory.delete_all(user_id=user_id, run_id=run_id)
    except Exception:
        pass


def _create_agent(agent_id: str):
    return create_memory(config=CONFIG, agent_id=agent_id)


def test_step1_create_agents() -> None:
    _print_step("Step 1: Create Multiple Agents")
    support_agent = _create_agent("support_agent")
    sales_agent = _create_agent("sales_agent")
    tech_agent = _create_agent("tech_agent")

    # Clean up any previous state
    _safe_delete_all(support_agent)
    _safe_delete_all(sales_agent)
    _safe_delete_all(tech_agent)

    print("✓ Created memory instances for:")
    print("  - Support Agent")
    print("  - Sales Agent")
    print("  - Technical Agent")


def test_step2_add_agent_specific_memories() -> None:
    _print_step("Step 2: Add Agent-Specific Memories")
    customer_id = "scenario3_customer_001"
    support_agent = _create_agent("support_agent")
    sales_agent = _create_agent("sales_agent")
    tech_agent = _create_agent("tech_agent")

    for agent in (support_agent, sales_agent, tech_agent):
        _safe_delete_all(agent, user_id=customer_id)

    support_agent.add(
        "Customer prefers email support over phone calls",
        user_id=customer_id,
        metadata={"category": "communication_preference"},
    )
    sales_agent.add(
        "Customer interested in AI-powered features and automation",
        user_id=customer_id,
        metadata={"category": "product_interest"},
    )
    tech_agent.add(
        "Customer uses Python and PostgreSQL in their tech stack",
        user_id=customer_id,
        metadata={"category": "technical_info"},
    )
    print("✓ Added memories for each agent")

    for agent in (support_agent, sales_agent, tech_agent):
        _safe_delete_all(agent, user_id=customer_id)


def test_step3_agent_specific_search() -> None:
    _print_step("Step 3: Agent-Specific Search")
    customer_id = "scenario3_customer_002"
    support_agent = _create_agent("support_agent")
    sales_agent = _create_agent("sales_agent")

    _safe_delete_all(support_agent, user_id=customer_id)
    _safe_delete_all(sales_agent, user_id=customer_id)

    support_agent.add("Customer prefers email support", user_id=customer_id)
    sales_agent.add("Customer interested in AI features", user_id=customer_id)

    print("Support Agent Search:")
    support_results = support_agent.search(
        query="customer preferences",
        user_id=customer_id,
        agent_id="support_agent",
    )
    for result in _get_results(support_results):
        print(f"  - {_memory_text(result)}")

    print("\nSales Agent Search:")
    sales_results = sales_agent.search(
        query="customer interests",
        user_id=customer_id,
        agent_id="sales_agent",
    )
    for result in _get_results(sales_results):
        print(f"  - {_memory_text(result)}")

    _safe_delete_all(support_agent, user_id=customer_id)
    _safe_delete_all(sales_agent, user_id=customer_id)


def test_step4_cross_agent_search() -> None:
    _print_step("Step 4: Cross-Agent Search")
    customer_id = "scenario3_customer_003"
    support_agent = _create_agent("support_agent")
    sales_agent = _create_agent("sales_agent")
    tech_agent = _create_agent("tech_agent")

    for agent in (support_agent, sales_agent, tech_agent):
        _safe_delete_all(agent, user_id=customer_id)

    support_agent.add("Customer prefers email support", user_id=customer_id)
    sales_agent.add("Customer interested in AI features", user_id=customer_id)
    tech_agent.add("Customer uses Python and PostgreSQL", user_id=customer_id)

    print("Cross-Agent Search:")
    all_results = support_agent.search(
        query="customer information",
        user_id=customer_id,
    )

    results = _get_results(all_results)
    print(f"Found {len(results)} memories across all agents:")
    for result in results:
        agent_id = result.get("agent_id", "Unknown")
        print(f"  [{agent_id}] {_memory_text(result)}")

    for agent in (support_agent, sales_agent, tech_agent):
        _safe_delete_all(agent, user_id=customer_id)


def test_step5_project_collaboration() -> None:
    _print_step("Step 5: Project Collaboration")
    project_id = "scenario3_project_ai_platform"

    alice_dev = _create_agent("alice_dev")
    bob_dev = _create_agent("bob_dev")
    charlie_qa = _create_agent("charlie_qa")

    for agent in (alice_dev, bob_dev, charlie_qa):
        _safe_delete_all(agent, run_id=project_id)

    alice_dev.add(
        "Implemented user authentication module with JWT tokens",
        user_id="alice",
        run_id=project_id,
        metadata={"scope": "development", "module": "authentication"},
    )

    bob_dev.add(
        "Created database schema for user profiles",
        user_id="bob",
        run_id=project_id,
        metadata={"scope": "development", "module": "database"},
    )

    charlie_qa.add(
        "Found critical bug in user registration flow",
        user_id="charlie",
        run_id=project_id,
        metadata={"scope": "testing", "issue_type": "bug"},
    )

    print("Project Status Search:")
    project_results = alice_dev.search(
        query="project status and progress",
        run_id=project_id,
    )

    for result in _get_results(project_results):
        agent_id = result.get("agent_id", "Unknown")
        scope = result.get("metadata", {}).get("scope", "Unknown")
        print(f"  [{agent_id}] [{scope}] {_memory_text(result)}")

    for agent in (alice_dev, bob_dev, charlie_qa):
        _safe_delete_all(agent, run_id=project_id)


def test_step6_memory_scopes() -> None:
    _print_step("Step 6: Memory Scopes")
    agent = _create_agent("demo_agent")
    _safe_delete_all(agent, user_id="user123")

    agent.add(
        "Agent-specific memory",
        user_id="user123",
        metadata={"scope": "AGENT"},
    )
    agent.add(
        "User-specific memory",
        user_id="user123",
        metadata={"scope": "USER"},
    )

    print("Agent-scoped memories:")
    results = agent.search(
        query="memories",
        user_id="user123",
        filters={"metadata.scope": "AGENT"}
    )
    for result in results.get('results', []):
        print(f"  - {result['memory']}")

    # Alternative: Search all memories and filter in Python
    print("\nAll memories:")
    all_results = agent.search(
        query="memories",
        user_id="user123"
    )
    for result in all_results.get('results', []):
        scope = result.get('metadata', {}).get('scope', 'AGENT')
        print(f"  [{scope}] {result['memory']}")

    _safe_delete_all(agent, user_id="user123")


def test_step7_memory_isolation() -> None:
    _print_step("Step 7: Memory Isolation")
    user_id = "scenario3_user_isolation"

    agent1 = _create_agent("scenario3_agent1")
    agent2 = _create_agent("scenario3_agent2")

    _safe_delete_all(agent1, user_id=user_id)
    _safe_delete_all(agent2, user_id=user_id)

    agent1.add("Agent1 is a support agent", user_id=user_id)
    agent2.add("Agent2 is a sales agent", user_id=user_id)

    print("Agent1 search:")
    results1 = agent1.search(query="memories", user_id=user_id, agent_id="scenario3_agent1")
    for result in _get_results(results1):
        print(f"  - {_memory_text(result)}")

    print("\nAgent2 search:")
    results2 = agent2.search(query="memories", user_id=user_id, agent_id="scenario3_agent2")
    for result in _get_results(results2):
        print(f"  - {_memory_text(result)}")

    _safe_delete_all(agent1, user_id=user_id)
    _safe_delete_all(agent2, user_id=user_id)


def test_complete_example() -> None:
    _print_step("Complete Example")
    customer_id = "scenario3_complete_customer"

    support_agent = _create_agent("support_agent")
    sales_agent = _create_agent("sales_agent")
    tech_agent = _create_agent("tech_agent")

    for agent in (support_agent, sales_agent, tech_agent):
        _safe_delete_all(agent, user_id=customer_id)

    print("[Step 1] Adding Agent-Specific Memories")
    support_agent.add(
        "Customer prefers email support over phone calls",
        user_id=customer_id,
        metadata={"category": "communication"},
    )
    sales_agent.add(
        "Customer interested in AI-powered features",
        user_id=customer_id,
        metadata={"category": "interest"},
    )
    tech_agent.add(
        "Customer uses Python and PostgreSQL",
        user_id=customer_id,
        metadata={"category": "technical"},
    )
    print("✓ Added memories for each agent")

    print("\n[Step 2] Agent-Specific Search")
    print("Support Agent:")
    support_results = support_agent.search(
        query="customer preferences",
        user_id=customer_id,
        agent_id="support_agent",
    )
    for result in _get_results(support_results):
        print(f"  - {_memory_text(result)}")

    print("\n[Step 3] Cross-Agent Search")
    all_results = support_agent.search(
        query="customer information",
        user_id=customer_id,
    )
    memories = _get_results(all_results)
    print(f"Found {len(memories)} memories across all agents:")
    for result in memories:
        agent_id = result.get("agent_id", "Unknown")
        print(f"  [{agent_id}] {_memory_text(result)}")

    for agent in (support_agent, sales_agent, tech_agent):
        _safe_delete_all(agent, user_id=customer_id)


def test_extension_team_collaboration() -> None:
    _print_step("Extension Exercise 1: Team Collaboration")
    project_id = "scenario3_extension_project_xyz"

    dev1 = _create_agent("scenario3_dev1")
    dev2 = _create_agent("scenario3_dev2")

    _safe_delete_all(dev1, run_id=project_id)
    _safe_delete_all(dev2, run_id=project_id)

    dev1.add("Implemented feature X", run_id=project_id)
    dev2.add("Implemented feature Y", run_id=project_id)

    results = dev1.search("project progress", run_id=project_id)
    print("Project progress results:")
    for result in _get_results(results):
        print(f"  - {_memory_text(result)}")

    _safe_delete_all(dev1, run_id=project_id)
    _safe_delete_all(dev2, run_id=project_id)


def test_extension_customer_service_team() -> None:
    _print_step("Extension Exercise 2: Customer Service Team")
    customer_id = "scenario3_extension_customer_123"

    agent1 = _create_agent("cs_agent_1")
    agent2 = _create_agent("cs_agent_2")

    _safe_delete_all(agent1, user_id=customer_id)
    _safe_delete_all(agent2, user_id=customer_id)

    agent1.add("Customer reported issue A", user_id=customer_id)
    agent2.add("Customer issue resolved", user_id=customer_id)

    history = agent1.search("customer issues", user_id=customer_id)
    print("Shared customer history:")
    for result in _get_results(history):
        print(f"  - {_memory_text(result)}")

    _safe_delete_all(agent1, user_id=customer_id)
    _safe_delete_all(agent2, user_id=customer_id)


def test_extension_memory_scopes() -> None:
    _print_step("Extension Exercise 3: Memory Scopes")
    agent = _create_agent("scenario3_scope_agent")
    _safe_delete_all(agent)

    agent.add("Private memory", metadata={"scope": "AGENT"})
    agent.add("Shared memory", metadata={"scope": "GROUP"})

    private = agent.search("memory", filters={"scope": "AGENT"})
    print("Private scope memories:")
    for result in _get_results(private):
        print(f"  - {_memory_text(result)}")

    print("\nShared memories (GROUP scope):")
    shared = agent.search("memory", filters={"scope": "GROUP"})
    for result in _get_results(shared):
        print(f"  - {_memory_text(result)}")

    _safe_delete_all(agent)


def test_multiple_agents_same_user() -> None:
    """Test multiple agents operating on the same user: verify that different agents can independently manage the same user's memories"""
    _print_step("Multiple Agents Same User: Independent Memory Management")
    user_id = "scenario3_multi_agent_user"
    
    agent1 = _create_agent("scenario3_agent_a")
    agent2 = _create_agent("scenario3_agent_b")
    agent3 = _create_agent("scenario3_agent_c")
    
    for agent in (agent1, agent2, agent3):
        _safe_delete_all(agent, user_id=user_id)
    
    print("1. Each agent adds memories for the same user...")
    agent1.add("Agent A: User prefers email", user_id=user_id, metadata={"source": "agent_a"})
    agent2.add("Agent B: User likes Python", user_id=user_id, metadata={"source": "agent_b"})
    agent3.add("Agent C: User works at Google", user_id=user_id, metadata={"source": "agent_c"})
    print("   ✓ All agents added memories")
    
    print("\n2. Each agent searches their own memories...")
    for agent, agent_id in [(agent1, "scenario3_agent_a"), (agent2, "scenario3_agent_b"), (agent3, "scenario3_agent_c")]:
        results = agent.search(query="user", user_id=user_id, agent_id=agent_id)
        agent_memories = _get_results(results)
        print(f"   {agent_id}: Found {len(agent_memories)} memories")
        for mem in agent_memories:
            print(f"      - {_memory_text(mem)}")
    
    print("\n3. Cross-agent search (all agents)...")
    all_results = agent1.search(query="user", user_id=user_id)
    all_memories = _get_results(all_results)
    print(f"   Found {len(all_memories)} memories across all agents")
    
    for agent in (agent1, agent2, agent3):
        _safe_delete_all(agent, user_id=user_id)


def test_agent_memory_update() -> None:
    """Test agent memory update: verify that an agent can update memories it added"""
    _print_step("Agent Memory Update: Updating Agent-Specific Memories")
    user_id = "scenario3_update_user"
    agent = _create_agent("scenario3_update_agent")
    
    _safe_delete_all(agent, user_id=user_id)
    
    print("1. Adding initial memory...")
    result = agent.add(
        "Customer prefers email support",
        user_id=user_id,
        metadata={"version": 1}
    )
    memory_id = None
    if isinstance(result, dict):
        results = result.get("results", [])
        if results and isinstance(results[0], dict):
            memory_id = results[0].get("id") or results[0].get("memory_id")
    
    if memory_id:
        print(f"   ✓ Added memory with ID: {memory_id}")
        
        print("\n2. Updating memory...")
        agent.update(
            memory_id=memory_id,
            content="Customer prefers email support and chat",
            user_id=user_id,
            metadata={"version": 2}
        )
        
        print("\n3. Verifying update...")
        updated = agent.get(memory_id=memory_id, user_id=user_id)
        if updated:
            print(f"   ✓ Updated memory: {_memory_text(updated)}")
            meta = updated.get("metadata", {}) if isinstance(updated, dict) else {}
            print(f"   Version: {meta.get('version', 'N/A')}")
    
    _safe_delete_all(agent, user_id=user_id)


def test_agent_memory_delete() -> None:
    """Test agent memory delete: verify that an agent can delete memories it added"""
    _print_step("Agent Memory Delete: Deleting Agent-Specific Memories")
    user_id = "scenario3_delete_user"
    agent = _create_agent("scenario3_delete_agent")
    
    _safe_delete_all(agent, user_id=user_id)
    
    print("1. Adding memories...")
    result1 = agent.add("Memory 1: Customer preference", user_id=user_id)
    result2 = agent.add("Memory 2: Customer interest", user_id=user_id)
    result3 = agent.add("Memory 3: Customer info", user_id=user_id)
    
    memory_ids = []
    for result in [result1, result2, result3]:
        if isinstance(result, dict):
            results = result.get("results", [])
            if results and isinstance(results[0], dict):
                mem_id = results[0].get("id") or results[0].get("memory_id")
                if mem_id:
                    memory_ids.append(mem_id)
    
    print(f"   ✓ Added {len(memory_ids)} memories")
    
    print("\n2. Deleting one memory...")
    if memory_ids:
        deleted_id = memory_ids[0]
        success = agent.delete(memory_id=deleted_id, user_id=user_id)
        if success:
            print(f"   ✓ Deleted memory ID: {deleted_id}")
    
    print("\n3. Verifying remaining memories...")
    all_memories = agent.get_all(user_id=user_id)
    remaining = _get_results(all_memories)
    print(f"   Remaining memories: {len(remaining)}")
    for mem in remaining:
        print(f"      - {_memory_text(mem)}")
    
    _safe_delete_all(agent, user_id=user_id)


def test_agent_metadata_filtering() -> None:
    """Test agent memory metadata filtering: verify that agent memories can be filtered by metadata"""
    _print_step("Agent Metadata Filtering: Filtering by Metadata")
    user_id = "scenario3_metadata_user"
    agent = _create_agent("scenario3_metadata_agent")
    
    _safe_delete_all(agent, user_id=user_id)
    
    print("1. Adding memories with different metadata...")
    agent.add(
        "Customer prefers email",
        user_id=user_id,
        metadata={"category": "communication", "priority": "high"}
    )
    agent.add(
        "Customer likes Python",
        user_id=user_id,
        metadata={"category": "technical", "priority": "medium"}
    )
    agent.add(
        "Customer works at Google",
        user_id=user_id,
        metadata={"category": "professional", "priority": "high"}
    )
    print("   ✓ Added memories with metadata")
    
    print("\n2. Filtering by category...")
    category_results = agent.search(
        query="customer",
        user_id=user_id,
        filters={"metadata.category": "communication"}
    )
    category_memories = _get_results(category_results)
    print(f"   Found {len(category_memories)} memories with category='communication'")
    for mem in category_memories:
        print(f"      - {_memory_text(mem)}")
    
    print("\n3. Filtering by priority...")
    priority_results = agent.search(
        query="customer",
        user_id=user_id,
        filters={"metadata.priority": "high"}
    )
    priority_memories = _get_results(priority_results)
    print(f"   Found {len(priority_memories)} memories with priority='high'")
    for mem in priority_memories:
        print(f"      - {_memory_text(mem)}")
    
    _safe_delete_all(agent, user_id=user_id)


def test_agent_batch_operations() -> None:
    """Test agent batch operations: verify that an agent can batch add and manage memories"""
    _print_step("Agent Batch Operations: Batch Memory Management")
    user_id = "scenario3_batch_user"
    agent = _create_agent("scenario3_batch_agent")
    
    _safe_delete_all(agent, user_id=user_id)
    
    print("1. Batch adding memories...")
    memories = [
        ("Customer prefers email", {"batch": 1}),
        ("Customer likes Python", {"batch": 1}),
        ("Customer uses PostgreSQL", {"batch": 1}),
        ("Customer works at Google", {"batch": 1}),
    ]
    
    memory_ids = []
    for content, meta in memories:
        result = agent.add(content, user_id=user_id, metadata=meta)
        if isinstance(result, dict):
            results = result.get("results", [])
            if results and isinstance(results[0], dict):
                mem_id = results[0].get("id") or results[0].get("memory_id")
                if mem_id:
                    memory_ids.append(mem_id)
    
    print(f"   ✓ Added {len(memory_ids)} memories in batch")
    
    print("\n2. Verifying batch addition...")
    all_memories = agent.get_all(user_id=user_id)
    all_results = _get_results(all_memories)
    print(f"   Total memories: {len(all_results)}")
    
    print("\n3. Batch search...")
    search_results = agent.search(query="customer", user_id=user_id, limit=10)
    search_list = _get_results(search_results)
    print(f"   Found {len(search_list)} memories in search")
    
    print("\n4. Batch delete...")
    if memory_ids:
        deleted_count = 0
        for mem_id in memory_ids[:2]:  # Delete first 2
            success = agent.delete(memory_id=mem_id, user_id=user_id)
            if success:
                deleted_count += 1
        print(f"   ✓ Deleted {deleted_count} memories")
    
    final_memories = agent.get_all(user_id=user_id)
    final_results = _get_results(final_memories)
    print(f"   Remaining memories: {len(final_results)}")
    
    _safe_delete_all(agent, user_id=user_id)


def test_agent_get_all() -> None:
    """Test agent get_all operation: verify that an agent can retrieve all its own memories"""
    _print_step("Agent Get All: Retrieving All Agent Memories")
    user_id = "scenario3_getall_user"
    agent = _create_agent("scenario3_getall_agent")
    
    _safe_delete_all(agent, user_id=user_id)
    
    print("1. Adding multiple memories...")
    for i in range(5):
        agent.add(f"Memory {i+1}: Customer preference {i+1}", user_id=user_id)
    print("   ✓ Added 5 memories")
    
    print("\n2. Getting all memories...")
    all_memories = agent.get_all(user_id=user_id)
    all_results = _get_results(all_memories)
    print(f"   ✓ Retrieved {len(all_results)} memories:")
    for idx, mem in enumerate(all_results, start=1):
        print(f"      {idx}. {_memory_text(mem)}")
    
    print("\n3. Comparing with search results...")
    search_results = agent.search(query="customer", user_id=user_id, limit=10)
    search_list = _get_results(search_results)
    print(f"   Search found {len(search_list)} memories")
    print(f"   ✓ get_all and search consistency: {len(all_results) == len(search_list)}")
    
    _safe_delete_all(agent, user_id=user_id)


def test_complex_multi_agent_scenario() -> None:
    """Test complex multi-agent scenario: simulate real-world multi-agent collaboration scenarios"""
    _print_step("Complex Multi-Agent Scenario: Real-World Collaboration")
    customer_id = "scenario3_complex_customer"
    project_id = "scenario3_complex_project"
    
    support_agent = _create_agent("scenario3_support")
    sales_agent = _create_agent("scenario3_sales")
    tech_agent = _create_agent("scenario3_tech")
    manager_agent = _create_agent("scenario3_manager")
    
    for agent in (support_agent, sales_agent, tech_agent, manager_agent):
        _safe_delete_all(agent, user_id=customer_id)
        _safe_delete_all(agent, run_id=project_id)
    
    print("1. Support agent records customer interaction...")
    support_agent.add(
        "Customer reported login issue on Monday",
        user_id=customer_id,
        metadata={"interaction_type": "support", "date": "2024-01-01"}
    )
    
    print("2. Tech agent investigates and fixes...")
    tech_agent.add(
        "Fixed authentication bug in login module",
        user_id=customer_id,
        run_id=project_id,
        metadata={"action": "bug_fix", "module": "authentication"}
    )
    
    print("3. Sales agent follows up...")
    sales_agent.add(
        "Customer satisfied with resolution, interested in premium features",
        user_id=customer_id,
        metadata={"followup": True, "upsell_opportunity": True}
    )
    
    print("4. Manager agent reviews overall status...")
    manager_results = manager_agent.search(query="customer status", user_id=customer_id)
    manager_memories = _get_results(manager_results)
    print(f"   Manager found {len(manager_memories)} relevant memories:")
    for mem in manager_memories:
        agent_id = mem.get("agent_id", "Unknown")
        print(f"      [{agent_id}] {_memory_text(mem)}")
    
    print("\n5. Project-specific search...")
    project_results = tech_agent.search(query="project work", run_id=project_id)
    project_memories = _get_results(project_results)
    print(f"   Found {len(project_memories)} project memories")
    for mem in project_memories:
        print(f"      - {_memory_text(mem)}")
    
    for agent in (support_agent, sales_agent, tech_agent, manager_agent):
        _safe_delete_all(agent, user_id=customer_id)
        _safe_delete_all(agent, run_id=project_id)


def test_agent_isolation_verification() -> None:
    """Test agent isolation verification: strictly verify that memories of different agents are completely isolated"""
    _print_step("Agent Isolation Verification: Strict Isolation Testing")
    user_id = "scenario3_isolation_test"
    
    agent1 = _create_agent("scenario3_isolate_1")
    agent2 = _create_agent("scenario3_isolate_2")
    agent3 = _create_agent("scenario3_isolate_3")
    
    for agent in (agent1, agent2, agent3):
        _safe_delete_all(agent, user_id=user_id)
    
    print("1. Each agent adds unique memories...")
    agent1.add("Agent1 secret: Customer password reset", user_id=user_id)
    agent2.add("Agent2 secret: Customer payment info", user_id=user_id)
    agent3.add("Agent3 secret: Customer personal data", user_id=user_id)
    print("   ✓ All agents added memories")
    
    print("\n2. Verifying isolation - Agent1 should only see its own...")
    results1 = agent1.search(query="secret", user_id=user_id, agent_id="scenario3_isolate_1")
    memories1 = _get_results(results1)
    print(f"   Agent1 found {len(memories1)} memories")
    for mem in memories1:
        agent_id = mem.get("agent_id", "Unknown")
        print(f"      [{agent_id}] {_memory_text(mem)}")
        if agent_id != "scenario3_isolate_1":
            print("      ⚠ Warning: Isolation breach detected!")
    
    print("\n3. Verifying isolation - Agent2 should only see its own...")
    results2 = agent2.search(query="secret", user_id=user_id, agent_id="scenario3_isolate_2")
    memories2 = _get_results(results2)
    print(f"   Agent2 found {len(memories2)} memories")
    for mem in memories2:
        agent_id = mem.get("agent_id", "Unknown")
        print(f"      [{agent_id}] {_memory_text(mem)}")
        if agent_id != "scenario3_isolate_2":
            print("      ⚠ Warning: Isolation breach detected!")
    
    print("\n4. Cross-agent search (should see all)...")
    cross_results = agent1.search(query="secret", user_id=user_id)
    cross_memories = _get_results(cross_results)
    print(f"   Cross-agent search found {len(cross_memories)} memories")
    for mem in cross_memories:
        agent_id = mem.get("agent_id", "Unknown")
        print(f"      [{agent_id}] {_memory_text(mem)}")
    
    for agent in (agent1, agent2, agent3):
        _safe_delete_all(agent, user_id=user_id)


def main() -> None:
    _print_banner("Powermem Scenario 3: Multi-Agent")

    test_step1_create_agents()
    test_step2_add_agent_specific_memories()
    test_step3_agent_specific_search()
    test_step4_cross_agent_search()
    test_step5_project_collaboration()
    test_step6_memory_scopes()
    test_step7_memory_isolation()
    test_complete_example()
    test_extension_team_collaboration()
    test_extension_customer_service_team()
    test_extension_memory_scopes()
    
    # Additional comprehensive tests
    test_multiple_agents_same_user()
    test_agent_memory_update()
    test_agent_memory_delete()
    test_agent_metadata_filtering()
    test_agent_batch_operations()
    test_agent_get_all()
    test_complex_multi_agent_scenario()
    test_agent_isolation_verification()

    _print_banner("Scenario 3 walkthrough completed successfully!")


if __name__ == "__main__":
    main()

