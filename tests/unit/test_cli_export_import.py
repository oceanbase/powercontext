"""
Unit tests for PowerMem CLI export, import, batch, quality, and optimize commands.

Uses click.testing.CliRunner with mocked CLIContext to avoid external dependencies.
Cross-platform compatible (Windows, macOS, Linux) via tmp_path fixture.
"""

import json
import os
import pytest
from unittest.mock import MagicMock, patch, PropertyMock

from click.testing import CliRunner

from powermem.core.memory import Memory
from powermem.cli.commands.interactive import InteractiveSession
from powermem.cli.main import cli


def _make_mock_memory():
    """Create a mock Memory instance with export/import/optimize support."""
    mem = MagicMock()

    mem.export_memories.return_value = json.dumps([
        {"id": 1, "memory": "Test memory 1", "user_id": "u1"},
        {"id": 2, "memory": "Test memory 2", "user_id": "u1"},
    ], ensure_ascii=False, indent=2)

    mem.import_memories.return_value = {"success": 3, "failed": 0}

    mem.add.return_value = {
        "results": [{"id": 100, "event": "ADD", "memory": "batch item"}],
    }

    mem.get_all.return_value = {
        "results": [
            {"id": 1, "memory": "Short", "metadata": {"k": "v"}},
            {"id": 2, "memory": "This is a reasonably long memory content", "metadata": {}},
            {"id": 3, "memory": "", "metadata": None},
        ],
    }

    mem.optimize.return_value = {
        "duplicates_found": 2,
        "duplicates_removed": 2,
        "total_scanned": 10,
    }

    return mem


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def mock_memory():
    return _make_mock_memory()


# ==================== Export ====================

class TestExport:

    def test_export_json_stdout(self, runner, mock_memory):
        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, ["memory", "export"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 2

    def test_export_json_to_file(self, runner, mock_memory, tmp_path):
        out_file = str(tmp_path / "export.json")
        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, ["memory", "export", "--output", out_file])
        assert result.exit_code == 0
        assert "exported" in result.output.lower() or "success" in result.output.lower()
        assert os.path.exists(out_file)
        with open(out_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert len(data) == 2

    def test_export_csv_format(self, runner, mock_memory):
        mock_memory.export_memories.return_value = "id,memory,user_id\n1,Test,u1\n"
        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, ["memory", "export", "--format", "csv"])
        assert result.exit_code == 0
        assert "id,memory" in result.output

    def test_export_with_user_filter(self, runner, mock_memory):
        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, ["memory", "export", "--user-id", "u1"])
        assert result.exit_code == 0
        mock_memory.export_memories.assert_called_once_with(
            format="json",
            user_id="u1",
            agent_id=None,
            run_id=None,
            limit=1000,
        )

    def test_export_with_limit(self, runner, mock_memory):
        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, ["memory", "export", "--limit", "50"])
        assert result.exit_code == 0
        mock_memory.export_memories.assert_called_once_with(
            format="json",
            user_id=None,
            agent_id=None,
            run_id=None,
            limit=50,
        )

    def test_export_creates_parent_dirs(self, runner, mock_memory, tmp_path):
        nested = str(tmp_path / "sub" / "dir" / "export.json")
        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, ["memory", "export", "--output", nested])
        assert result.exit_code == 0
        assert os.path.exists(nested)

    def test_export_error(self, runner, mock_memory):
        mock_memory.export_memories.side_effect = RuntimeError("DB down")
        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, ["memory", "export"])
        assert result.exit_code != 0
        assert "error" in result.output.lower()


# ==================== Import ====================

