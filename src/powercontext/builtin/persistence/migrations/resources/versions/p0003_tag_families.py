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

"""Remove the family whitelist while preserving the target constraint and indexes."""

from alembic import op

revision = "p0003"
down_revision = "p0002"


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "sqlite":
        # Keep a frozen copy of the replaced layout and rows. Cleanup belongs
        # to a later, independently reviewed maintenance revision.
        original = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_schema WHERE type='table' AND name='pc_artifact_tags'"
        ).scalar_one()
        retained = original.replace("CREATE TABLE pc_artifact_tags", "CREATE TABLE pc_retained_p0002_artifact_tags", 1)
        connection.exec_driver_sql(retained)
        connection.exec_driver_sql("INSERT INTO pc_retained_p0002_artifact_tags SELECT * FROM pc_artifact_tags")
        # Alembic reflects indexes and named CHECKs, but not application triggers.
        triggers = (
            connection
            .exec_driver_sql(
                "SELECT sql FROM sqlite_schema WHERE type='trigger' AND tbl_name='pc_artifact_tags' ORDER BY name"
            )
            .scalars()
            .all()
        )
        with op.batch_alter_table("pc_artifact_tags", recreate="always") as batch:
            batch.drop_constraint("ck_pc_artifact_tags_family", type_="check")
        for statement in triggers:
            connection.exec_driver_sql(statement)
    else:
        op.drop_constraint("ck_pc_artifact_tags_family", "pc_artifact_tags", type_="check")
