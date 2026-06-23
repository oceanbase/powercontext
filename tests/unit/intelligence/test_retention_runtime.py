"""Tests for retention fields runtime integration.

Covers:
- should_forget() using initial_retention (effective_retention)
- reinforce() boosting current_retention
- on_get() triggering reinforcement when review is due
- on_get() NOT triggering reinforcement before review time
- Reprocessing preserving current_retention
- calculate_current_retention() combining initial_retention and decay
"""

import math
from datetime import timedelta
from unittest.mock import patch

import pytest

from powermem.intelligence.ebbinghaus_algorithm import EbbinghausAlgorithm
from powermem.intelligence.plugin import EbbinghausIntelligencePlugin
from powermem.intelligence.intelligent_memory_manager import IntelligentMemoryManager
from powermem.utils.utils import get_current_datetime


@pytest.fixture
def algo():
    return EbbinghausAlgorithm({"decay_rate": 1.5, "initial_retention": 1.0})


@pytest.fixture
def algo_low_retention():
    return EbbinghausAlgorithm({"decay_rate": 1.5, "initial_retention": 0.5})


# ---- Test 1: should_forget considers initial_retention ----

def test_should_forget_considers_initial_retention(algo):
    """High-importance memory should survive longer than low-importance at same age."""
    created_at = get_current_datetime() - timedelta(hours=30)

    low_importance = {
        "created_at": created_at,
        "memory_type": "working",
        "access_count": 0,
        "metadata": {
            "intelligence": {
                "initial_retention": 0.3,
            }
        },
    }
    high_importance = {
        "created_at": created_at,
        "memory_type": "working",
        "access_count": 0,
        "metadata": {
            "intelligence": {
                "initial_retention": 0.95,
            }
        },
    }

    # With effective_retention = initial_retention * decay_factor,
    # the low-importance memory should be forgotten sooner.
    assert algo.should_forget(low_importance) is True
    assert algo.should_forget(high_importance) is False


# ---- Test 2: reinforce boosts current_retention ----

def test_reinforce_boosts_current_retention(algo):
    """reinforce() should increase current_retention with diminishing returns."""
    now = get_current_datetime()
    schedule = [
        (now - timedelta(hours=1)).isoformat(),
        (now + timedelta(hours=5)).isoformat(),
        (now + timedelta(hours=23)).isoformat(),
    ]
    memory = {
        "metadata": {
            "intelligence": {
                "current_retention": 0.6,
                "initial_retention": 0.6,
                "reinforcement_factor": 0.3,
                "review_count": 0,
                "review_schedule": schedule,
            }
        }
    }

    result = algo.reinforce(memory)

    expected = min(1.0, 0.6 + 0.3 * (1.0 - 0.6))
    assert result["current_retention"] == pytest.approx(expected)
    assert result["review_count"] == 1
    assert result["last_reviewed"] is not None
    assert result["next_review"] == schedule[1]


def test_reinforce_never_exceeds_one(algo):
    """current_retention should never exceed 1.0 after reinforcement."""
    memory = {
        "metadata": {
            "intelligence": {
                "current_retention": 0.95,
                "reinforcement_factor": 0.5,
                "review_count": 0,
                "review_schedule": [],
            }
        }
    }

    result = algo.reinforce(memory)
    assert result["current_retention"] <= 1.0


# ---- Test 3: on_get triggers reinforcement when review is due ----

def test_on_get_triggers_reinforcement_when_review_due():
    """When now >= next_review, on_get should boost current_retention."""
    config = {
        "enabled": True,
        "importance": {},
        "llm": {},
        "decay_rate": 1.5,
        "initial_retention": 1.0,
        "reinforcement_factor": 0.3,
    }
    plugin = EbbinghausIntelligencePlugin(config)

    now = get_current_datetime()
    past_review = (now - timedelta(hours=1)).isoformat()
    future_review = (now + timedelta(hours=23)).isoformat()

    memory = {
        "id": "test-mem",
        "content": "test content",
        "memory_type": "working",
        "access_count": 0,
        "importance_score": 0.5,
        "created_at": (now - timedelta(hours=2)).isoformat(),
        "metadata": {
            "memory_type": "working",
            "intelligence": {
                "current_retention": 0.7,
                "initial_retention": 0.7,
                "reinforcement_factor": 0.3,
                "review_count": 0,
                "next_review": past_review,
                "review_schedule": [past_review, future_review],
            }
        },
    }

    updates, delete = plugin.on_get(memory)

    assert delete is False
    assert updates is not None
    intel = updates["metadata"]["intelligence"]
    assert intel["current_retention"] > 0.7
    assert intel["review_count"] == 1
    assert intel["next_review"] == future_review


# ---- Test 4: on_get does NOT reinforce before review time ----

