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

"""Complete released Server 1.3.0, no-vector profile."""

import json
from pathlib import Path

from alembic import context, op
from sqlalchemy import text

revision = "s1300"
down_revision = None


def upgrade():
    connection = op.get_bind()
    resource = json.loads((Path(__file__).parents[1] / "schemas/sqlite.json").read_text())
    for statement in resource["initialize"]:
        connection.exec_driver_sql(statement)
    manifest = dict(context.config.attributes["configuration"]["processing_manifest"])
    manifest["bindings"] = {
        key: value for key, value in manifest["bindings"].items() if value not in {"handoff", "prompt"}
    }
    manifest["capabilities"] = [value for value in manifest["capabilities"] if value not in {"handoff", "prompt"}]
    connection.execute(
        text(
            "INSERT INTO pc_artifact_processing_schema (singleton,schema_version,source_version,phase,migration_id,config_manifest) VALUES (1,1515,1515,'complete','fresh',:manifest)"
        ),
        {"manifest": json.dumps(manifest, sort_keys=True, separators=(",", ":"))},
    )
