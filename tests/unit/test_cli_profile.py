"""
Unit tests for PowerMem CLI profile commands and memory add/search profile extensions.

Uses click.testing.CliRunner with mocked CLIContext to avoid external dependencies.
Cross-platform compatible (Windows, macOS, Linux) via tmp_path fixture.
"""

import json
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from click.testing import CliRunner

from powermem.cli.main import cli


def _make_mock_user_memory():
    """Create a mock UserMemory instance."""
    um = MagicMock()
    um.profile.return_value = {
        "id": 1,
        "user_id": "user123",
        "profile_content": "Likes Python and dark mode",
        "topics": {"interests": {"programming": "Python"}},
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-06-25T00:00:00",
    }
    um.profile_list.return_value = [
        {
            "id": 1,
            "user_id": "user123",
            "profile_content": "Likes Python",
            "topics": {},
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-06-25T00:00:00",
        },
        {
            "id": 2,
            "user_id": "user456",
            "profile_content": "Prefers dark mode",
            "topics": {},
            "created_at": "2026-01-02T00:00:00",
            "updated_at": "2026-06-25T00:00:00",
        },
    ]
    um.delete_profile.return_value = True
    um.add.return_value = {
        "results": [{"id": 12345, "event": "ADD", "memory": "test"}],
        "profile_extracted": True,
        "profile_content": "Test profile",
    }
    um.search.return_value = {
        "results": [
            {"id": 111, "memory": "found memory", "score": 0.95},
        ],
        "profile_content": "User profile content",
        "topics": {"interests": {"sport": "Basketball"}},
    }
    return um


def _make_mock_memory():
    """Create a mock Memory instance."""
    mem = MagicMock()
    mem.add.return_value = {
        "results": [{"id": 99999, "event": "ADD", "memory": "test content"}],
    }
    mem.search.return_value = {
        "results": [{"id": 111, "memory": "found memory", "score": 0.8}],
    }
    return mem


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mock_user_memory():
    return _make_mock_user_memory()


@pytest.fixture
def mock_memory():
    return _make_mock_memory()


# ==================== Profile Get ====================

