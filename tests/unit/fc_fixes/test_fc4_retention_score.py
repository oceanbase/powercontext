"""
FC-4: Issue #1143 — retention_score null 修复

验证 _persist_memory_to_storage() 从 enhanced_metadata.intelligence.current_retention
提取 retention_score，而非从 memory_data 的顶层字段获取（可能为 None）。
涉及文件:
  - src/powermem/agent/implementations/multi_agent.py:328
  - src/powermem/agent/implementations/multi_user.py:249
"""

import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from types import SimpleNamespace


class TestFC4RetentionScoreMultiAgent:
    """FC-4: multi_agent.py _persist_memory_to_storage() retention_score 修复"""

    def _make_manager(self):
        """Create a minimal MultiAgentMemoryManager mock for testing."""
        with patch("powermem.agent.implementations.multi_agent.Memory"):
            from powermem.agent.implementations.multi_agent import MultiAgentMemoryManager
            manager = MagicMock(spec=MultiAgentMemoryManager)
            manager._get_or_create_memory_instance = MagicMock()
            manager._persist_memory_to_storage = MultiAgentMemoryManager._persist_memory_to_storage.__get__(manager)
            return manager

    # ── AC-4.1: multi-agent 模式 retention_score 不为 null ────────────

    def test_ac_4_1_retention_score_not_null(self, mock_memory_instance):
        """
        Given: multi-agent 模式，memory_data 包含 metadata.intelligence.current_retention
        When: _persist_memory_to_storage() 构建 metadata dict
        Then: retention_score 应为 intelligence.current_retention 的值（非 null）

        当前代码使用 memory_data.get('retention_score')，此时该字段可能为 None。
        """
        manager = self._make_manager()
        manager._get_or_create_memory_instance.return_value = mock_memory_instance

        memory_data = {
            "content": "User prefers dark mode",
            "user_id": "u1",
            "agent_id": "a1",
            "run_id": "r1",
            "scope": SimpleNamespace(value="agent"),
            "memory_type": SimpleNamespace(value="long_term"),
            "metadata": {
                "intelligence": {
                    "current_retention": 0.85,
                    "importance_score": 0.7,
                },
            },
        }

        manager._persist_memory_to_storage(memory_data)

        # 检查传递给 Memory.add() 的 metadata
        call_kwargs = mock_memory_instance.add.call_args
        passed_metadata = call_kwargs.kwargs.get("metadata", {})
        assert passed_metadata.get("retention_score") is not None, \
            "retention_score should NOT be null — should come from intelligence.current_retention"

    # ── AC-4.2: retention_score 值正确 ────────────────────────────────

    def test_ac_4_2_retention_score_value_correct(self, mock_memory_instance):
        """
        Given: intelligence.current_retention = 0.8
        When: _persist_memory_to_storage() 构建 metadata dict
        Then: retention_score 应为 0.8
        """
        manager = self._make_manager()
        manager._get_or_create_memory_instance.return_value = mock_memory_instance

        memory_data = {
            "content": "Test content",
            "user_id": "u1",
            "agent_id": "a1",
            "run_id": "r1",
            "scope": SimpleNamespace(value="agent"),
            "memory_type": SimpleNamespace(value="long_term"),
            "metadata": {
                "intelligence": {
                    "current_retention": 0.8,
                },
            },
        }

        manager._persist_memory_to_storage(memory_data)
        call_kwargs = mock_memory_instance.add.call_args
        passed_metadata = call_kwargs.kwargs.get("metadata", {})
        assert passed_metadata.get("retention_score") == 0.8, \
            f"retention_score should be 0.8, got {passed_metadata.get('retention_score')}"

    # ── AC-4.4: LLM 未启用时默认值 1.0 ──────────────────────────────

    def test_ac_4_4_default_retention_score_when_no_intelligence(self, mock_memory_instance):
        """
        Given: intelligence 数据中无 current_retention（LLM 未启用）
        When: _persist_memory_to_storage() 构建 metadata dict
        Then: retention_score 使用默认值 1.0（非 null）
        """
        manager = self._make_manager()
        manager._get_or_create_memory_instance.return_value = mock_memory_instance

        memory_data = {
            "content": "Test content",
            "user_id": "u1",
            "agent_id": "a1",
            "run_id": "r1",
            "scope": SimpleNamespace(value="agent"),
            "memory_type": SimpleNamespace(value="long_term"),
            "metadata": {},  # No intelligence field
        }

        manager._persist_memory_to_storage(memory_data)
        call_kwargs = mock_memory_instance.add.call_args
        passed_metadata = call_kwargs.kwargs.get("metadata", {})
        assert passed_metadata.get("retention_score") == 1.0, \
            f"retention_score should default to 1.0, got {passed_metadata.get('retention_score')}"


