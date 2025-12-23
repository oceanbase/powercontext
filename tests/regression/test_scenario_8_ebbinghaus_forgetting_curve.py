"""Executable walkthrough for `scenario_8_ebbinghaus_forgetting_curve.md`.

Run `python docs/examples/scenario_8_ebbinghaus_forgetting_curve.py` to execute
every code sample from the Scenario 8 documentation in one go.
"""

from __future__ import annotations

import math
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

try:  # Optional deps for visualization
    import matplotlib.pyplot as plt  # type: ignore
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover
    plt = None  # type: ignore
    np = None  # type: ignore

import pytest
from dotenv import load_dotenv
from powermem import create_memory
from powermem.config_loader import auto_config

# -----------------------------------------------------------------------------
# Environment setup
# -----------------------------------------------------------------------------

env_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
env_example_path = os.path.join(
    os.path.dirname(__file__), "..", "..", "configs", "env.example"
)

if not os.path.exists(env_path):
    print(f"\n No .env file found at: {env_path}")
    print("To add your API keys:")
    print(f"   1. Copy: cp {env_example_path} {env_path}")
    print(f"   2. Edit {env_path} and add your API keys")
    print("\n  For now, using mock providers for demonstration...")
else:
    print("Found .env file")
    load_dotenv(env_path, override=True)


# -----------------------------------------------------------------------------
# Helper utilities
# -----------------------------------------------------------------------------

DEFAULT_USER_ID = "student_001"
DEMO_USER_ID = "student_002"


@pytest.fixture(scope="session")
def memory():
    """Session-scoped fixture providing a shared Memory instance for all tests."""
    config = auto_config()
    mem = create_memory(config=config)
    yield mem
    # Cleanup after all tests complete
    try:
        _safe_delete_all(mem, user_id=DEFAULT_USER_ID)
        _safe_delete_all(mem, user_id=DEMO_USER_ID)
        print(f"\n✓ Cleaned up all test data for users: {DEFAULT_USER_ID}, {DEMO_USER_ID}")
    except Exception as e:
        print(f"\n⚠ Could not cleanup test data: {str(e)[:100]}")


def _print_banner(title: str) -> None:
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def _print_step(title: str) -> None:
    print("\n" + "-" * 80)
    print(title)
    print("-" * 80)


def _safe_delete_all(memory, user_id: str) -> None:
    try:
        memory.delete_all(user_id=user_id)
    except Exception:
        pass


def calculate_retention(time_elapsed_hours: float) -> float:
    """Calculate retention score based on the Ebbinghaus forgetting curve."""
    if time_elapsed_hours <= 0:
        return 1.0
    base_retention_1h = 0.44
    decay_constant = -math.log(base_retention_1h)
    retention = math.exp(-decay_constant * time_elapsed_hours)
    return max(retention, 0.2)


# -----------------------------------------------------------------------------
# Step 1: Understanding the forgetting curve
# -----------------------------------------------------------------------------

def test_step1_show_retention_table() -> None:
    _print_step("Step 1: Understanding the Forgetting Curve Formula")
    time_points = [0, 0.33, 1, 9, 24, 48, 144, 744]
    labels = ["0h", "20min", "1h", "9h", "1d", "2d", "6d", "31d"]

    print("Ebbinghaus Forgetting Curve - Retention Over Time:")
    print("=" * 60)
    for hours, label in zip(time_points, labels):
        retention = calculate_retention(hours)
        print(f"{label:>6}: {retention * 100:>5.1f}% retention")


# -----------------------------------------------------------------------------
# Step 2: Add memories with timestamps
# -----------------------------------------------------------------------------

