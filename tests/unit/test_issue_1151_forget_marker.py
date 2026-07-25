"""Unit tests for Issue #1151 — OceanBase forget marker (FC-3).

Verifies that _forget_marker_updates() returns a metadata dict containing
should_forget and marked_for_forgetting_at for OceanBase storage.
"""

from unittest.mock import patch
from datetime import datetime

import pytest

from powermem.core.memory import _forget_marker_updates


class TestIssue1151ForgetMarker:
    """FC-3: _forget_marker_updates() must include metadata dict."""

    @patch("powermem.core.memory.get_current_datetime")
    def test_returns_metadata_key(self, mock_dt):
        """AC-3.1: Return dict contains 'metadata' key."""
        mock_dt.return_value = datetime(2026, 7, 25, 12, 0, 0)
        result = _forget_marker_updates()
        assert "metadata" in result, (
            "_forget_marker_updates() must include 'metadata' key for OceanBase storage"
        )

    @patch("powermem.core.memory.get_current_datetime")
    def test_metadata_contains_should_forget(self, mock_dt):
        """AC-3.2: metadata dict contains 'should_forget': True."""
        mock_dt.return_value = datetime(2026, 7, 25, 12, 0, 0)
        result = _forget_marker_updates()
        metadata = result.get("metadata", {})
        assert "should_forget" in metadata, (
            "metadata dict must contain 'should_forget'"
        )
        assert metadata["should_forget"] is True

    @patch("powermem.core.memory.get_current_datetime")
    def test_metadata_contains_marked_for_forgetting_at(self, mock_dt):
        """AC-3.3: metadata dict contains 'marked_for_forgetting_at' with ISO timestamp."""
        mock_dt.return_value = datetime(2026, 7, 25, 12, 0, 0)
        result = _forget_marker_updates()
        metadata = result.get("metadata", {})
        assert "marked_for_forgetting_at" in metadata, (
            "metadata dict must contain 'marked_for_forgetting_at'"
        )
        assert metadata["marked_for_forgetting_at"] == "2026-07-25T12:00:00"

    @patch("powermem.core.memory.get_current_datetime")
    def test_top_level_fields_preserved(self, mock_dt):
        """NFR-3.1: Top-level should_forget and marked_for_forgetting_at still exist
        for backward compatibility with SQLite and other backends."""
        mock_dt.return_value = datetime(2026, 7, 25, 12, 0, 0)
        result = _forget_marker_updates()
        assert result.get("should_forget") is True, (
            "Top-level 'should_forget' must be preserved for backward compatibility"
        )
        assert "marked_for_forgetting_at" in result, (
            "Top-level 'marked_for_forgetting_at' must be preserved for backward compatibility"
        )

    @patch("powermem.core.memory.get_current_datetime")
    def test_metadata_matches_top_level_values(self, mock_dt):
        """AC-3.4: metadata values match top-level values."""
        mock_dt.return_value = datetime(2026, 7, 25, 12, 0, 0)
        result = _forget_marker_updates()
        assert result["should_forget"] == result["metadata"]["should_forget"]
        assert result["marked_for_forgetting_at"] == result["metadata"]["marked_for_forgetting_at"]

    @patch("powermem.core.memory.get_current_datetime")
    def test_marked_at_is_iso_format(self, mock_dt):
        """AC-3.5: marked_for_forgetting_at is a valid ISO format string."""
        mock_dt.return_value = datetime(2026, 7, 25, 12, 0, 0)
        result = _forget_marker_updates()
        ts = result["metadata"]["marked_for_forgetting_at"]
        # Should be parseable as ISO datetime
        parsed = datetime.fromisoformat(ts)
        assert parsed.year == 2026
