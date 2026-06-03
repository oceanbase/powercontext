"""Unit tests for per-memory Ebbinghaus decay rates."""

from datetime import timedelta

import pytest

from powermem.intelligence.ebbinghaus_algorithm import EbbinghausAlgorithm
from powermem.intelligence.intelligent_memory_manager import IntelligentMemoryManager
from powermem.utils.utils import get_current_datetime


@pytest.fixture
def algo():
    return EbbinghausAlgorithm({"decay_rate": 0.1})


def test_decay_rate_ordering_by_type(algo):
    working = algo._get_decay_rate_for_type("working")
    short_term = algo._get_decay_rate_for_type("short_term")
    long_term = algo._get_decay_rate_for_type("long_term")

    assert working == pytest.approx(0.05)
    assert short_term == pytest.approx(0.15)
    assert long_term == pytest.approx(0.2)
    assert working < short_term < long_term


def test_calculate_decay_uses_explicit_decay_rate(algo):
    created_at = get_current_datetime() - timedelta(hours=4)

    working_decay = algo.calculate_decay(created_at, decay_rate=0.05)
    long_term_decay = algo.calculate_decay(created_at, decay_rate=0.2)

    assert working_decay < long_term_decay


def test_should_forget_uses_memory_type_decay_rate(algo):
    created_at = get_current_datetime() - timedelta(hours=2)

    working_memory = {
        "created_at": created_at,
        "memory_type": "working",
        "access_count": 1,
    }
    long_term_memory = {
        "created_at": created_at,
        "memory_type": "long_term",
        "access_count": 1,
    }

    assert algo.should_forget(working_memory) is True
    assert algo.should_forget(long_term_memory) is False


def test_resolve_decay_rate_prefers_memory_type_over_stored_decay_rate(algo):
    memory = {
        "metadata": {
            "memory_type": "working",
            "intelligence": {
                "memory_type": "working",
                "decay_rate": 0.2,
            },
        }
    }

    assert algo._resolve_decay_rate(memory) == pytest.approx(0.05)


def test_resolve_decay_rate_reads_nested_intelligence_memory_type(algo):
    memory = {
        "metadata": {
            "intelligence": {
                "memory_type": "long_term",
                "decay_rate": 0.1,
            },
        }
    }

    assert algo._resolve_decay_rate(memory) == pytest.approx(0.2)


def test_resolve_decay_rate_falls_back_to_stored_rate(algo):
    memory = {
        "metadata": {
            "intelligence": {
                "decay_rate": 0.17,
            },
        }
    }

    assert algo._resolve_decay_rate(memory) == pytest.approx(0.17)


def test_promotion_changes_effective_rate(algo):
    memory = {"memory_type": "working"}
    promoted = {"memory_type": "long_term"}

    assert algo._resolve_decay_rate(memory) < algo._resolve_decay_rate(promoted)


def test_custom_decay_rate_multipliers_are_applied():
    custom = EbbinghausAlgorithm(
        {
            "decay_rate": 0.2,
            "decay_rate_multipliers": {
                "working": 0.25,
                "short_term": 1.0,
                "long_term": 3.0,
            },
        }
    )

    assert custom._get_decay_rate_for_type("working") == pytest.approx(0.05)
    assert custom._get_decay_rate_for_type("short_term") == pytest.approx(0.2)
    assert custom._get_decay_rate_for_type("long_term") == pytest.approx(0.6)


def test_search_results_use_type_specific_decay_rate():
    manager = IntelligentMemoryManager({"intelligent_memory": {"decay_rate": 0.1}})
    created_at = get_current_datetime() - timedelta(hours=4)
    results = [
        {
            "id": "working",
            "content": "shared keyword",
            "created_at": created_at,
            "memory_type": "working",
        },
        {
            "id": "long",
            "content": "shared keyword",
            "created_at": created_at,
            "memory_type": "long_term",
        },
    ]

    processed = manager.process_search_results(results, "keyword")
    by_id = {item["id"]: item for item in processed}

    assert by_id["working"]["decay_factor"] < by_id["long"]["decay_factor"]
    assert processed[0]["id"] == "long"
