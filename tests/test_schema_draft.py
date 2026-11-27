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

"""Exercise the actual cumulative Server draft on the registered source paths."""

import pytest
from sqlalchemy import create_engine

from tests.builtin.persistence.test_release140_server_migrations import _source


@pytest.mark.parametrize("release", ["1.3.0", "1.4.0"])
@pytest.mark.parametrize("vectors", [False, True])
def test_registered_source_replays_complete_cumulative_draft(tmp_path, release, vectors):
    _settings, _embedding, runner = _source(tmp_path, release=release, vectors=vectors)
    engine = create_engine(_settings.database.url.replace("sqlite+aiosqlite:", "sqlite:"))
    with engine.connect() as connection:
        retained_runs = connection.exec_driver_sql("SELECT scope_id, run_id, payload FROM pc_dream_runs").all()
    plan = runner.plan(backup_policy="skip")
    result = runner.apply(
        plan_id=plan.plan_id,
        backup_policy="skip",
        accepted=True,
        maintenance_confirmed=True,
        accept_no_backup=True,
    )
    assert result.revision == plan.target_revision
    assert runner.verify().state == "ready"
    assert not runner.apply().changed
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT scope_id, run_id, payload FROM pc_dream_runs").all() == retained_runs
        assert [row[2] for row in connection.exec_driver_sql("PRAGMA index_info(ix_pc_dream_operation_list)")] == [
            "scope_id",
            "operation",
            "accepted_at",
            "run_id",
        ]
    engine.dispose()
