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

import sqlite3

from typer.testing import CliRunner

from powercontext.cli.app import create_cli
from powercontext.server.cli import app as server_app


def test_server_migrate_runs_the_packaged_forward_only_chain(tmp_path) -> None:
    database = tmp_path / "runtime.db"
    environment = tmp_path / "server.env"
    environment.write_text(
        "\n".join((
            "POWERCONTEXT_SERVER_DATABASE_KIND=sqlite",
            f"POWERCONTEXT_SERVER_DATABASE_URL=sqlite+aiosqlite:///{database}",
        )),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        create_cli((server_app,)),
        ["server", "migrate", "--env-file", str(environment)],
    )

    assert result.exit_code == 0, result.output
    assert "0003_scope_source_skill" in result.output
    with sqlite3.connect(database) as connection:
        revision = connection.execute("SELECT version_num FROM pc_schema_revisions").fetchone()
        work_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'pc_work_items'"
        ).fetchone()
    assert revision == ("0003_scope_source_skill",)
    assert work_table == ("pc_work_items",)
