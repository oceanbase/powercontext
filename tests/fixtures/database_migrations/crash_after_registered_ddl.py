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

"""Interrupt a migration client or its engine after registered DDL completes."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

from pydantic import SecretStr
from sqlalchemy import Engine, event

from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from tests.builtin.persistence.test_mysql_migration_runner import _BackendTarget, _full_server_runner


def main() -> None:
    evidence = Path(sys.argv[1])
    backend = _BackendTarget(
        OceanBaseConfig(url=SecretStr(os.environ["POWERCONTEXT_MIGRATION_CRASH_TARGET"])), evidence
    )

    @event.listens_for(Engine, "after_cursor_execute")
    def exit_after_ddl(connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().startswith("CREATE TABLE pc_retained_dream_candidate_versions"):
            connection_id = connection.exec_driver_sql("SELECT CONNECTION_ID()").scalar_one()
            wait_for_engine = os.environ.get("POWERCONTEXT_MIGRATION_WAIT_FOR_ENGINE_CRASH") == "1"
            marker_name = "engine-crash-ready.json" if wait_for_engine else "client-exit.json"
            with (evidence / marker_name).open("w", encoding="utf-8") as marker:
                json.dump({"connection_id": connection_id}, marker)
                marker.flush()
                os.fsync(marker.fileno())
            if not wait_for_engine:
                # Bypass cleanup and leave the remote interruption unacknowledged.
                os._exit(73)
            deadline = time.monotonic() + 180
            while not (evidence / "engine-stopped").exists():
                if time.monotonic() >= deadline:
                    raise TimeoutError("The parent did not confirm engine termination")  # noqa: TRY003
                time.sleep(0.05)
            # Require a real connection failure after the parent kills observer.
            connection.exec_driver_sql("SELECT 1")
            raise AssertionError("The migration connection survived engine termination")  # noqa: TRY003

    runner = _full_server_runner(backend)
    plan = runner.plan(backup_policy="manual")
    runner.apply(
        plan_id=plan.plan_id,
        accepted=True,
        maintenance_confirmed=True,
        backup_policy="manual",
        backup_confirmed=True,
        backup_ref="operator://manual-backup",
    )
    raise AssertionError("The registered DDL exit point was not reached")  # noqa: TRY003


if __name__ == "__main__":
    main()
