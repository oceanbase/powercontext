"""Unit tests for per-memory Ebbinghaus decay rates."""

from datetime import timedelta

import pytest

from powermem.intelligence.ebbinghaus_algorithm import EbbinghausAlgorithm
from powermem.intelligence.intelligent_memory_manager import IntelligentMemoryManager
from powermem.utils.utils import get_current_datetime


@pytest.fixture
def algo():
    return EbbinghausAlgorithm({"decay_rate": 1.5})


def test_decay_rate_ordering_by_type(algo):
    working = algo._get_decay_rate_for_type("working")
    short_term = algo._get_decay_rate_for_type("short_term")
    long_term = algo._get_decay_rate_for_type("long_term")

    assert working == pytest.approx(1.5)
    assert short_term == pytest.approx(10.5)
    assert long_term == pytest.approx(90.0)
    assert working < short_term < long_term


def test_calculate_decay_uses_explicit_decay_rate(algo):
    created_at = get_current_datetime() - timedelta(hours=4)

    working_decay = algo.calculate_decay(created_at, decay_rate=0.05)
    long_term_decay = algo.calculate_decay(created_at, decay_rate=0.2)

    assert working_decay < long_term_decay


def test_should_forget_uses_memory_type_decay_rate(algo):
    created_at = get_current_datetime() - timedelta(hours=50)

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

    assert algo._resolve_decay_rate(memory) == pytest.approx(1.5)


def test_resolve_decay_rate_reads_nested_intelligence_memory_type(algo):
    memory = {
        "metadata": {
            "intelligence": {
                "memory_type": "long_term",
                "decay_rate": 0.1,
            },
        }
    }

    assert algo._resolve_decay_rate(memory) == pytest.approx(90.0)


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
            "score": 0.8,
            "created_at": created_at,
            "memory_type": "working",
        },
        {
            "id": "long",
            "content": "shared keyword",
            "score": 0.8,
            "created_at": created_at,
            "memory_type": "long_term",
        },
    ]

    processed = manager.process_search_results(results, "keyword")
    by_id = {item["id"]: item for item in processed}

    assert by_id["working"]["decay_factor"] < by_id["long"]["decay_factor"]
    assert processed[0]["id"] == "long"


def test_search_results_demote_memories_marked_for_forgetting():
    manager = IntelligentMemoryManager(
        {
            "intelligent_memory": {
                "decay_rate": 0.1,
                "forgotten_score_multiplier": 0.1,
            }
        }
    )
    created_at = get_current_datetime()
    results = [
        {
            "id": "active",
            "content": "shared keyword",
            "score": 0.8,
            "created_at": created_at,
        },
        {
            "id": "forgotten",
            "content": "shared keyword extra",
            "score": 0.8,
            "created_at": created_at,
            "should_forget": True,
        },
    ]

    processed = manager.process_search_results(results, "keyword")
    by_id = {item["id"]: item for item in processed}

    assert by_id["forgotten"]["forgotten_score_multiplier"] == pytest.approx(
        0.1
    )
    assert by_id["forgotten"]["final_score"] < by_id["active"]["final_score"]
    assert by_id["forgotten"]["score"] == pytest.approx(
        by_id["forgotten"]["final_score"]
    )
    assert processed[0]["id"] == "active"


def test_search_results_expose_scores_in_ranking_order():
    manager = IntelligentMemoryManager(
        {
            "intelligent_memory": {
                "decay_rate": 0.1,
                "forgotten_score_multiplier": 0.1,
            }
        }
    )
    created_at = get_current_datetime()
    results = [
        {
            "id": "forgotten",
            "content": "shared keyword",
            "created_at": created_at,
            "score": 0.9,
            "should_forget": True,
        },
        {
            "id": "active",
            "content": "shared keyword",
            "created_at": created_at,
            "score": 0.2,
        },
    ]

    processed = manager.process_search_results(results, "keyword")
    scores = [item["score"] for item in processed]

    assert processed[0]["id"] == "active"
    assert scores == sorted(scores, reverse=True)
    assert processed[1]["id"] == "forgotten"
    assert processed[1]["original_score"] == pytest.approx(0.9)


@pytest.mark.parametrize(
    "marker",
    [
        {"should_forget": True},
        {"metadata": {"should_forget": True}},
        {"metadata": {"should_forget": "true"}},
        {"metadata": {"memory_management": {"should_forget": True}}},
    ],
)
def test_search_results_read_forget_marker_from_supported_locations(marker):
    manager = IntelligentMemoryManager(
        {
            "intelligent_memory": {
                "decay_rate": 0.1,
                "forgotten_score_multiplier": 0.2,
            }
        }
    )
    result = {
        "id": "forgotten",
        "content": "keyword",
        "created_at": get_current_datetime(),
        **marker,
    }

    processed = manager.process_search_results([result], "keyword")

    assert processed[0]["forgotten_score_multiplier"] == pytest.approx(0.2)


def test_search_results_do_not_demote_unmarked_memories():
    manager = IntelligentMemoryManager({"intelligent_memory": {"decay_rate": 0.1}})
    result = {
        "id": "active",
        "content": "keyword",
        "score": 0.85,
        "created_at": get_current_datetime(),
        "metadata": {"should_forget": False},
    }

    processed = manager.process_search_results([result], "keyword")

    assert processed[0]["forgotten_score_multiplier"] == pytest.approx(1.0)
    assert processed[0]["final_score"] == pytest.approx(
        0.85 * processed[0]["decay_factor"]
    )


def test_search_results_use_storage_score_for_ranking():
    manager = IntelligentMemoryManager({"intelligent_memory": {"decay_rate": 0.1}})
    result = {
        "id": "storage-result",
        "memory": "some content",
        "score": 0.92,
        "created_at": get_current_datetime(),
    }

    processed = manager.process_search_results([result], "any query")

    assert processed[0]["original_score"] == pytest.approx(0.92)
    assert processed[0]["final_score"] == pytest.approx(
        0.92 * processed[0]["decay_factor"]
    )


def test_forgotten_score_multiplier_does_not_boost_scores():
    manager = IntelligentMemoryManager(
        {"intelligent_memory": {"forgotten_score_multiplier": 2.0}}
    )

    assert manager.forgotten_score_multiplier == pytest.approx(1.0)


def test_old_unaccessed_working_memory_is_forgotten_not_promoted():
    """Regression: a 50h-old working memory with access_count=0 should be
    soft-forgotten, not promoted. Age alone must not override decay."""
    from powermem.intelligence.plugin import EbbinghausIntelligencePlugin

    plugin = EbbinghausIntelligencePlugin({"enabled": True, "decay_rate": 1.5})
    memory = {
        "memory_type": "working",
        "access_count": 0,
        "importance_score": 0.3,
        "created_at": (get_current_datetime() - timedelta(hours=50)).isoformat(),
        "metadata": {},
    }

    updates, delete_flag = plugin.on_get(memory)

    assert delete_flag is True
    assert updates is None
