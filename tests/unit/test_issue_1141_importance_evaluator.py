"""Unit tests for Issue #1141 — Importance evaluator unification (FC-6).

Verifies that _rule_based_evaluation() uses the six-dimension weighted
evaluation framework and get_importance_breakdown() includes weighted_total.
"""

import pytest
from unittest.mock import MagicMock

from powermem.intelligence.importance_evaluator import ImportanceEvaluator


@pytest.fixture
def evaluator():
    """Create an ImportanceEvaluator with default config."""
    config = {}
    llm_config = {}
    ev = ImportanceEvaluator(config, llm_config)
    return ev


class TestIssue1141RuleBasedEvaluation:
    """FC-6: _rule_based_evaluation must use six-dimension weighted framework."""

    def test_returns_weighted_sum_of_dimensions(self, evaluator):
        """AC-6.1: Return value equals six-dimension weighted sum."""
        # Content with keywords that trigger multiple dimensions
        content = "I love this new research data about my todo list"

        result = evaluator._rule_based_evaluation(content, None, None)

        # Verify result is a float in [0, 1]
        assert isinstance(result, float)
        assert 0.0 <= result <= 1.0

        # The result should NOT be the old hardcoded logic (keyword + length bonus)
        # but should be the weighted sum of six _evaluate_* methods
        # We verify by checking that the score is consistent with weighted calculation
        relevance = evaluator._evaluate_relevance(content, None)
        novelty = evaluator._evaluate_novelty(content, None)
        emotional = evaluator._evaluate_emotional_impact(content)
        actionable = evaluator._evaluate_actionable(content)
        factual = evaluator._evaluate_factual(content)
        personal = evaluator._evaluate_personal(content, None)

        expected = (
            relevance * 0.3
            + novelty * 0.2
            + emotional * 0.15
            + actionable * 0.15
            + factual * 0.1
            + personal * 0.1
        )
        expected = max(0.0, min(1.0, expected))

        assert result == pytest.approx(expected), (
            f"_rule_based_evaluation should return weighted sum of dimensions. "
            f"Expected {expected}, got {result}"
        )

    def test_zero_input_returns_zero(self, evaluator):
        """AC-6.2: Empty/neutral content returns 0.0."""
        # Content with no keywords matching any dimension
        content = "the is a an of"
        result = evaluator._rule_based_evaluation(content, None, None)
        assert result == pytest.approx(0.0), (
            "Neutral content with no keyword matches should return 0.0"
        )

    def test_returns_clamped_to_unit_range(self, evaluator):
        """AC-6.3: Return value is clamped to [0, 1]."""
        # Even with maximum keyword hits, score should not exceed 1.0
        content = "important critical urgent remember password " * 10
        result = evaluator._rule_based_evaluation(content, None, None)
        assert 0.0 <= result <= 1.0

    def test_custom_criteria_weights_are_used(self, evaluator):
        """AC-6.4: Custom criteria_weights affect the result."""
        content = "some relevant new content"

        # Default weights
        default_result = evaluator._rule_based_evaluation(content, None, None)

        # Custom weights — weight relevance at 100%
        evaluator.criteria_weights = {
            "relevance": 1.0,
            "novelty": 0.0,
            "emotional_impact": 0.0,
            "actionable": 0.0,
            "factual": 0.0,
            "personal": 0.0,
        }
        custom_result = evaluator._rule_based_evaluation(content, None, None)

        relevance_score = evaluator._evaluate_relevance(content, None)
        assert custom_result == pytest.approx(
            max(0.0, min(1.0, relevance_score))
        ), "Custom weights should change the calculation"


class TestIssue1141ImportanceBreakdown:
    """FC-6: get_importance_breakdown must include weighted_total."""

    def test_breakdown_contains_weighted_total(self, evaluator):
        """AC-6.5: get_importance_breakdown() returns dict with 'weighted_total' key."""
        content = "important data about new research"
        breakdown = evaluator.get_importance_breakdown(content, None, None)

        assert isinstance(breakdown, dict), "Breakdown should be a dict"
        assert "weighted_total" in breakdown, (
            "get_importance_breakdown() must include 'weighted_total' key"
        )

    def test_breakdown_contains_all_dimensions(self, evaluator):
        """AC-6.6: Breakdown contains all six dimension keys."""
        content = "test content"
        breakdown = evaluator.get_importance_breakdown(content, None, None)

        expected_keys = ["relevance", "novelty", "emotional_impact",
                         "actionable", "factual", "personal"]
        for key in expected_keys:
            assert key in breakdown, f"Breakdown missing dimension: {key}"

    def test_weighted_total_equals_weighted_sum(self, evaluator):
        """AC-6.7: weighted_total equals the weighted sum of dimensions."""
        content = "I love this new research data"
        breakdown = evaluator.get_importance_breakdown(content, None, None)

        expected_total = sum(
            breakdown.get(dim, 0.0) * weight
            for dim, weight in evaluator.criteria_weights.items()
        )
        expected_total = max(0.0, min(1.0, expected_total))

        assert breakdown["weighted_total"] == pytest.approx(expected_total), (
            f"weighted_total should equal weighted sum. "
            f"Expected {expected_total}, got {breakdown.get('weighted_total')}"
        )

    def test_breakdown_zero_input(self, evaluator):
        """AC-6.8: Zero input produces zero breakdown."""
        content = "the is a an of"
        breakdown = evaluator.get_importance_breakdown(content, None, None)

        for key in ["relevance", "novelty", "emotional_impact",
                     "actionable", "factual", "personal"]:
            assert breakdown.get(key, 0.0) == pytest.approx(0.0), (
                f"Dimension '{key}' should be 0.0 for neutral content"
            )
        assert breakdown.get("weighted_total", -1) == pytest.approx(0.0)


class TestIssue1141EvaluateDimensions:
    """FC-6: Individual dimension evaluators work correctly."""

    def test_evaluate_relevance_with_keywords(self, evaluator):
        """Relevance score increases with relevant keywords."""
        assert evaluator._evaluate_relevance("this is relevant", None) > 0.0
        assert evaluator._evaluate_relevance("no matching words", None) == 0.0

    def test_evaluate_novelty_with_keywords(self, evaluator):
        """Novelty score increases with novelty keywords."""
        assert evaluator._evaluate_novelty("new discovery", None) > 0.0
        assert evaluator._evaluate_novelty("same old thing", None) == 0.0

    def test_evaluate_emotional_impact_with_keywords(self, evaluator):
        """Emotional impact score increases with emotional words."""
        assert evaluator._evaluate_emotional_impact("I am happy") > 0.0
        assert evaluator._evaluate_emotional_impact("neutral statement") == 0.0

    def test_evaluate_actionable_with_keywords(self, evaluator):
        """Actionable score increases with action words."""
        assert evaluator._evaluate_actionable("create a new file") > 0.0
        assert evaluator._evaluate_actionable("just thinking") == 0.0

    def test_evaluate_factual_with_keywords(self, evaluator):
        """Factual score increases with factual keywords."""
        assert evaluator._evaluate_factual("research study data") > 0.0
        assert evaluator._evaluate_factual("just a feeling") == 0.0

    def test_evaluate_personal_with_keywords(self, evaluator):
        """Personal score increases with personal keywords."""
        assert evaluator._evaluate_personal("my personal preference", None) > 0.0
        assert evaluator._evaluate_personal("general knowledge", None) == 0.0