def test_on_get_does_not_reinforce_before_review_due():
    """When now < next_review, current_retention should not change via reinforce."""
    config = {
        "enabled": True,
        "importance": {},
        "llm": {},
        "decay_rate": 1.5,
        "initial_retention": 1.0,
        "reinforcement_factor": 0.3,
    }
    plugin = EbbinghausIntelligencePlugin(config)

    now = get_current_datetime()
    future_review = (now + timedelta(hours=5)).isoformat()

    memory = {
        "id": "test-mem",
        "content": "test content",
        "memory_type": "working",
        "access_count": 0,
        "importance_score": 0.5,
        "created_at": now.isoformat(),
        "metadata": {
            "memory_type": "working",
            "intelligence": {
                "current_retention": 0.7,
                "initial_retention": 0.7,
                "reinforcement_factor": 0.3,
                "review_count": 0,
                "next_review": future_review,
                "review_schedule": [future_review],
            }
        },
    }

    updates, delete = plugin.on_get(memory)

    assert delete is False
    assert updates is not None
    intel = updates["metadata"].get("intelligence", {})
    if "current_retention" in intel:
        assert intel["current_retention"] == pytest.approx(0.7)


# ---- Test 5: reprocessing preserves current_retention ----

def test_reprocess_preserves_current_retention():
    """When access_count%5 triggers reprocessing, current_retention from
    reinforcement should not be reset to initial_retention."""
    config = {
        "enabled": True,
        "importance": {},
        "llm": {},
        "decay_rate": 1.5,
        "initial_retention": 1.0,
        "reinforcement_factor": 0.3,
    }
    plugin = EbbinghausIntelligencePlugin(config)

    now = get_current_datetime()
    past_review = (now - timedelta(hours=1)).isoformat()
    future_review = (now + timedelta(hours=23)).isoformat()

    memory = {
        "id": "test-mem",
        "content": "test content",
        "memory_type": "working",
        "access_count": 4,
        "importance_score": 0.5,
        "created_at": (now - timedelta(hours=2)).isoformat(),
        "metadata": {
            "memory_type": "working",
            "importance_score": 0.5,
            "intelligence": {
                "current_retention": 0.85,
                "initial_retention": 0.5,
                "reinforcement_factor": 0.3,
                "review_count": 2,
                "last_reviewed": (now - timedelta(hours=1)).isoformat(),
                "next_review": past_review,
                "review_schedule": [past_review, future_review],
            }
        },
    }

    updates, delete = plugin.on_get(memory)

    assert delete is False
    assert updates is not None
    intel = updates["metadata"]["intelligence"]
    # current_retention should not have been reset to initial_retention (0.5);
    # it should be >= the pre-existing 0.85 (reinforcement may boost it further).
    assert intel["current_retention"] >= 0.85
    assert intel["review_count"] >= 2


# ---- Test 6: calculate_current_retention combines initial and decay ----

def test_calculate_current_retention_combines_initial_and_decay(algo):
    """calculate_current_retention should return initial_retention * decay_factor."""
    created_at = get_current_datetime() - timedelta(hours=24)

    memory = {
        "created_at": created_at,
        "memory_type": "working",
        "access_count": 0,
        "metadata": {
            "intelligence": {
                "initial_retention": 0.8,
            }
        },
    }

    result = algo.calculate_current_retention(memory)
    raw_decay = algo.calculate_decay(
        created_at, decay_rate=algo._resolve_decay_rate(memory)
    )

    assert result == pytest.approx(0.8 * raw_decay)


def test_calculate_current_retention_defaults_without_stored_initial(algo):
    """When no initial_retention is stored, use the config default."""
    created_at = get_current_datetime() - timedelta(hours=12)

    memory = {
        "created_at": created_at,
        "memory_type": "working",
        "access_count": 0,
    }

    result = algo.calculate_current_retention(memory)
    raw_decay = algo.calculate_decay(
        created_at, decay_rate=algo._resolve_decay_rate(memory)
    )

    assert result == pytest.approx(algo.initial_retention * raw_decay)


def test_search_ranking_uses_effective_retention():
    """process_search_results should rank by effective_retention, not raw decay."""
    manager = IntelligentMemoryManager(
        {"intelligent_memory": {"decay_rate": 1.5, "initial_retention": 1.0}}
    )
    created_at = get_current_datetime() - timedelta(hours=30)

    results = [
        {
            "id": "low-init",
            "content": "keyword",
            "score": 0.8,
            "created_at": created_at,
            "memory_type": "working",
            "access_count": 0,
            "metadata": {
                "intelligence": {"initial_retention": 0.3}
            },
        },
        {
            "id": "high-init",
            "content": "keyword",
            "score": 0.8,
            "created_at": created_at,
            "memory_type": "working",
            "access_count": 0,
            "metadata": {
                "intelligence": {"initial_retention": 0.95}
            },
        },
    ]

    processed = manager.process_search_results(results, "keyword")
    by_id = {item["id"]: item for item in processed}

    assert by_id["high-init"]["final_score"] > by_id["low-init"]["final_score"]
    assert "effective_retention" in by_id["high-init"]