class TestImport:

    def test_import_json(self, runner, mock_memory, tmp_path):
        in_file = str(tmp_path / "import.json")
        data = [{"content": "mem1"}, {"content": "mem2"}]
        with open(in_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, ["memory", "import", in_file])
        assert result.exit_code == 0
        assert "success" in result.output.lower()

    def test_import_csv(self, runner, mock_memory, tmp_path):
        in_file = str(tmp_path / "import.csv")
        with open(in_file, "w", encoding="utf-8") as f:
            f.write("content,user_id\nmemory one,u1\n")

        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, ["memory", "import", in_file, "--format", "csv"])
        assert result.exit_code == 0
        assert "success" in result.output.lower()

    def test_import_with_user_override(self, runner, mock_memory, tmp_path):
        in_file = str(tmp_path / "import.json")
        with open(in_file, "w", encoding="utf-8") as f:
            json.dump([{"content": "test"}], f)

        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, [
                "memory", "import", in_file, "--user-id", "override_user",
            ])
        assert result.exit_code == 0
        mock_memory.import_memories.assert_called_once()
        call_kwargs = mock_memory.import_memories.call_args
        assert call_kwargs.kwargs.get("user_id") == "override_user" or \
               (len(call_kwargs) > 1 and call_kwargs[1].get("user_id") == "override_user")

    def test_import_nonexistent_file(self, runner, mock_memory):
        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, ["memory", "import", "/nonexistent/file.json"])
        assert result.exit_code != 0

    def test_import_empty_file(self, runner, mock_memory, tmp_path):
        in_file = str(tmp_path / "empty.json")
        with open(in_file, "w", encoding="utf-8") as f:
            f.write("")

        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, ["memory", "import", in_file])
        assert result.exit_code != 0
        assert "empty" in result.output.lower() or "error" in result.output.lower()

    def test_import_json_output(self, runner, mock_memory, tmp_path):
        in_file = str(tmp_path / "import.json")
        with open(in_file, "w", encoding="utf-8") as f:
            json.dump([{"content": "test"}], f)

        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, ["memory", "import", in_file, "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["success"] == 3

    def test_import_exits_nonzero_when_all_rows_fail(self, runner, mock_memory, tmp_path):
        in_file = str(tmp_path / "import.json")
        with open(in_file, "w", encoding="utf-8") as f:
            json.dump([{"content": ""}], f)
        mock_memory.import_memories.return_value = {"success": 0, "failed": 1}

        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, ["memory", "import", in_file])
        assert result.exit_code != 0
        assert "0 succeeded" in result.output
        assert "1 failed" in result.output

    def test_import_dict_top_level_fails(self, runner, mock_memory, tmp_path):
        in_file = str(tmp_path / "dict.json")
        with open(in_file, "w", encoding="utf-8") as f:
            json.dump({"content": "one"}, f)
        mock_memory.import_memories.side_effect = ValueError(
            "Invalid JSON top-level shape for import: expected array, got dict"
        )

        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, ["memory", "import", in_file])
        assert result.exit_code != 0
        assert "error" in result.output.lower()


class TestExportImportRoundTrip:

    def test_json_exported_memory_field_imports_as_content(self):
        source = Memory.__new__(Memory)
        source.get_all = MagicMock(return_value={
            "results": [
                {
                    "id": 1,
                    "memory": "Remember the exported JSON body",
                    "user_id": "u1",
                    "agent_id": "a1",
                    "metadata": {"source": "test"},
                },
            ],
        })
        exported = Memory.export_memories(source, format="json")

        target = Memory.__new__(Memory)
        target.add = MagicMock()
        result = Memory.import_memories(target, exported, format="json")

        assert result == {"success": 1, "failed": 0}
        target.add.assert_called_once()
        kwargs = target.add.call_args.kwargs
        assert kwargs["messages"] == "Remember the exported JSON body"
        assert kwargs["user_id"] == "u1"
        assert kwargs["agent_id"] == "a1"
        assert kwargs["metadata"] == {"source": "test"}

    def test_json_export_preserves_and_imports_run_id(self):
        source = Memory.__new__(Memory)
        source.get_all = MagicMock(return_value={
            "results": [
                {
                    "id": 1,
                    "memory": "Session-scoped memory",
                    "user_id": "u1",
                    "agent_id": "a1",
                    "run_id": "run-abc",
                    "metadata": {"source": "test"},
                },
            ],
        })
        exported = Memory.export_memories(source, format="json")
        exported_rows = json.loads(exported)
        assert exported_rows[0]["run_id"] == "run-abc"

        target = Memory.__new__(Memory)
        target.add = MagicMock()
        result = Memory.import_memories(target, exported, format="json")

        assert result == {"success": 1, "failed": 0}
        kwargs = target.add.call_args.kwargs
        assert kwargs["run_id"] == "run-abc"

    def test_csv_export_preserves_and_imports_run_id(self):
        source = Memory.__new__(Memory)
        source.get_all = MagicMock(return_value={
            "results": [
                {
                    "id": 1,
                    "memory": "Session-scoped memory",
                    "user_id": "u1",
                    "agent_id": "a1",
                    "run_id": "run-xyz",
                    "metadata": {"source": "test"},
                },
            ],
        })
        exported = Memory.export_memories(source, format="csv")
        assert "run-xyz" in exported

        target = Memory.__new__(Memory)
        target.add = MagicMock()
        result = Memory.import_memories(target, exported, format="csv")

        assert result == {"success": 1, "failed": 0}
        kwargs = target.add.call_args.kwargs
        assert kwargs["run_id"] == "run-xyz"

    def test_import_json_dict_top_level_rejected(self):
        target = Memory.__new__(Memory)
        target.add = MagicMock()
        with pytest.raises(ValueError):
            Memory.import_memories(target, '{"content": "one"}', format="json")
        target.add.assert_not_called()

    def test_csv_exported_memory_field_imports_as_content(self):
        source = Memory.__new__(Memory)
        source.get_all = MagicMock(return_value={
            "results": [
                {
                    "id": 1,
                    "memory": "Remember the exported CSV body",
                    "user_id": "u1",
                    "agent_id": "a1",
                    "metadata": {"source": "test"},
                },
            ],
        })
        exported = Memory.export_memories(source, format="csv")

        target = Memory.__new__(Memory)
        target.add = MagicMock()
        result = Memory.import_memories(target, exported, format="csv")

        assert result == {"success": 1, "failed": 0}
        target.add.assert_called_once()
        kwargs = target.add.call_args.kwargs
        assert kwargs["messages"] == "Remember the exported CSV body"
        assert kwargs["user_id"] == "u1"
        assert kwargs["agent_id"] == "a1"
        assert kwargs["metadata"] == {"source": "test"}


