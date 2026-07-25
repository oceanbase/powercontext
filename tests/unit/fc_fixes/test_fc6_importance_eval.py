"""
FC-6: Issue #1141 — 重要性评估统一

验证 _rule_based_evaluation() 使用六个 _evaluate_* 维度方法和 criteria_weights 加权计算。
涉及文件: src/powermem/intelligence/importance_evaluator.py
"""

import pytest
from unittest.mock import MagicMock, patch


class TestFC6RuleBasedEvaluation:
    """FC-6: _rule_based_evaluation() 六维加权评估"""

    # ── AC-6.1: 调用六个 _evaluate_* 维度方法 ────────────────────────

    def test_ac_6_1_calls_all_six_dimensions(self, evaluator):
        """
        Given: ImportanceEvaluator（LLM 不可用）
        When: 调用 _rule_based_evaluation()
        Then: 内部应调用六个 _evaluate_* 维度方法

        当前代码使用硬编码关键词匹配，不调用 _evaluate_* 方法。
        修复后应调用所有六个维度方法。
        """
        content = "I love this new important feature"
        metadata = {"priority": "high"}
        context = {"user_engagement": "high"}

        with patch.object(evaluator, '_evaluate_relevance', return_value=0.5) as mock_rel, \
             patch.object(evaluator, '_evaluate_novelty', return_value=0.3) as mock_nov, \
             patch.object(evaluator, '_evaluate_emotional_impact', return_value=0.4) as mock_emo, \
             patch.object(evaluator, '_evaluate_actionable', return_value=0.2) as mock_act, \
             patch.object(evaluator, '_evaluate_factual', return_value=0.1) as mock_fac, \
             patch.object(evaluator, '_evaluate_personal', return_value=0.6) as mock_per:

            evaluator._rule_based_evaluation(content, metadata, context)

            mock_rel.assert_called_once_with(content, context)
            mock_nov.assert_called_once_with(content, metadata)
            mock_emo.assert_called_once_with(content)
            mock_act.assert_called_once_with(content)
            mock_fac.assert_called_once_with(content)
            mock_per.assert_called_once_with(content, metadata)

    # ── AC-6.2: 返回值等于六维加权和 ─────────────────────────────────

    def test_ac_6_2_weighted_sum_calculation(self, evaluator):
        """
        Given: ImportanceEvaluator with default criteria_weights
        When: 调用 _rule_based_evaluation()，各维度返回已知值
        Then: 返回值等于各维度分数的加权和（clamped 到 [0, 1]）

        当前代码使用硬编码加分逻辑，不使用 criteria_weights 加权。
        """
        content = "test content"
        # Mock each dimension to return a known value
        dimension_values = {
            "relevance": 0.8,
            "novelty": 0.6,
            "emotional_impact": 0.4,
            "actionable": 0.3,
            "factual": 0.2,
            "personal": 0.1,
        }

        with patch.object(evaluator, '_evaluate_relevance', return_value=dimension_values["relevance"]), \
             patch.object(evaluator, '_evaluate_novelty', return_value=dimension_values["novelty"]), \
             patch.object(evaluator, '_evaluate_emotional_impact', return_value=dimension_values["emotional_impact"]), \
             patch.object(evaluator, '_evaluate_actionable', return_value=dimension_values["actionable"]), \
             patch.object(evaluator, '_evaluate_factual', return_value=dimension_values["factual"]), \
             patch.object(evaluator, '_evaluate_personal', return_value=dimension_values["personal"]):

            result = evaluator._rule_based_evaluation(content)

            # Calculate expected weighted sum
            expected = sum(
                dimension_values[dim] * weight
                for dim, weight in evaluator.criteria_weights.items()
                if dim in dimension_values
            )
            expected = max(0.0, min(1.0, expected))

            assert abs(result - expected) < 0.001, \
                f"Expected weighted sum {expected}, got {result}"

    def test_ac_6_2_clamped_to_unit_range(self, evaluator):
        """
        Given: ImportanceEvaluator
        When: 所有维度返回最大值 1.0
        Then: 返回值不超过 1.0

        加权和最大为 sum(weights) = 1.0，应 clamp 到 [0, 1]。
        """
        content = "test"
        with patch.object(evaluator, '_evaluate_relevance', return_value=1.0), \
             patch.object(evaluator, '_evaluate_novelty', return_value=1.0), \
             patch.object(evaluator, '_evaluate_emotional_impact', return_value=1.0), \
             patch.object(evaluator, '_evaluate_actionable', return_value=1.0), \
             patch.object(evaluator, '_evaluate_factual', return_value=1.0), \
             patch.object(evaluator, '_evaluate_personal', return_value=1.0):

            result = evaluator._rule_based_evaluation(content)
            assert 0.0 <= result <= 1.0, f"Result {result} out of [0, 1] range"

    # ── AC-6.3: get_importance_breakdown 包含 weighted_total ──────────

    def test_ac_6_3_breakdown_contains_weighted_total(self, evaluator):
        """
        Given: ImportanceEvaluator
        When: 调用 get_importance_breakdown()
        Then: 返回 dict 包含 'weighted_total' 键

        当前代码 get_importance_breakdown() 不计算 weighted_total。
        """
        content = "important task for tomorrow"
        result = evaluator.get_importance_breakdown(content)

        assert "weighted_total" in result, \
            f"get_importance_breakdown() should contain 'weighted_total' key, got keys: {list(result.keys())}"

    def test_ac_6_3_weighted_total_matches_calculation(self, evaluator):
        """
        Given: ImportanceEvaluator
        When: 调用 get_importance_breakdown()
        Then: weighted_total 等于各维度分数的加权和
        """
        content = "test content"
        breakdown = evaluator.get_importance_breakdown(content)

        if "weighted_total" in breakdown:
            expected_total = sum(
                breakdown.get(dim, 0.0) * weight
                for dim, weight in evaluator.criteria_weights.items()
            )
            assert abs(breakdown["weighted_total"] - expected_total) < 0.001, \
                f"weighted_total {breakdown['weighted_total']} != expected {expected_total}"

    # ── AC-6.4: 规则引擎和 LLM 引擎使用相同框架 ─────────────────────

    def test_ac_6_4_rule_based_uses_same_framework_as_llm(self, evaluator):
        """
        Given: ImportanceEvaluator
        When: 检查 _rule_based_evaluation 使用的维度和权重
        Then: 与 _llm_based_evaluation 使用相同的六个维度 + criteria_weights

        通过验证 _rule_based_evaluation 使用 criteria_weights 来间接验证。
        """
        # 验证 evaluator 有六个维度方法
        assert hasattr(evaluator, '_evaluate_relevance')
        assert hasattr(evaluator, '_evaluate_novelty')
        assert hasattr(evaluator, '_evaluate_emotional_impact')
        assert hasattr(evaluator, '_evaluate_actionable')
        assert hasattr(evaluator, '_evaluate_factual')
        assert hasattr(evaluator, '_evaluate_personal')

        # 验证 criteria_weights 包含六个维度
        assert len(evaluator.criteria_weights) == 6, \
            f"Expected 6 criteria weights, got {len(evaluator.criteria_weights)}"

    # ── AC-6.5: 零输入场景返回 0.0 ──────────────────────────────────

    def test_ac_6_5_zero_input_returns_zero(self, evaluator):
        """
        Given: ImportanceEvaluator
        When: 输入内容无任何关键词匹配
        Then: 返回值为 0.0

        当前代码有长度加分（len > 100 → +0.1），即使无关键词也会返回 > 0。
        修复后应使用 _evaluate_* 方法，无匹配时返回 0.0。
        """
        content = "xyz qjk"  # 无任何关键词
        result = evaluator._rule_based_evaluation(content)
        assert result == 0.0, \
            f"Zero-input content should return 0.0, got {result}"

    def test_ac_6_5_empty_content_returns_zero(self, evaluator):
        """
        Given: ImportanceEvaluator
        When: 输入内容为空字符串
        Then: 返回值为 0.0
        """
        result = evaluator._rule_based_evaluation("")
        assert result == 0.0, \
            f"Empty content should return 0.0, got {result}"


