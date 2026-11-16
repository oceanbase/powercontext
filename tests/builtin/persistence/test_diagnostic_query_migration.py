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

"""Append the nullable query column without rewriting historical diagnostics."""

import sqlite3
from contextlib import closing

from powercontext.builtin.persistence.migrations.deployment import server_bundle
from powercontext.builtin.persistence.migrations.sqlite import SQLiteMigrationRunner
from tests.builtin.persistence.test_portable_storage_migration import apply, configuration, previous_bundle


def test_query_column_upgrade_preserves_005_receipts_and_keeps_original_text_unknown(tmp_path):
    previous = previous_bundle(tmp_path / "old", None, through="v1_5_0_005")
    config = configuration(None)
    database = tmp_path / "diagnostics.db"
    old = SQLiteMigrationRunner(database, previous, configuration=config)
    assert apply(old).revision == "v1_5_0_005"
    body = "历史上下文保留 😀\n"
    receipt = b'{"historical":"receipt"}'
    evidence = b'{"historical":"stages"}'
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute(
            "INSERT INTO pc_context_prepare_diagnostics "
            "(record_id,scope_id,started_at,finished_at,duration_ms,status,query_digest,requested_max_bytes,"
            "prepared_content,receipt_payload,diagnostics_payload) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "legacy",
                "scope",
                "2026-11-01",
                "2026-11-01",
                1.0,
                "ready",
                "sha256:" + "0" * 64,
                8000,
                body,
                receipt,
                evidence,
            ),
        )
        assert "query_text" not in {
            row[1] for row in connection.execute("PRAGMA table_info(pc_context_prepare_diagnostics)")
        }
    current = server_bundle()
    assert all(current.checksums[key] == value for key, value in previous.checksums.items())
    upgraded = SQLiteMigrationRunner(database, current, configuration=config)
    plan = upgraded.plan()
    assert plan.revisions == ("v1_5_0_006",)
    assert apply(upgraded).revision == "v1_5_0_006"
    assert upgraded.verify().state == "ready" and not apply(upgraded).changed
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute(
            "SELECT query_text,prepared_content,receipt_payload,diagnostics_payload FROM pc_context_prepare_diagnostics WHERE record_id='legacy'"
        ).fetchone() == (None, body, receipt, evidence)
