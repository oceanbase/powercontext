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

"""Bridge released Server 1.4.0 to the canonical shared schema checkpoint."""

import json
from pathlib import Path

from alembic import op
from sqlalchemy import inspect

revision = "s1400"
down_revision = "r1400"


def upgrade():
    connection = op.get_bind()
    root = Path(__file__).parents[1]
    target = json.loads((root / "schemas/sqlite.json").read_text())["target"]
    # The source already has the current Candidate envelope. Copy every source
    # column; only the new nullable Topic attempt fields remain unknown.
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
        temporary = "pc_release1400_" + name
        connection.exec_driver_sql(ddl.replace("CREATE TABLE " + name, "CREATE TABLE " + temporary, 1))
        connection.exec_driver_sql(f"INSERT INTO {temporary} ({fields}) SELECT {fields} FROM {name}")  # noqa: S608 -- Frozen table and inspected column identifiers.
        connection.exec_driver_sql(f"DROP TABLE {name}")
        connection.exec_driver_sql(f"ALTER TABLE {temporary} RENAME TO {name}")
    for kind, name, _owner, ddl in target:
        if kind == "table" and name == "pc_topic_memory_work_blocks":
            connection.exec_driver_sql(ddl)
    for kind, _name, owner, ddl in target:
        if kind == "index" and owner in rebuild | {"pc_topic_memory_work_blocks"}:
            connection.exec_driver_sql(ddl)
    # Keep the shared schema shape without inventing 1.3 Candidate history.
    # The released 1.4 source has no old Candidate envelopes to archive.
    dream = json.loads((root / "dream/schema.json").read_text())
    for entry in dream["tables"].values():
        connection.exec_driver_sql(entry["retained"]["sqlite"])
    connection.exec_driver_sql(
        "UPDATE pc_artifact_processing_leases SET supervisor_generation=supervisor_generation+1, holder_id='migration-s1400', lease_expires_at='1970-01-01 00:00:00.000000' WHERE holder_id <> 'migration-s1400' OR lease_expires_at IS NULL OR lease_expires_at <> '1970-01-01 00:00:00.000000'"
    )
