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
            },
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
            },
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
            },
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
    """Without stored current_retention, should return initial_retention * decay_factor."""
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


def test_initialized_current_retention_decays_and_can_forget(algo):
    """Real process_memory_metadata output should not create a permanent floor.

    process_memory_metadata initializes current_retention to initial_retention.
    That stored snapshot must still decay over time, otherwise normal working
    memories can never fall below the forget threshold.
    """
    created_at = get_current_datetime() - timedelta(hours=50)
    metadata = algo.process_memory_metadata(
        "ordinary working memory",
        importance_score=0.5,
        memory_type="working",
    )
    intelligence = metadata["intelligence"]
    intelligence["last_reviewed"] = created_at.isoformat()
    metadata["created_at"] = created_at.isoformat()

    memory = {
        "created_at": created_at,
        "memory_type": "working",
        "access_count": 0,
        "metadata": {
            "memory_type": "working",
            "intelligence": intelligence,
        },
    }

    assert intelligence["current_retention"] == pytest.approx(
        intelligence["initial_retention"]
    )
    assert algo.calculate_current_retention(memory) < algo.working_threshold
    assert algo.should_forget(memory) is True


def test_search_ranking_decays_initialized_current_retention():
    """Search ranking should not treat initialized current_retention as fixed."""
    manager = IntelligentMemoryManager(
        {"intelligent_memory": {"decay_rate": 1.5, "initial_retention": 1.0}}
    )
    algo = manager.ebbinghaus_algorithm
    old_time = get_current_datetime() - timedelta(hours=50)
    fresh_time = get_current_datetime()

    old_meta = algo.process_memory_metadata("keyword", 0.5, "working")
    old_meta["intelligence"]["last_reviewed"] = old_time.isoformat()
    fresh_meta = algo.process_memory_metadata("keyword", 0.5, "working")
    fresh_meta["intelligence"]["last_reviewed"] = fresh_time.isoformat()

    results = [
        {
            "id": "old",
            "content": "keyword",
            "score": 0.8,
            "created_at": old_time,
            "memory_type": "working",
            "access_count": 0,
            "metadata": {
                "memory_type": "working",
                "intelligence": old_meta["intelligence"],
            },
        },
        {
            "id": "fresh",
            "content": "keyword",
            "score": 0.8,
            "created_at": fresh_time,
            "memory_type": "working",
            "access_count": 0,
            "metadata": {
                "memory_type": "working",
                "intelligence": fresh_meta["intelligence"],
            },
        },
    ]

    processed = manager.process_search_results(results, "keyword")
    by_id = {item["id"]: item for item in processed}

    assert by_id["old"]["effective_retention"] < by_id["fresh"]["effective_retention"]
    assert processed[0]["id"] == "fresh"


def test_reinforce_uses_decayed_current_retention_before_boost(algo):
    """reinforce() should boost the real-time retention, not a stale snapshot."""
    last_reviewed = get_current_datetime() - timedelta(hours=50)
    memory = {
        "created_at": last_reviewed,
        "memory_type": "working",
        "access_count": 0,
        "metadata": {
            "intelligence": {
                "initial_retention": 0.3,
                "current_retention": 0.8,
                "last_reviewed": last_reviewed.isoformat(),
                "reinforcement_factor": 0.3,
                "review_count": 0,
                "review_schedule": [],
            }
        },
    }
    decayed = 0.8 * algo.calculate_decay(
        last_reviewed,
        decay_rate=algo._resolve_decay_rate(memory),
    )

    result = algo.reinforce(memory)

    assert result["current_retention"] == pytest.approx(decayed + 0.3 * (1.0 - decayed))
    assert result["current_retention"] < 0.8


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
            "metadata": {"intelligence": {"initial_retention": 0.3}},
        },
        {
            "id": "high-init",
            "content": "keyword",
            "score": 0.8,
            "created_at": created_at,
            "memory_type": "working",
            "access_count": 0,
            "metadata": {"intelligence": {"initial_retention": 0.95}},
        },
    ]

    processed = manager.process_search_results(results, "keyword")
    by_id = {item["id"]: item for item in processed}

    assert by_id["high-init"]["final_score"] > by_id["low-init"]["final_score"]
    assert "effective_retention" in by_id["high-init"]