def test_step2_add_memories_with_timestamps(memory, user_id: str = DEFAULT_USER_ID) -> List[int]:
    _print_step("Step 2: Add Memories with Timestamps")
    _safe_delete_all(memory, user_id=user_id)

    memories_data = [
        ("Python is a high-level programming language", datetime.now() - timedelta(days=31)),
        ("Lists in Python are mutable sequences", datetime.now() - timedelta(days=6)),
        ("Dictionaries use key-value pairs", datetime.now() - timedelta(days=2)),
        ("Functions are defined with the 'def' keyword", datetime.now() - timedelta(hours=9)),
        ("Classes are blueprints for creating objects", datetime.now() - timedelta(hours=1)),
        ("Decorators modify function behavior", datetime.now() - timedelta(minutes=20)),
    ]

    memory_ids: List[int] = []
    print("Adding memories with timestamps...")
    for content, created_at in memories_data:
        result = memory.add(
            messages=content,
            user_id=user_id,
            metadata={
                "created_at": created_at.isoformat(),
                "category": "programming",
                "subject": "Python",
            },
        )
        results_list = result.get("results", [])
        print(content, created_at.isoformat())
        if results_list:
            memory_ids.append(results_list[0].get("id"))
        print(f"  ✓ Added: {content[:60]}...", created_at.isoformat())

    print(f"\n✓ Added {len(memory_ids)} memories")
    # Note: Cleanup will be done at the end of test_main() to preserve data for subsequent steps
    return memory_ids


# -----------------------------------------------------------------------------
# Step 3: Calculate retention scores
# -----------------------------------------------------------------------------

def test_step3_calculate_retention_scores(memory, user_id: str = DEFAULT_USER_ID) -> List[Dict[str, Any]]:
    _print_step("Step 3: Calculate Retention Scores for Memories")
    all_memories = memory.get_all(user_id=user_id)
    memories = all_memories.get("results", [])

    print(f"{'Memory':<50} {'Age':<12} {'Retention':<12} {'Score':<10}")
    print("-" * 80)

    now = datetime.now()
    scored: List[Dict[str, Any]] = []
    for mem in memories:
        metadata = mem.get("metadata", {})
        created_at_str = metadata.get("created_at")
        if not created_at_str:
            continue

        try:
            created_at = datetime.fromisoformat(created_at_str)
        except Exception:
            continue

        hours_elapsed = max((now - created_at).total_seconds() / 3600, 0.0)
        retention = calculate_retention(hours_elapsed)
        age_str = f"{hours_elapsed:.1f}h" if hours_elapsed < 24 else f"{hours_elapsed/24:.1f}d"
        content = mem.get("memory", "")[:48]
        scored.append(
            {
                "memory": mem,
                "retention": retention,
                "hours_elapsed": hours_elapsed,
                "age_str": age_str,
            }
        )
        print(f"{content:<50} {age_str:<12} {retention * 100:>5.1f}%      {retention:.3f}")

    print(f"\nTotal memories analyzed: {len(scored)}")
    # Note: Cleanup will be done at the end of test_main() to preserve data for subsequent steps
    return scored


# -----------------------------------------------------------------------------
# Step 4: Retention-weighted search
# -----------------------------------------------------------------------------

