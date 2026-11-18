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

"""Create the complete diagnostic store in one additive revision."""

import sqlite3
from contextlib import closing

from powercontext.builtin.persistence.migrations.deployment import server_bundle
from powercontext.builtin.persistence.migrations.sqlite import SQLiteMigrationRunner
from tests.builtin.persistence.test_portable_storage_migration import apply, configuration, previous_bundle


def test_diagnostic_upgrade_creates_query_column_and_preserves_published_data(tmp_path):
    previous = previous_bundle(tmp_path / "old", None, through="v1_5_0_005")
    config = configuration(None)
    database = tmp_path / "diagnostics.db"
    old = SQLiteMigrationRunner(database, previous, configuration=config)
    assert apply(old).revision == "v1_5_0_005"
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "INSERT INTO pc_scopes (scope_id,title,summary,scope_id_search,title_search,summary_search,version) "
            "VALUES ('scope','preserved','summary','scope','preserved','summary',1)"
        )
        assert not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pc_context_prepare_diagnostics'"
        ).fetchall()
    current = server_bundle()
    assert all(current.checksums[key] == value for key, value in previous.checksums.items())
    upgraded = SQLiteMigrationRunner(database, current, configuration=config)
    assert upgraded.plan().revisions == ("v1_5_2_pr2639", "v1_5_0_007")
    assert apply(upgraded).revision == "v1_5_0_007"
    assert upgraded.verify().state == "ready" and not apply(upgraded).changed
    query = "查询原文保留 😀\n"
    with closing(sqlite3.connect(database)) as connection, connection:
        assert connection.execute("SELECT title,summary,version FROM pc_scopes WHERE scope_id='scope'").fetchone() == (
            "preserved",
            "summary",
            1,
        )
        connection.execute(
            "INSERT INTO pc_context_prepare_diagnostics "
            "(record_id,scope_id,started_at,finished_at,duration_ms,status,query_digest,requested_max_bytes,"
            "diagnostics_payload,query_text) VALUES (?,?,?,?,?,?,?,?,?,?)",
            ("new", "scope", "2026-11-01", "2026-11-01", 1.0, "empty", "sha256:" + "0" * 64, 8000, b"{}", query),
        )
        assert connection.execute(
            "SELECT query_text FROM pc_context_prepare_diagnostics WHERE record_id='new'"
        ).fetchone() == (query,)