# ==================== Batch Add ====================

class TestBatchAdd:

    def test_batch_add_json_file(self, runner, mock_memory, tmp_path):
        batch_file = str(tmp_path / "batch.json")
        data = [
            {"content": "memory one", "user_id": "u1"},
            {"content": "memory two"},
            "plain string memory",
        ]
        with open(batch_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, [
                "memory", "add", "--batch", batch_file, "--user-id", "u_default",
            ])
        assert result.exit_code == 0
        assert "batch add complete" in result.output.lower() or "success" in result.output.lower()
        assert mock_memory.add.call_count == 3

    def test_batch_add_invalid_json(self, runner, tmp_path):
        batch_file = str(tmp_path / "bad.json")
        with open(batch_file, "w", encoding="utf-8") as f:
            f.write("not json")

        result = runner.invoke(cli, ["memory", "add", "--batch", batch_file])
        assert result.exit_code != 0
        assert "invalid json" in result.output.lower() or "error" in result.output.lower()

    def test_batch_add_empty_array(self, runner, mock_memory, tmp_path):
        batch_file = str(tmp_path / "empty.json")
        with open(batch_file, "w", encoding="utf-8") as f:
            json.dump([], f)

        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, ["memory", "add", "--batch", batch_file])
        assert result.exit_code == 0
        assert "empty" in result.output.lower() or "warning" in result.output.lower()

    def test_batch_add_not_array(self, runner, tmp_path):
        batch_file = str(tmp_path / "obj.json")
        with open(batch_file, "w", encoding="utf-8") as f:
            json.dump({"content": "single"}, f)

        result = runner.invoke(cli, ["memory", "add", "--batch", batch_file])
        assert result.exit_code != 0
        assert "array" in result.output.lower() or "error" in result.output.lower()

    def test_batch_and_content_mutually_exclusive(self, runner, tmp_path):
        batch_file = str(tmp_path / "batch.json")
        with open(batch_file, "w", encoding="utf-8") as f:
            json.dump(["test"], f)

        result = runner.invoke(cli, [
            "memory", "add", "some content", "--batch", batch_file,
        ])
        assert result.exit_code != 0
        assert "cannot use both" in result.output.lower() or "error" in result.output.lower()

    def test_batch_add_with_failures(self, runner, mock_memory, tmp_path):
        batch_file = str(tmp_path / "batch.json")
        data = [
            {"content": "good memory"},
            {"content": ""},
            42,
        ]
        with open(batch_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, ["memory", "add", "--batch", batch_file])
        assert result.exit_code == 0
        assert "1 succeeded" in result.output or "success" in result.output.lower()
        assert "2 failed" in result.output

    def test_batch_add_json_output_stays_parseable_with_skipped_items(
            self, runner, mock_memory, tmp_path):
        batch_file = str(tmp_path / "batch.json")
        data = [
            {"content": "memory one"},
            {"content": ""},
            42,
        ]
        with open(batch_file, "w", encoding="utf-8") as f:
            json.dump(data, f)

        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, [
                "memory", "add", "--batch", batch_file, "--json",
            ])

        assert result.exit_code == 0
        output = json.loads(result.output)
        assert output == {"success": 1, "failed": 2, "total": 3}
        assert "warning" not in result.output.lower()


