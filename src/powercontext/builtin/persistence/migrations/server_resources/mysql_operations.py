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

"""Frozen dynamic identifiers for Server initialization and Candidate conversion."""

import copy
import json

from sqlalchemy import inspect, text

from powercontext.builtin.persistence.migrations.models import MigrationError


def quote(identifier):
    return "`" + identifier.replace("`", "``") + "`"


def execute(connection, statement, configuration):
    if statement == "@initialize_processing":
        manifest = copy.deepcopy(configuration["processing_manifest"])
        manifest["bindings"] = {
            key: value for key, value in manifest["bindings"].items() if value not in {"handoff", "prompt"}
        }
        manifest["capabilities"] = [value for value in manifest["capabilities"] if value not in {"handoff", "prompt"}]
        connection.execute(
            text(
                "INSERT INTO pc_artifact_processing_schema (singleton,schema_version,source_version,phase,migration_id,config_manifest) SELECT 1,1515,1515,'complete','fresh',:manifest WHERE NOT EXISTS (SELECT 1 FROM pc_artifact_processing_schema)"
            ),
            {"manifest": json.dumps(manifest, sort_keys=True, separators=(",", ":"))},
        )
    elif statement == "@drop_candidate_head_version_fk":
        matches = [
            key
            for key in inspect(connection).get_foreign_keys("pc_candidate_heads")
            if key["constrained_columns"] == ["scope_id", "candidate_id", "version"]
            and key["referred_table"] == "pc_candidate_versions"
        ]
        if len(matches) != 1 or not matches[0]["name"]:
            raise MigrationError("incompatible_schema", "Candidate foreign-key identity is ambiguous.")
        connection.exec_driver_sql("ALTER TABLE pc_candidate_heads DROP FOREIGN KEY " + quote(matches[0]["name"]))
    else:
        connection.exec_driver_sql(statement, execution_options={"no_parameters": True})