def search_with_retention_weighting(
    memory,
    query: str,
    user_id: str,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    results = memory.search(query=query, user_id=user_id, limit=limit * 2)
    now = datetime.now()
    weighted_results: List[Dict[str, Any]] = []

    for mem in results.get("results", []):
        similarity = mem.get("score") or 0.5
        metadata = mem.get("metadata", {})
        created_at_str = metadata.get("created_at")
        if created_at_str:
            created_at = datetime.fromisoformat(created_at_str)
            hours_elapsed = max((now - created_at).total_seconds() / 3600, 0.0)
            retention = calculate_retention(hours_elapsed)
        else:
            hours_elapsed = 0.0
            retention = 0.9

        weighted_results.append(
            {
                "memory": mem.get("memory", ""),
                "similarity": similarity,
                "retention": retention,
                "combined_score": similarity * retention,
                "hours_elapsed": hours_elapsed,
                "metadata": metadata,
            }
        )

    weighted_results.sort(key=lambda item: item["combined_score"], reverse=True)
    return weighted_results[:limit]


def test_step4_search_with_retention(memory, user_id: str = DEFAULT_USER_ID) -> None:
    _print_step("Step 4: Apply Time-Based Weighting to Search Results")
    query = "Python programming concepts"

    print(f"Searching for: '{query}'")
    print(f"{'Memory':<50} {'Similarity':<12} {'Retention':<12} {'Combined':<10}")
    print("-" * 80)

    results = search_with_retention_weighting(memory, query, user_id, limit=5)
    if not results:
        print("No memories found for the given query.")
        return

    for idx, result in enumerate(results, 1):
        content = result["memory"][:48]
        print(
            f"{idx}. {content:<48} {result['similarity']:.3f}      "
            f"{result['retention'] * 100:>5.1f}%      {result['combined_score']:.3f}"
        )
    # Note: Cleanup will be done at the end of test_main() to preserve data for subsequent steps


# -----------------------------------------------------------------------------
# Step 5: Visualize forgetting curve
# -----------------------------------------------------------------------------

def test_step5_visualize_curve() -> None:
    _print_step("Step 5: Visualize the Forgetting Curve")
    if plt is None or np is None:
        print("Visualization skipped (matplotlib or numpy not available).")
        return

    hours = np.linspace(0, 744, 1000)
    retentions = [calculate_retention(h) for h in hours]

    plt.figure(figsize=(12, 6))
    plt.plot(hours / 24, [r * 100 for r in retentions], color="#2E86AB", linewidth=2.5)

    key_points = [
        (0, "Immediate"),
        (0.33, "20 min"),
        (1, "1 hour"),
        (9, "9 hours"),
        (24, "1 day"),
        (48, "2 days"),
        (144, "6 days"),
        (744, "31 days"),
    ]

    for hours_val, label in key_points:
        retention = calculate_retention(hours_val)
        plt.plot(hours_val / 24, retention * 100, "ro", markersize=8)
        plt.annotate(
            f"{label}\n{retention * 100:.1f}%",
            xy=(hours_val / 24, retention * 100),
            xytext=(10, 10),
            textcoords="offset points",
            bbox=dict(boxstyle="round,pad=0.4", facecolor="#FFE66D", alpha=0.8),
            arrowprops=dict(arrowstyle="->", color="#333"),
        )

    plt.xlabel("Time (days)")
    plt.ylabel("Retention (%)")
    plt.title("Ebbinghaus Forgetting Curve")
    plt.grid(True, linestyle="--", alpha=0.3)
    plt.xlim(0, 31)
    plt.ylim(0, 105)
    plt.tight_layout()

    save_dir = os.path.join(os.path.dirname(__file__), "data")
    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, "ebbinghaus_curve.png")
    plt.savefig(save_path, dpi=150, bbox_inches="tight", facecolor="white")
    print(f"✓ Saved visualization to '{save_path}'")
    plt.close()


# -----------------------------------------------------------------------------
# Step 6: Spaced repetition recommendations
# -----------------------------------------------------------------------------

def get_review_schedule() -> List[Tuple[float, str]]:
    return [
        (0.33, "20 minutes"),
        (1, "1 hour"),
        (9, "9 hours"),
        (24, "1 day"),
        (48, "2 days"),
        (144, "6 days"),
        (336, "14 days"),
        (744, "31 days"),
    ]


def get_next_review_time(mem: Dict[str, Any], retention_threshold: float = 0.5) -> Tuple[float, bool]:
    metadata = mem.get("metadata", {})
    created_at_str = metadata.get("created_at")
    reference_str = metadata.get("last_reviewed", created_at_str)
    if not reference_str:
        return 0, True

    reference_time = datetime.fromisoformat(reference_str)
    hours_elapsed = max((datetime.now() - reference_time).total_seconds() / 3600, 0.0)
    current_retention = calculate_retention(hours_elapsed)

    if current_retention < retention_threshold:
        return 0, True

    for hours, _ in get_review_schedule():
        if hours > hours_elapsed:
            return hours - hours_elapsed, False

    return 0, True


def test_step6_spaced_repetition(memory, user_id: str = DEFAULT_USER_ID) -> None:
    _print_step("Step 6: Implement Spaced Repetition Recommendations")
    all_memories = memory.get_all(user_id=user_id)
    memories = all_memories.get("results", [])

    if not memories:
        print("No memories available for spaced repetition analysis.")
        return

    print(f"{'Memory':<50} {'Retention':<12} {'Review Status':<20}")
    print("-" * 80)

    now = datetime.now()
    for mem in memories:
        metadata = mem.get("metadata", {})
        created_at_str = metadata.get("created_at")
        if not created_at_str:
            continue

        created_at = datetime.fromisoformat(created_at_str)
        hours_elapsed = max((now - created_at).total_seconds() / 3600, 0.0)
        retention = calculate_retention(hours_elapsed)
        hours_until, needs_review = get_next_review_time(mem, retention_threshold=0.5)

        if needs_review:
            status = "⚠ Review NOW"
        elif hours_until < 24:
            status = f"Review in {hours_until:.1f}h"
        else:
            status = f"Review in {hours_until/24:.1f}d"

        print(f"{mem.get('memory', '')[:48]:<50} {retention * 100:>5.1f}%      {status:<20}")
    # Note: Cleanup will be done at the end of test_main() to preserve data for subsequent steps