class TestProfileGet:

    def test_profile_get_success(self, runner, mock_user_memory):
        with patch("powermem.cli.commands.profile.CLIContext.user_memory",
                   new_callable=PropertyMock, return_value=mock_user_memory):
            result = runner.invoke(cli, ["profile", "get", "user123"])
        assert result.exit_code == 0
        assert "user123" in result.output
        assert "User Profile" in result.output or "User ID" in result.output

    def test_profile_get_not_found(self, runner, mock_user_memory):
        mock_user_memory.profile.return_value = {}
        with patch("powermem.cli.commands.profile.CLIContext.user_memory",
                   new_callable=PropertyMock, return_value=mock_user_memory):
            result = runner.invoke(cli, ["profile", "get", "nonexistent"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower() or "error" in result.output.lower()

    def test_profile_get_json(self, runner, mock_user_memory):
        with patch("powermem.cli.commands.profile.CLIContext.user_memory",
                   new_callable=PropertyMock, return_value=mock_user_memory):
            result = runner.invoke(cli, ["profile", "get", "user123", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["user_id"] == "user123"

    def test_profile_get_missing_user_id(self, runner):
        result = runner.invoke(cli, ["profile", "get"])
        assert result.exit_code != 0


# ==================== Profile List ====================

class TestProfileList:

    def test_profile_list_all(self, runner, mock_user_memory):
        with patch("powermem.cli.commands.profile.CLIContext.user_memory",
                   new_callable=PropertyMock, return_value=mock_user_memory):
            result = runner.invoke(cli, ["profile", "list"])
        assert result.exit_code == 0
        assert "user123" in result.output
        assert "user456" in result.output

    def test_profile_list_by_user_id(self, runner, mock_user_memory):
        mock_user_memory.profile_list.return_value = [
            {"id": 1, "user_id": "user123", "profile_content": "Likes Python", "topics": {},
             "created_at": "2026-01-01T00:00:00", "updated_at": "2026-06-25T00:00:00"},
        ]
        with patch("powermem.cli.commands.profile.CLIContext.user_memory",
                   new_callable=PropertyMock, return_value=mock_user_memory):
            result = runner.invoke(cli, ["profile", "list", "--user-id", "user123"])
        assert result.exit_code == 0
        mock_user_memory.profile_list.assert_called_once_with(
            user_id="user123",
            main_topic=None,
            sub_topic=None,
            topic_value=None,
            limit=100,
            offset=0,
        )

    def test_profile_list_with_main_topic(self, runner, mock_user_memory):
        with patch("powermem.cli.commands.profile.CLIContext.user_memory",
                   new_callable=PropertyMock, return_value=mock_user_memory):
            result = runner.invoke(cli, ["profile", "list", "--main-topic", "interests"])
        assert result.exit_code == 0
        mock_user_memory.profile_list.assert_called_once_with(
            user_id=None,
            main_topic=["interests"],
            sub_topic=None,
            topic_value=None,
            limit=100,
            offset=0,
        )

    def test_profile_list_with_sub_topic(self, runner, mock_user_memory):
        with patch("powermem.cli.commands.profile.CLIContext.user_memory",
                   new_callable=PropertyMock, return_value=mock_user_memory):
            result = runner.invoke(cli, ["profile", "list", "--sub-topic", "interests.sport"])
        assert result.exit_code == 0
        mock_user_memory.profile_list.assert_called_once_with(
            user_id=None,
            main_topic=None,
            sub_topic=["interests.sport"],
            topic_value=None,
            limit=100,
            offset=0,
        )

    def test_profile_list_json(self, runner, mock_user_memory):
        with patch("powermem.cli.commands.profile.CLIContext.user_memory",
                   new_callable=PropertyMock, return_value=mock_user_memory):
            result = runner.invoke(cli, ["profile", "list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "profiles" in data
        assert data["count"] == 2

    def test_profile_list_empty(self, runner, mock_user_memory):
        mock_user_memory.profile_list.return_value = []
        with patch("powermem.cli.commands.profile.CLIContext.user_memory",
                   new_callable=PropertyMock, return_value=mock_user_memory):
            result = runner.invoke(cli, ["profile", "list"])
        assert result.exit_code == 0
        assert "no profiles found" in result.output.lower()


# ==================== Profile Delete ====================

class TestProfileDelete:

    def test_profile_delete_success(self, runner, mock_user_memory):
        with patch("powermem.cli.commands.profile.CLIContext.user_memory",
                   new_callable=PropertyMock, return_value=mock_user_memory):
            result = runner.invoke(cli, ["profile", "delete", "user123", "--yes"])
        assert result.exit_code == 0
        assert "deleted" in result.output.lower()

    def test_profile_delete_not_found(self, runner, mock_user_memory):
        mock_user_memory.delete_profile.return_value = False
        with patch("powermem.cli.commands.profile.CLIContext.user_memory",
                   new_callable=PropertyMock, return_value=mock_user_memory):
            result = runner.invoke(cli, ["profile", "delete", "nonexistent", "--yes"])
        assert result.exit_code != 0
        assert "not found" in result.output.lower()

    def test_profile_delete_cancel(self, runner, mock_user_memory):
        with patch("powermem.cli.commands.profile.CLIContext.user_memory",
                   new_callable=PropertyMock, return_value=mock_user_memory):
            result = runner.invoke(cli, ["profile", "delete", "user123"], input="n\n")
        assert result.exit_code == 0
        assert "cancelled" in result.output.lower()
        mock_user_memory.delete_profile.assert_not_called()


# ==================== Memory Add with Profile ====================

class TestMemoryAddWithProfile:

    def test_add_with_profile(self, runner, mock_user_memory):
        with patch("powermem.cli.commands.memory.CLIContext.user_memory",
                   new_callable=PropertyMock, return_value=mock_user_memory):
            result = runner.invoke(cli, [
                "memory", "add", "I like Python",
                "--user-id", "user123",
                "--with-profile",
            ])
        assert result.exit_code == 0
        assert "SUCCESS" in result.output
        assert "profile extracted" in result.output.lower()

    def test_add_with_profile_no_user_id(self, runner):
        result = runner.invoke(cli, [
            "memory", "add", "I like Python",
            "--with-profile",
        ])
        assert result.exit_code != 0
        assert "requires --user-id" in result.output.lower() or "error" in result.output.lower()

    def test_add_with_profile_type_topics(self, runner, mock_user_memory):
        with patch("powermem.cli.commands.memory.CLIContext.user_memory",
                   new_callable=PropertyMock, return_value=mock_user_memory):
            result = runner.invoke(cli, [
                "memory", "add", "I like Python",
                "--user-id", "user123",
                "--with-profile",
                "--profile-type", "topics",
            ])
        assert result.exit_code == 0
        mock_user_memory.add.assert_called_once()
        call_kwargs = mock_user_memory.add.call_args
        assert call_kwargs.kwargs.get("profile_type") == "topics" or \
               (call_kwargs[1] if len(call_kwargs) > 1 else {}).get("profile_type") == "topics"

    def test_add_without_profile(self, runner, mock_memory):
        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, [
                "memory", "add", "test content",
                "--user-id", "user123",
            ])
        assert result.exit_code == 0
        assert "SUCCESS" in result.output
        mock_memory.add.assert_called_once()


# ==================== Memory Search with Profile ====================

class TestMemorySearchWithProfile:

    def test_search_add_profile(self, runner, mock_user_memory):
        with patch("powermem.cli.commands.memory.CLIContext.user_memory",
                   new_callable=PropertyMock, return_value=mock_user_memory):
            result = runner.invoke(cli, [
                "memory", "search", "preferences",
                "--user-id", "user123",
                "--add-profile",
            ])
        assert result.exit_code == 0
        assert "User Profile" in result.output
        mock_user_memory.search.assert_called_once()

    def test_search_add_profile_no_user_id(self, runner):
        result = runner.invoke(cli, [
            "memory", "search", "preferences",
            "--add-profile",
        ])
        assert result.exit_code != 0
        assert "requires --user-id" in result.output.lower() or "error" in result.output.lower()

    def test_search_add_profile_json(self, runner, mock_user_memory):
        with patch("powermem.cli.commands.memory.CLIContext.user_memory",
                   new_callable=PropertyMock, return_value=mock_user_memory):
            result = runner.invoke(cli, [
                "memory", "search", "preferences",
                "--user-id", "user123",
                "--add-profile",
                "--json",
            ])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "results" in data

    def test_search_without_profile(self, runner, mock_memory):
        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, [
                "memory", "search", "test query",
            ])
        assert result.exit_code == 0
        mock_memory.search.assert_called_once()