def test_fresh_zero_importance_memory_keeps_search_relevance():
    """A fresh relevant hit should not be zeroed out by importance_score=0."""
    manager = IntelligentMemoryManager(
        {"intelligent_memory": {"decay_rate": 1.5, "initial_retention": 1.0}}
    )
    algo = manager.ebbinghaus_algorithm
    now = get_current_datetime()
    relevant_meta = algo.process_memory_metadata(
        "Zhang San is a software engineer",
        importance_score=0.0,
        memory_type="working",
    )

    results = [
        {
            "id": "relevant",
            "content": "Zhang San is a software engineer",
            "score": 0.62,
            "created_at": now,
            "memory_type": "working",
            "access_count": 0,
            "metadata": {
                "memory_type": "working",
                "intelligence": relevant_meta["intelligence"],
            },
        },
        {
            "id": "irrelevant",
            "content": "Wang Wu likes running",
            "score": 0.01,
            "created_at": now,
            "memory_type": "working",
            "access_count": 0,
            "metadata": {
                "memory_type": "working",
                "intelligence": {
                    "initial_retention": 1.0,
                    "current_retention": 1.0,
                    "last_reviewed": now.isoformat(),
                },
            },
        },
    ]

    processed = manager.process_search_results(results, "Zhang San occupation")

    assert relevant_meta["intelligence"]["initial_retention"] == pytest.approx(
        algo.working_threshold
    )
    assert processed[0]["id"] == "relevant"
    assert processed[0]["final_score"] > 0


# ---- Tests for review-reinforcement protecting against forgetting ----


def test_should_forget_respects_stored_current_retention(algo):
    """Recent reinforced retention can protect a memory from forgetting."""
    created_at = get_current_datetime() - timedelta(hours=50)
    last_reviewed = get_current_datetime()

    memory = {
        "created_at": created_at,
        "memory_type": "working",
        "access_count": 0,
        "metadata": {
            "intelligence": {
                "initial_retention": 0.3,
                "current_retention": 0.8,
                "last_reviewed": last_reviewed.isoformat(),
            }
        },
    }

    assert algo.should_forget(memory) is False


def test_on_get_reinforced_memory_not_forgotten_same_call():
    """A memory that is reinforced during on_get should not be deleted in the
    same call, even if its pre-reinforcement effective retention was below the
    forget threshold."""
    config = {
        "enabled": True,
        "importance": {},
        "llm": {},
        "decay_rate": 1.5,
        "initial_retention": 1.0,
        "reinforcement_factor": 0.3,
        "working_threshold": 0.3,
    }
    plugin = EbbinghausIntelligencePlugin(config)

    now = get_current_datetime()
    past_review = (now - timedelta(hours=1)).isoformat()
    future_review = (now + timedelta(hours=23)).isoformat()
    created_at = (now - timedelta(hours=50)).isoformat()

    memory = {
        "id": "reinforce-protect",
        "content": "important content",
        "memory_type": "working",
        "access_count": 0,
        "importance_score": 0.3,
        "created_at": created_at,
        "metadata": {
            "memory_type": "working",
            "intelligence": {
                "current_retention": 0.25,
                "initial_retention": 0.3,
                "reinforcement_factor": 0.3,
                "review_count": 0,
                "next_review": past_review,
                "review_schedule": [past_review, future_review],
            },
        },
    }

    updates, delete = plugin.on_get(memory)

    assert delete is False
    assert updates is not None
    intel = updates["metadata"]["intelligence"]
    assert intel["current_retention"] > 0.25


def test_calculate_current_retention_reflects_reinforcement(algo):
    """calculate_current_retention should decay a reinforced retention snapshot."""
    created_at = get_current_datetime() - timedelta(hours=50)
    last_reviewed = get_current_datetime()

    memory = {
        "created_at": created_at,
        "memory_type": "working",
        "access_count": 0,
        "metadata": {
            "intelligence": {
                "initial_retention": 0.3,
                "current_retention": 0.85,
                "last_reviewed": last_reviewed.isoformat(),
            }
        },
    }

    base_decay = algo.calculate_decay(
        created_at, decay_rate=algo._resolve_decay_rate(memory)
    )
    base_retention = 0.3 * base_decay

    result = algo.calculate_current_retention(memory)

    assert result > base_retention
    assert result <= 0.85


def test_search_ranking_reflects_reinforced_current_retention():
    """process_search_results should rank a reinforced memory higher than
    an unreinforced one with the same initial conditions."""
    manager = IntelligentMemoryManager(
        {"intelligent_memory": {"decay_rate": 1.5, "initial_retention": 1.0}}
    )
    created_at = get_current_datetime() - timedelta(hours=50)
    last_reviewed = get_current_datetime()

    results = [
        {
            "id": "unreinforced",
            "content": "keyword",
            "score": 0.8,
            "created_at": created_at,
            "memory_type": "working",
            "access_count": 0,
            "metadata": {"intelligence": {"initial_retention": 0.3}},
        },
        {
            "id": "reinforced",
            "content": "keyword",
            "score": 0.8,
            "created_at": created_at,
            "memory_type": "working",
            "access_count": 0,
            "metadata": {
                "intelligence": {
                    "initial_retention": 0.3,
                    "current_retention": 0.85,
                    "last_reviewed": last_reviewed.isoformat(),
                }
            },
        },
    ]

    processed = manager.process_search_results(results, "keyword")
    by_id = {item["id"]: item for item in processed}

    assert (
        by_id["reinforced"]["effective_retention"]
        > by_id["unreinforced"]["effective_retention"]
    )
    assert processed[0]["id"] == "reinforced"