# -----------------------------------------------------------------------------
# Step 7: Complete demo
# -----------------------------------------------------------------------------

def test_step7_complete_demo(memory) -> None:
    _print_step("Step 7: Complete Example - Learning System with Forgetting Curve")
    user_id = DEMO_USER_ID
    _safe_delete_all(memory, user_id=user_id)

    learning_materials = [
        ("Python basics", datetime.now() - timedelta(days=31)),
        ("Data structures", datetime.now() - timedelta(days=6)),
        ("Functions and classes", datetime.now() - timedelta(days=2)),
        ("Advanced topics", datetime.now() - timedelta(hours=9)),
        ("Best practices", datetime.now() - timedelta(hours=1)),
    ]

    print("1. Adding learning materials...")
    for topic, created_at in learning_materials:
        memory.add(
            messages=f"Learned about {topic}",
            user_id=user_id,
            metadata={
                "created_at": created_at.isoformat(),
                "category": "learning",
                "topic": topic,
            },
        )
        print(f"   ✓ Added: {topic}")

    print("\n2. Searching with retention-based weighting...")
    query = "Python programming"
    weighted_results = search_with_retention_weighting(memory, query, user_id, limit=5)

    if weighted_results:
        print(f"{'Memory':<40} {'Similarity':<12} {'Retention':<12} {'Score':<10}")
        print("-" * 80)
        for idx, result in enumerate(weighted_results, 1):
            content = result["memory"][:38]
            print(
                f"{idx}. {content:<38} {result['similarity']:.3f}      "
                f"{result['retention'] * 100:>5.1f}%      {result['combined_score']:.3f}"
            )
    else:
        print("No results found for the demo query.")

    print("\n3. Retention analysis...")
    all_memories = memory.get_all(user_id=user_id)
    memories = all_memories.get("results", [])
    now = datetime.now()
    total_retention = 0.0
    for mem in memories:
        metadata = mem.get("metadata", {})
        created_at_str = metadata.get("created_at")
        if not created_at_str:
            continue
        created_at = datetime.fromisoformat(created_at_str)
        hours_elapsed = max((now - created_at).total_seconds() / 3600, 0.0)
        total_retention += calculate_retention(hours_elapsed)

    avg_retention = total_retention / len(memories) if memories else 0.0
    print(f"   Average retention: {avg_retention * 100:.1f}%")
    print(f"   Total memories: {len(memories)}")
    
    # Cleanup
    _safe_delete_all(memory, user_id=user_id)


# -----------------------------------------------------------------------------
# Main execution
# -----------------------------------------------------------------------------

def main() -> None:
    _print_banner("Powermem Scenario 8: Ebbinghaus Forgetting Curve")
    
    # Load config and fix dashscope_base_url issue
    config = auto_config()
    
    memory = create_memory(config=config)

    test_step1_show_retention_table()
    test_step2_add_memories_with_timestamps(memory, DEFAULT_USER_ID)
    test_step3_calculate_retention_scores(memory, DEFAULT_USER_ID)
    test_step4_search_with_retention(memory, DEFAULT_USER_ID)
    test_step5_visualize_curve()
    test_step6_spaced_repetition(memory, DEFAULT_USER_ID)
    test_step7_complete_demo(memory)

    # Final cleanup for all users
    try:
        _safe_delete_all(memory, user_id=DEFAULT_USER_ID)
        _safe_delete_all(memory, user_id=DEMO_USER_ID)
        print(f"\n✓ Cleaned up all test data for users: {DEFAULT_USER_ID}, {DEMO_USER_ID}")
    except Exception as e:
        print(f"\n⚠ Could not cleanup test data: {str(e)[:100]}")

    _print_banner("Scenario 8 walkthrough completed successfully!")


if __name__ == "__main__":
    main()