# ==================== Quality ====================

class TestQuality:

    def test_quality_table(self, runner, mock_memory):
        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, ["memory", "quality"])
        assert result.exit_code == 0
        assert "Memory Quality" in result.output
        assert "Total Memories" in result.output

    def test_quality_json(self, runner, mock_memory):
        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, ["memory", "quality", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "total_memories" in data
        assert "quality_score" in data

    def test_quality_with_user_filter(self, runner, mock_memory):
        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, ["memory", "quality", "--user-id", "u1"])
        assert result.exit_code == 0
        mock_memory.get_all.assert_called_once_with(
            user_id="u1", agent_id=None, limit=1000,
        )

    def test_quality_error(self, runner, mock_memory):
        mock_memory.get_all.side_effect = RuntimeError("DB error")
        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, ["memory", "quality"])
        assert result.exit_code != 0
        assert "error" in result.output.lower()

    def test_quality_score_non_negative_for_empty_only(self, runner, mock_memory):
        mock_memory.get_all.return_value = {
            "results": [{"id": 1, "memory": "", "metadata": None}],
        }
        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, ["memory", "quality", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["total_memories"] == 1
        assert data["empty_content"] == 1
        assert data["short_content_lt10"] == 0
        assert 0.0 <= data["quality_score"] <= 1.0


# ==================== Optimize ====================

class TestOptimize:

    def test_optimize_deduplicate(self, runner, mock_memory):
        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, ["memory", "optimize"])
        assert result.exit_code == 0
        assert "optimization complete" in result.output.lower() or "success" in result.output.lower()
        mock_memory.optimize.assert_called_once_with(
            strategy="deduplicate",
            user_id=None,
            threshold=0.95,
        )

    def test_optimize_compress(self, runner, mock_memory):
        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, [
                "memory", "optimize", "--strategy", "compress", "--threshold", "0.85",
            ])
        assert result.exit_code == 0
        mock_memory.optimize.assert_called_once_with(
            strategy="compress",
            user_id=None,
            threshold=0.85,
        )

    def test_optimize_with_user(self, runner, mock_memory):
        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, [
                "memory", "optimize", "--user-id", "u1",
            ])
        assert result.exit_code == 0
        mock_memory.optimize.assert_called_once_with(
            strategy="deduplicate",
            user_id="u1",
            threshold=0.95,
        )

    def test_optimize_json(self, runner, mock_memory):
        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, ["memory", "optimize", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "duplicates_found" in data

    def test_optimize_error(self, runner, mock_memory):
        mock_memory.optimize.side_effect = ValueError("Unknown strategy")
        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, ["memory", "optimize"])
        assert result.exit_code != 0
        assert "error" in result.output.lower()

    def test_optimize_invalid_strategy(self, runner, mock_memory):
        result = runner.invoke(cli, ["memory", "optimize", "--strategy", "invalid"])
        assert result.exit_code != 0


# ==================== Interactive shell ====================

class TestInteractiveExportRunId:
    """Regression for: interactive shell export ignored --run-id."""

    def _make_session(self, mock_memory):
        ctx = MagicMock()
        ctx.memory = mock_memory
        ctx.default_user_id = None
        ctx.default_agent_id = None
        return InteractiveSession(ctx)

    def test_export_forwards_long_run_id(self, mock_memory):
        session = self._make_session(mock_memory)
        session._cmd_export(["--run-id", "run-1"])
        mock_memory.export_memories.assert_called_once_with(
            format="json",
            user_id=None,
            agent_id=None,
            run_id="run-1",
            limit=1000,
        )

    def test_export_forwards_short_run_id(self, mock_memory):
        session = self._make_session(mock_memory)
        session._cmd_export(["-r", "run-2"])
        mock_memory.export_memories.assert_called_once_with(
            format="json",
            user_id=None,
            agent_id=None,
            run_id="run-2",
            limit=1000,
        )

    def test_export_without_run_id_passes_none(self, mock_memory):
        session = self._make_session(mock_memory)
        session._cmd_export([])
        mock_memory.export_memories.assert_called_once_with(
            format="json",
            user_id=None,
            agent_id=None,
            run_id=None,
            limit=1000,
        )


