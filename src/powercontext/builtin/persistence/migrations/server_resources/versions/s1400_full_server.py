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

"""Complete 1.3.0 -> 1.4 Server storage transition on the pinned transaction."""

import json
import runpy
from pathlib import Path

from alembic import context, op
from sqlalchemy import inspect

revision = "s1400"
down_revision = "s1300"


def upgrade():
    connection = op.get_bind()
    root = Path(__file__).parents[1]
    dream = runpy.run_path(str(root / "dream/upgrade.py"))
    dream["upgrade_dream_storage"](
        connection, target_manifest=context.config.attributes["configuration"]["processing_manifest"]
    )
    target = json.loads((root / "schemas/sqlite.json").read_text())["target"]
    # The canonical rebuild fixes constraint names and column order as well as
    # additions. Explicit column lists preserve every existing value; new nullable
    # provenance stays unknown, never synthesized from migration time.
    rebuild = {
        "pc_candidate_heads",
        "pc_candidate_versions",
        "pc_profile_policies",
        "pc_artifacts",
        "pc_artifact_processing_intents",
        "pc_dream_runs",
        "pc_topic_memory_work_budgets",
    }
    for kind, name, _owner, ddl in target:
        if kind != "table" or name not in rebuild:
            continue
        columns = [item["name"] for item in inspect(connection).get_columns(name)]
        fields = ",".join('"' + item + '"' for item in columns)
        temporary = "pc_full_upgrade_" + name
        connection.exec_driver_sql(ddl.replace("CREATE TABLE " + name, "CREATE TABLE " + temporary, 1))
        connection.exec_driver_sql(f"INSERT INTO {temporary} ({fields}) SELECT {fields} FROM {name}")  # noqa: S608 -- frozen identifiers
        connection.exec_driver_sql(f"DROP TABLE {name}")
        connection.exec_driver_sql(f"ALTER TABLE {temporary} RENAME TO {name}")
    added = {
        "pc_artifact_handles",
        "pc_context_bootstrap_receipts",
        "pc_lexical_projection_versions",
        "pc_topic_memory_work_blocks",
    }
    for kind, name, _owner, ddl in target:
        if kind == "table" and name in added:
            connection.exec_driver_sql(ddl)
    for kind, _name, owner, ddl in target:
        if kind == "index" and owner in rebuild | added:
            connection.exec_driver_sql(ddl)
    # Revoke the old fence and leave an expired term for timed-lease takeover.
    # NULL denotes a non-expiring single-process term and blocks OceanBase.
    connection.exec_driver_sql(
        "UPDATE pc_artifact_processing_leases SET supervisor_generation=supervisor_generation+1, holder_id='migration-s1400', lease_expires_at='1970-01-01 00:00:00.000000'"
    )
