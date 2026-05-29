"""Unit tests for IntelligentMemoryManager memory-type classification."""

from unittest.mock import patch

import pytest

from powermem.intelligence.intelligent_memory_manager import IntelligentMemoryManager


@pytest.fixture
def manager():
    return IntelligentMemoryManager(
        {
            "intelligent_memory": {
                "short_term_threshold": 0.6,
                "long_term_threshold": 0.8,
            }
        }
    )


@pytest.mark.parametrize(
    "importance_score,expected_type",
    [
        (0.4, "working"),
        (0.55, "working"),
        (0.6, "short_term"),
        (0.75, "short_term"),
        (0.8, "long_term"),
        (0.95, "long_term"),
    ],
)
def test_classify_memory_type_matches_ebbinghaus_thresholds(
    manager, importance_score, expected_type
):
    assert manager._classify_memory_type(importance_score) == expected_type


def test_process_metadata_uses_configured_thresholds(manager):
    with patch.object(
        manager.importance_evaluator,
        "evaluate_importance",
        return_value=0.55,
    ):
        result = manager.process_metadata("User prefers dark theme")

    assert result["intelligence"]["memory_type"] == "working"
    assert result["intelligence"]["importance_score"] == 0.55


def test_process_metadata_respects_custom_short_term_threshold():
    custom = IntelligentMemoryManager(
        {"intelligent_memory": {"short_term_threshold": 0.5, "long_term_threshold": 0.8}}
    )
    with patch.object(
        custom.importance_evaluator,
        "evaluate_importance",
        return_value=0.55,
    ):
        result = custom.process_metadata("test content")

    assert result["intelligence"]["memory_type"] == "short_term"