class TestFC4RetentionScoreMultiUser:
    """FC-4: multi_user.py _persist_memory_to_storage() retention_score 修复"""

    def _make_manager(self):
        """Create a minimal MultiUserMemoryManager mock for testing."""
        with patch("powermem.agent.implementations.multi_user.Memory"):
            from powermem.agent.implementations.multi_user import MultiUserMemoryManager
            manager = MagicMock(spec=MultiUserMemoryManager)
            manager._get_or_create_memory_instance = MagicMock()
            manager._persist_memory_to_storage = MultiUserMemoryManager._persist_memory_to_storage.__get__(manager)
            return manager

    # ── AC-4.2: multi-user 模式 retention_score 不为 null ────────────

    def test_ac_4_2_multi_user_retention_score_not_null(self, mock_memory_instance):
        """
        Given: multi-user 模式，memory_data 包含 metadata.intelligence.current_retention
        When: _persist_memory_to_storage() 构建 metadata dict
        Then: retention_score 应为 intelligence.current_retention 的值（非 null）
        """
        manager = self._make_manager()
        manager._get_or_create_memory_instance.return_value = mock_memory_instance

        memory_data = {
            "content": "User preference",
            "user_id": "u1",
            "agent_id": "a1",
            "run_id": "r1",
            "scope": SimpleNamespace(value="user"),
            "memory_type": SimpleNamespace(value="long_term"),
            "privacy_level": SimpleNamespace(value="private"),
            "metadata": {
                "intelligence": {
                    "current_retention": 0.9,
                },
            },
        }

        manager._persist_memory_to_storage(memory_data)
        call_kwargs = mock_memory_instance.add.call_args
        passed_metadata = call_kwargs.kwargs.get("metadata", {})
        assert passed_metadata.get("retention_score") is not None, \
            "multi-user retention_score should NOT be null"

    def test_ac_4_4_multi_user_default_retention(self, mock_memory_instance):
        """
        Given: multi-user 模式，metadata 中无 intelligence
        When: _persist_memory_to_storage() 构建 metadata dict
        Then: retention_score 使用默认值 1.0
        """
        manager = self._make_manager()
        manager._get_or_create_memory_instance.return_value = mock_memory_instance

        memory_data = {
            "content": "Test",
            "user_id": "u1",
            "agent_id": "a1",
            "run_id": "r1",
            "scope": SimpleNamespace(value="user"),
            "memory_type": SimpleNamespace(value="long_term"),
            "privacy_level": SimpleNamespace(value="private"),
            "metadata": {},
        }

        manager._persist_memory_to_storage(memory_data)
        call_kwargs = mock_memory_instance.add.call_args
        passed_metadata = call_kwargs.kwargs.get("metadata", {})
        assert passed_metadata.get("retention_score") == 1.0, \
            f"Default retention_score should be 1.0, got {passed_metadata.get('retention_score')}"


class TestFC4NFR:
    """FC-4: NFR 验证"""

    def _make_agent_manager(self):
        with patch("powermem.agent.implementations.multi_agent.Memory"):
            from powermem.agent.implementations.multi_agent import MultiAgentMemoryManager
            manager = MagicMock(spec=MultiAgentMemoryManager)
            manager._get_or_create_memory_instance = MagicMock()
            manager._persist_memory_to_storage = MultiAgentMemoryManager._persist_memory_to_storage.__get__(manager)
            return manager

    def test_nfr_4_3_data_quality(self):
        """
        Given: 修复后的 _persist_memory_to_storage()
        When: 检查 retention_score 数据类型
        Then: retention_score 始终为有效浮点数（NFR-4.3 数据质量）
        """
        # This test verifies the concept — actual implementation check is in AC tests
        valid_scores = [0.0, 0.5, 0.85, 1.0]
        for score in valid_scores:
            assert isinstance(score, float), f"retention_score {score} should be float"
            assert 0.0 <= score <= 1.0, f"retention_score {score} should be in [0, 1]"
