"""
FC-3: Issue #1151 — OceanBase forget marker

验证 _forget_marker_updates() 返回值同时包含 top-level 字段和 metadata 内嵌字段。
涉及文件: src/powermem/core/memory.py:56
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone


class TestFC3ForgetMarker:
    """FC-3: OceanBase forget marker 修复"""

    def _call_forget_marker_updates(self):
        """Import and call _forget_marker_updates()."""
        from powermem.core.memory import _forget_marker_updates
        return _forget_marker_updates()

    # ── AC-3.1: OceanBase metadata JSON 包含 should_forget ────────────

    def test_ac_3_1_metadata_contains_should_forget(self, mock_get_current_datetime):
        """
        Given: _forget_marker_updates() 被调用
        When: 检查返回值
        Then: 返回值的 'metadata' dict 中包含 should_forget: True

        当前代码仅返回 top-level 字段，不含 metadata 内嵌字段。
        OceanBase 的 _build_record_for_insert() 会丢弃 top-level 字段。
        """
        result = self._call_forget_marker_updates()
        assert "metadata" in result, \
            "Result should contain 'metadata' key for OceanBase compatibility"
        assert result["metadata"]["should_forget"] is True, \
            "metadata.should_forget should be True"

    # ── AC-3.2: OceanBase metadata JSON 包含 marked_for_forgetting_at ─

    def test_ac_3_2_metadata_contains_marked_for_forgetting_at(self, mock_get_current_datetime):
        """
        Given: _forget_marker_updates() 被调用
        When: 检查返回值
        Then: 返回值的 'metadata' dict 中包含 marked_for_forgetting_at 且为有效 ISO 时间戳

        当前代码仅返回 top-level 字段，不含 metadata 内嵌字段。
        """
        result = self._call_forget_marker_updates()
        assert "metadata" in result, \
            "Result should contain 'metadata' key"
        assert "marked_for_forgetting_at" in result["metadata"], \
            "metadata should contain marked_for_forgetting_at"
        # 验证是有效 ISO 时间戳
        timestamp = result["metadata"]["marked_for_forgetting_at"]
        parsed = datetime.fromisoformat(timestamp)
        assert parsed is not None, "marked_for_forgetting_at should be valid ISO timestamp"

    # ── AC-3.3: SQLite 回归测试 — top-level 字段仍存在 ────────────────

    def test_ac_3_3_top_level_fields_preserved(self, mock_get_current_datetime):
        """
        Given: _forget_marker_updates() 被调用
        When: 检查返回值
        Then: top-level 的 should_forget 和 marked_for_forgetting_at 仍存在（兼容 SQLite）

        修复应新增 metadata 内嵌字段，同时保留 top-level 字段。
        """
        result = self._call_forget_marker_updates()
        assert result.get("should_forget") is True, \
            "Top-level should_forget should be True (SQLite compatibility)"
        assert "marked_for_forgetting_at" in result, \
            "Top-level marked_for_forgetting_at should exist (SQLite compatibility)"
        # 验证是有效 ISO 时间戳
        timestamp = result["marked_for_forgetting_at"]
        parsed = datetime.fromisoformat(timestamp)
        assert parsed is not None

    def test_ac_3_3_sqlite_regression_no_break(self, mock_get_current_datetime):
        """
        Given: SQLite 存储后端
        When: storage.update_memory() 接收 _forget_marker_updates() 的返回值
        Then: 返回值结构不破坏现有行为

        SQLite 后端直接处理 top-level 字段，新增 metadata 键不应干扰。
        """
        result = self._call_forget_marker_updates()
        # SQLite 后端使用 top-level 字段
        assert result["should_forget"] is True
        assert "marked_for_forgetting_at" in result
        # 新增的 metadata 键不应覆盖 top-level
        assert "metadata" in result

    # ── AC-3.4: get_all/search 返回的记忆包含遗忘标记 ─────────────────

    def test_ac_3_4_forget_marker_in_updates_dict(self, mock_get_current_datetime):
        """
        Given: _forget_marker_updates() 返回值用于 storage.update_memory()
        When: 模拟 updates.update() 合并
        Then: 合并后的 dict 包含 metadata.should_forget

        验证 updates.update(_forget_marker_updates()) 正确传播标记。
        """
        result = self._call_forget_marker_updates()
        # 模拟已有 updates dict
        existing_updates = {"metadata": {"existing_key": "value"}}
        existing_updates.update(result)
        # metadata 应该被覆盖为 forget marker 的 metadata
        assert existing_updates["metadata"]["should_forget"] is True


class TestFC3NFR:
    """FC-3: NFR 验证"""

    def test_nfr_3_1_backward_compatible(self, mock_get_current_datetime):
        """
        Given: 修复后的 _forget_marker_updates()
        When: 检查返回值结构
        Then: 返回值仍包含 top-level should_forget 和 marked_for_forgetting_at（NFR-3.1）
        """
        from powermem.core.memory import _forget_marker_updates
        result = _forget_marker_updates()
        assert "should_forget" in result
        assert "marked_for_forgetting_at" in result

    def test_nfr_3_2_minimal_change(self, mock_get_current_datetime):
        """
        Given: 修复后的 _forget_marker_updates()
        When: 检查返回值键数
        Then: 仅新增 'metadata' 键（NFR-3.2 最小变更）
        """
        from powermem.core.memory import _forget_marker_updates
        result = _forget_marker_updates()
        # 修复前有 2 个键，修复后有 3 个键
        expected_keys = {"should_forget", "marked_for_forgetting_at", "metadata"}
        assert set(result.keys()) == expected_keys, \
            f"Expected keys {expected_keys}, got {set(result.keys())}"

    def test_nfr_3_3_data_integrity(self, mock_get_current_datetime):
        """
        Given: 修复后的 _forget_marker_updates()
        When: 检查 metadata 和 top-level 字段一致性
        Then: metadata 中的值与 top-level 值一致（NFR-3.3 数据完整性）
        """
        from powermem.core.memory import _forget_marker_updates
        result = _forget_marker_updates()
        assert result["should_forget"] == result["metadata"]["should_forget"]
        assert result["marked_for_forgetting_at"] == result["metadata"]["marked_for_forgetting_at"]
