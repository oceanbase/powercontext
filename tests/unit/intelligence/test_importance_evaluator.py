"""Unit tests for ImportanceEvaluator._parse_importance_response and fallback logic."""

import json
from unittest.mock import MagicMock, patch

import pytest

from powermem.intelligence.importance_evaluator import ImportanceEvaluator


@pytest.fixture
def evaluator():
    config = {}
    llm_config = {}
    ev = ImportanceEvaluator(config, llm_config)
    return ev


class TestParseImportanceResponse:
    """Tests for _parse_importance_response three-level fallback."""

    def test_valid_json_with_importance_score(self, evaluator):
        response = json.dumps({
            "importance_score": 0.75,
            "reasoning": "High relevance",
            "criteria_scores": {
                "relevance": 0.8, "novelty": 0.6,
                "emotional_impact": 0.5, "actionable": 0.7,
                "factual": 0.6, "personal": 0.4
            }
        })
        assert evaluator._parse_importance_response(response) == pytest.approx(0.75)

    def test_valid_json_with_overall_score(self, evaluator):
        """detailed_importance_breakdown template uses 'overall_score'."""
        response = json.dumps({
            "overall_score": 0.62,
            "reasoning": "Moderate",
            "criteria_scores": {}
        })
        assert evaluator._parse_importance_response(response) == pytest.approx(0.62)

    def test_json_only_criteria_scores_synthesizes_weighted(self, evaluator):
        """When no total score field, synthesize from criteria_scores."""
        response = json.dumps({
            "reasoning": "No top-level score",
            "criteria_scores": {
                "relevance": 0.8,
                "novelty": 0.6,
                "emotional_impact": 0.4,
                "actionable": 0.5,
                "factual": 0.7,
                "personal": 0.3
            }
        })
        # Expected: 0.8*0.3 + 0.6*0.2 + 0.4*0.15 + 0.5*0.15 + 0.7*0.1 + 0.3*0.1
        #         = 0.24 + 0.12 + 0.06 + 0.075 + 0.07 + 0.03 = 0.595
        result = evaluator._parse_importance_response(response)
        assert result == pytest.approx(0.595)

    def test_json_criteria_scores_nested_format(self, evaluator):
        """criteria_scores with nested {"score": float} format."""
        response = json.dumps({
            "reasoning": "detailed breakdown",
            "criteria_scores": {
                "relevance": {"score": 0.9, "reasoning": "very relevant"},
                "novelty": {"score": 0.5, "reasoning": "somewhat new"},
                "emotional_impact": {"score": 0.3, "reasoning": "low"},
                "actionable": {"score": 0.6, "reasoning": "usable"},
                "factual": {"score": 0.8, "reasoning": "verified"},
                "personal": {"score": 0.2, "reasoning": "general"}
            }
        })
        # 0.9*0.3 + 0.5*0.2 + 0.3*0.15 + 0.6*0.15 + 0.8*0.1 + 0.2*0.1
        # = 0.27 + 0.10 + 0.045 + 0.09 + 0.08 + 0.02 = 0.605
        result = evaluator._parse_importance_response(response)
        assert result == pytest.approx(0.605)

    def test_malformed_json_with_field_name_regex(self, evaluator):
        """Malformed JSON but field name pattern present — L2 extracts it."""
        response = 'I think the importance_score: 0.72 based on analysis...'
        assert evaluator._parse_importance_response(response) == pytest.approx(0.72)

    def test_text_with_score_equals(self, evaluator):
        """Alternative pattern: score = 0.65"""
        response = 'After evaluation, the importance score = 0.65'
        assert evaluator._parse_importance_response(response) == pytest.approx(0.65)

    def test_rejects_out_of_range_number_in_field_regex(self, evaluator):
        """Number > 1.0 next to field name should be rejected."""
        response = 'importance_score: 85 (out of 100)'
        assert evaluator._parse_importance_response(response) is None

    def test_rejects_number_slightly_above_one(self, evaluator):
        """1.5 is outside [0, 1], should not clamp."""
        response = json.dumps({"importance_score": 1.5})
        assert evaluator._parse_importance_response(response) is None

    def test_reasoning_with_dimension_count_not_grabbed(self, evaluator):
        """'6 dimensions' should not be treated as importance score."""
        response = 'I evaluated across 6 dimensions and found moderate relevance.'
        assert evaluator._parse_importance_response(response) is None

    def test_percentage_expression_not_grabbed(self, evaluator):
        """'85%' in text should not be misinterpreted."""
        response = 'The content appears to be about 85% factual with high novelty.'
        assert evaluator._parse_importance_response(response) is None

    def test_threshold_reference_not_grabbed(self, evaluator):
        """References to config thresholds should not be used as score."""
        response = 'Since the short_term threshold is 0.6, this seems moderate.'
        assert evaluator._parse_importance_response(response) is None

    def test_completely_unparseable_returns_none(self, evaluator):
        """Gibberish text returns None for safe fallback."""
        response = 'This content is interesting and noteworthy.'
        assert evaluator._parse_importance_response(response) is None

    def test_empty_response_returns_none(self, evaluator):
        assert evaluator._parse_importance_response("") is None

    def test_json_wrapped_in_markdown_code_block(self, evaluator):
        """JSON inside ```json ... ``` should be parsed via parse_json_from_text."""
        payload = json.dumps({"importance_score": 0.88, "reasoning": "critical"})
        response = f"```json\n{payload}\n```"
        assert evaluator._parse_importance_response(response) == pytest.approx(0.88)


class TestLLMBasedEvaluationFallback:
    """Tests that _llm_based_evaluation falls back correctly on parse failure."""

    def test_unparseable_response_falls_back_to_rule_based(self, evaluator):
        mock_llm = MagicMock()
        mock_llm.is_noop = False
        mock_llm.generate_response.return_value = "No numbers or JSON here at all."
        evaluator.set_llm(mock_llm)

        with patch.object(evaluator, '_rule_based_evaluation', return_value=0.35) as mock_rule:
            result = evaluator._llm_based_evaluation("test content", None, None)
            assert result == 0.35
            mock_rule.assert_called_once_with("test content", None, None)

    def test_valid_response_does_not_fallback(self, evaluator):
        mock_llm = MagicMock()
        mock_llm.is_noop = False
        mock_llm.generate_response.return_value = json.dumps({
            "importance_score": 0.9, "reasoning": "critical"
        })
        evaluator.set_llm(mock_llm)

        with patch.object(evaluator, '_rule_based_evaluation') as mock_rule:
            result = evaluator._llm_based_evaluation("test content", None, None)
            assert result == pytest.approx(0.9)
            mock_rule.assert_not_called()

    def test_llm_exception_falls_back_to_rule_based(self, evaluator):
        mock_llm = MagicMock()
        mock_llm.is_noop = False
        mock_llm.generate_response.side_effect = RuntimeError("API timeout")
        evaluator.set_llm(mock_llm)

        with patch.object(evaluator, '_rule_based_evaluation', return_value=0.4) as mock_rule:
            result = evaluator._llm_based_evaluation("test content", None, None)
            assert result == 0.4
            mock_rule.assert_called_once()