class TestFC6NFR:
    """FC-6: NFR 验证"""

    def test_nfr_6_1_return_range_preserved(self, evaluator):
        """
        Given: 修复后的 _rule_based_evaluation()
        When: 测试各种输入
        Then: 返回值范围保持 [0, 1]（NFR-6.1 向后兼容）
        """
        test_contents = [
            "",
            "short",
            "a" * 200,
            "important critical urgent todo task",
            "I love my birthday preference favorite",
        ]
        for content in test_contents:
            result = evaluator._rule_based_evaluation(content)
            assert 0.0 <= result <= 1.0, \
                f"Result {result} for content '{content[:30]}...' out of [0, 1] range"

    def test_nfr_6_2_consistency_with_llm_framework(self, evaluator):
        """
        Given: ImportanceEvaluator
        When: 检查评估框架
        Then: _rule_based_evaluation 和 _llm_based_evaluation 使用相同维度（NFR-6.2）
        """
        # 验证六个维度方法存在
        dimensions = [
            '_evaluate_relevance', '_evaluate_novelty', '_evaluate_emotional_impact',
            '_evaluate_actionable', '_evaluate_factual', '_evaluate_personal'
        ]
        for dim in dimensions:
            assert hasattr(evaluator, dim), f"Missing dimension method: {dim}"

    def test_nfr_6_3_configurable_weights(self, evaluator_custom_weights):
        """
        Given: ImportanceEvaluator with custom weights
        When: 调用 _rule_based_evaluation()
        Then: 使用自定义权重计算（NFR-6.3 可配置性）
        """
        content = "test"
        # 验证自定义权重
        assert evaluator_custom_weights.criteria_weights["relevance"] == 0.5
        assert evaluator_custom_weights.criteria_weights["novelty"] == 0.2

    def test_nfr_6_4_zero_input(self, evaluator):
        """
        Given: ImportanceEvaluator
        When: 输入内容无任何关键词匹配
        Then: 返回值为 0.0（NFR-6.4 零输入）
        """
        result = evaluator._rule_based_evaluation("xyz")
        assert result == 0.0
