# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from powercontext.cli.config_wizard_document import read_sqlite_summary, update_document
from powercontext.cli.env_file import EnvironmentFileError, parse_environment


def test_update_preserves_managed_and_unmanaged_unknown_assignments_and_comments() -> None:
    original = (
        "# My deployment\nOUTSIDE='literal # value'\n"
        "# >>> powercontext managed configuration >>>\n"
        "# config-version=1\n# credentials=MYTOKEN\n# generation-environment=MYTOKEN\n"
        "# Keep my timeout explanation\n  export TIMEOUT=30  # seconds\n"
        "UNKNOWN='[「project」,\n # not metadata\n TIMEOUT=999\n]'\nMYTOKEN=private\n"
        "# <<< powercontext managed configuration <<<\nTAIL=value\n"
    )

    updated = update_document(original, {"TIMEOUT": "120", "NEW": "yes"}, language="zh")

    assert updated == original.replace("TIMEOUT=30", "TIMEOUT=120").replace(
        "# <<< powercontext", "NEW=yes\n# powercontext-wizard-language=zh\n# <<< powercontext"
    )
    assert parse_environment(updated)["MYTOKEN"] == "private"


def test_update_does_not_interpret_metadata_or_assignments_inside_multiquoted_values() -> None:
    original = "VALUE=\"first\n\"'second\n# powercontext-wizard-language=zh\nOTHER=inside\n'\nOTHER=real\n"

    updated = update_document(original, {"OTHER": "new"}, language="en")

    assert updated.startswith(original.replace("OTHER=real", "OTHER=new"))
    assert parse_environment(updated)["VALUE"] == parse_environment(original)["VALUE"]


def test_deleted_assignment_retains_its_comment_and_other_credentials() -> None:
    original = "REMOVE='line one\nline two'  # retired setting\nKEEP=secret\n"

    updated = update_document(original, {"REMOVE": None, "ABSENT": None}, language="en")

    assert updated.startswith("# retired setting\nKEEP=secret\n")
    assert parse_environment(updated) == {"KEEP": "secret"}


def test_new_values_are_literal_and_support_multiline_text(tmp_path: Path) -> None:
    sentinel = tmp_path / "must-not-exist"
    secret = f"'$(touch {sentinel}) `touch {sentinel}` $HOME\n# literal"

    updated = update_document("", {"TOKEN": secret}, language="en")

    assert parse_environment(updated) == {"TOKEN": secret}
    assert "# config-version=1" in updated
    assert not sentinel.exists()


def test_update_preserves_crlf_and_replaces_language_metadata() -> None:
    original = "# powercontext-wizard-language=en\r\nVALUE=old\r\n"

    updated = update_document(original, {"VALUE": "new"}, language="zh")

    assert updated == "# powercontext-wizard-language=zh\r\nVALUE=new\r\n"


def test_update_handles_existing_assignment_after_managed_block() -> None:
    original = (
        "# >>> powercontext managed configuration >>>\nKEY=inside\n"
        "# <<< powercontext managed configuration <<<\nOUTSIDE=old\n"
    )

    updated = update_document(original, {"OUTSIDE": "new", "ADDED": "one"}, language="en")

    assert updated.endswith("OUTSIDE=new\n")
    assert parse_environment(updated) == {"KEY": "inside", "OUTSIDE": "new", "ADDED": "one"}


def test_update_retains_unchanged_assignment_spelling() -> None:
    original = "export KEY='same value' # keep\n"

    assert update_document(original, {"KEY": "same value"}, language="en").startswith(original)


def test_update_retains_missing_final_newline_if_nothing_is_appended() -> None:
    original = "# powercontext-wizard-language=en\nKEY=unchanged"

    assert update_document(original, {}, language="en") == original


@pytest.mark.parametrize(
    "content",
    [
        "KEY=one\nKEY=two\n",
        "KEY=$HOME\n",
        "# >>> powercontext managed configuration >>>\n",
        "# config-version=2\nKEY=value\n",
    ],
)
def test_invalid_existing_documents_are_rejected(content: str) -> None:
    with pytest.raises(EnvironmentFileError):
        update_document(content, {"KEY": "new"}, language="en")


def test_missing_sqlite_probe_creates_no_file_or_parent(tmp_path: Path) -> None:
    path = tmp_path / "missing-parent" / "powercontext.db"

    assert read_sqlite_summary(path) == {"status": "new"}
    assert not path.parent.exists()


def test_sqlite_preview_reads_only_metadata_and_does_not_modify_database(tmp_path: Path) -> None:
    path = tmp_path / "existing.db"
    manifest = {
        "mode": "global",
        "capabilities": ["topic-memory"],
        "bindings": {"topic-memory-source-window": "topic-memory"},
        "legacy_automatic_bindings": ["topic-memory-source-window"],
        "api_key": "must-not-be-returned",
    }
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE pc_scopes (scope_id TEXT, title TEXT)")
        connection.execute("INSERT INTO pc_scopes VALUES ('private-scope', 'private-content')")
        connection.execute("CREATE TABLE pc_artifact_processing_schema (config_manifest TEXT)")
        connection.execute("INSERT INTO pc_artifact_processing_schema VALUES (?)", (json.dumps(manifest),))
        connection.execute("CREATE TABLE pc_topic_memory_retrieval_shape (shape TEXT)")
        connection.execute("INSERT INTO pc_topic_memory_retrieval_shape VALUES ('hybrid')")
    before = path.read_bytes()

    summary = read_sqlite_summary(path)

    assert summary == {
        "status": "existing",
        "table_count": 3,
        "scope_count": 1,
        "processing_manifest": {name: value for name, value in manifest.items() if name != "api_key"},
        "topic_retrieval_shape": "hybrid",
    }
    assert path.read_bytes() == before
    assert "private" not in json.dumps(summary)
    assert "must-not-be-returned" not in json.dumps(summary)


def test_sqlite_preview_reports_foreign_database_without_modifying_it(tmp_path: Path) -> None:
    path = tmp_path / "foreign.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE private_data (content TEXT)")
    before = path.read_bytes()

    assert read_sqlite_summary(path) == {"status": "unrecognized", "table_count": 1}
    assert path.read_bytes() == before


def test_sqlite_preview_reports_bad_file_without_exposing_contents(tmp_path: Path) -> None:
    path = tmp_path / "broken.db"
    path.write_text("not-sqlite-private-secret", encoding="utf-8")

    assert read_sqlite_summary(path) == {"status": "unavailable", "reason": "database_unreadable"}
    assert read_sqlite_summary(tmp_path) == {"status": "unavailable", "reason": "not_regular_file"}


def test_sqlite_preview_handles_exclusively_locked_database(tmp_path: Path) -> None:
    path = tmp_path / "locked.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE pc_scopes (scope_id TEXT)")
        connection.execute("BEGIN EXCLUSIVE")

        assert read_sqlite_summary(path) == {"status": "unavailable", "reason": "database_busy"}