class TestInteractiveAddSearchListRunId:
    """Regression for: interactive shell add/search/list dropped --run-id."""

    def _make_session(self, mock_memory):
        ctx = MagicMock()
        ctx.memory = mock_memory
        ctx.default_user_id = None
        ctx.default_agent_id = None
        return InteractiveSession(ctx)

    def test_add_forwards_long_run_id(self, mock_memory):
        session = self._make_session(mock_memory)
        session._cmd_add(["hello", "--run-id", "run-1"])
        kwargs = mock_memory.add.call_args.kwargs
        assert kwargs["run_id"] == "run-1"
        assert kwargs["messages"] == "hello"

    def test_add_forwards_short_run_id(self, mock_memory):
        session = self._make_session(mock_memory)
        session._cmd_add(["hello", "-r", "run-2"])
        kwargs = mock_memory.add.call_args.kwargs
        assert kwargs["run_id"] == "run-2"

    def test_add_without_run_id_passes_none(self, mock_memory):
        session = self._make_session(mock_memory)
        session._cmd_add(["hello"])
        kwargs = mock_memory.add.call_args.kwargs
        assert kwargs["run_id"] is None

    def test_search_forwards_run_id(self, mock_memory):
        session = self._make_session(mock_memory)
        mock_memory.search.return_value = {"results": []}
        session._cmd_search(["query", "--run-id", "run-3"])
        kwargs = mock_memory.search.call_args.kwargs
        assert kwargs["run_id"] == "run-3"

    def test_search_without_run_id_passes_none(self, mock_memory):
        session = self._make_session(mock_memory)
        mock_memory.search.return_value = {"results": []}
        session._cmd_search(["query"])
        kwargs = mock_memory.search.call_args.kwargs
        assert kwargs["run_id"] is None

    def test_list_forwards_run_id(self, mock_memory):
        session = self._make_session(mock_memory)
        mock_memory.get_all.return_value = {"results": []}
        session._cmd_list(["--run-id", "run-4"])
        kwargs = mock_memory.get_all.call_args.kwargs
        assert kwargs["run_id"] == "run-4"

    def test_list_without_run_id_passes_none(self, mock_memory):
        session = self._make_session(mock_memory)
        mock_memory.get_all.return_value = {"results": []}
        session._cmd_list([])
        kwargs = mock_memory.get_all.call_args.kwargs
        assert kwargs["run_id"] is None


class TestMetadataFiltersShapeValidation:
    """Regression for: CLI --metadata/--filters accepted any JSON shape."""

    def test_add_rejects_metadata_array(self, runner, mock_memory, tmp_path):
        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, [
                "memory", "add", "content", "--metadata", "[]",
            ])
        assert result.exit_code != 0
        assert "expected JSON object" in result.output
        mock_memory.add.assert_not_called()

    def test_add_rejects_metadata_string(self, runner, mock_memory, tmp_path):
        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, [
                "memory", "add", "content", "--metadata", '"oops"',
            ])
        assert result.exit_code != 0
        assert "expected JSON object" in result.output

    def test_add_accepts_metadata_dict(self, runner, mock_memory, tmp_path):
        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, [
                "memory", "add", "content", "--metadata", '{"k": "v"}',
            ])
        assert result.exit_code == 0
        kwargs = mock_memory.add.call_args.kwargs
        assert kwargs["metadata"] == {"k": "v"}

    def test_search_rejects_filters_array(self, runner, mock_memory, tmp_path):
        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, [
                "memory", "search", "q", "--filters", "[]",
            ])
        assert result.exit_code != 0
        assert "expected JSON object" in result.output

    def test_list_rejects_filters_array(self, runner, mock_memory, tmp_path):
        with patch("powermem.cli.commands.memory.CLIContext.memory",
                   new_callable=PropertyMock, return_value=mock_memory):
            result = runner.invoke(cli, [
                "memory", "list", "--filters", "[]",
            ])
        assert result.exit_code != 0
        assert "expected JSON object" in result.output
