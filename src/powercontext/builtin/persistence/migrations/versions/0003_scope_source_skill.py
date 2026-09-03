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

"""Add Scope, Source Definition, publication, and Skill distribution state."""

from __future__ import annotations

from alembic import op
from sqlalchemy import BigInteger, Column, inspect

from powercontext.builtin.persistence.tables import (
    AGENT_SKILL_TARGETS_TABLE,
    ARTIFACT_PUBLICATIONS_TABLE,
    CONNECTOR_CHECKPOINTS_TABLE,
    SCOPE_TABLES,
    SHARED_METADATA,
    SKILL_PACKAGES_TABLE,
    SKILL_PUBLICATIONS_TABLE,
    SOURCE_DEFINITION_MANIFESTS_TABLE,
    identity_string,
)
from powercontext.limits import MAX_ARTIFACT_ID_LENGTH

revision = "0003_scope_source_skill"
down_revision = "0002_work_ledger"
branch_labels = None
depends_on = None

_NEW_TABLES = (
    *SCOPE_TABLES,
    ARTIFACT_PUBLICATIONS_TABLE,
    CONNECTOR_CHECKPOINTS_TABLE,
    SOURCE_DEFINITION_MANIFESTS_TABLE,
    SKILL_PACKAGES_TABLE,
    AGENT_SKILL_TARGETS_TABLE,
    SKILL_PUBLICATIONS_TABLE,
)


def upgrade() -> None:
    bind = op.get_bind()
    SHARED_METADATA.create_all(bind, tables=_NEW_TABLES, checkfirst=True)

    columns = {str(column["name"]) for column in inspect(bind).get_columns("pc_artifact_heads")}
    if "lifecycle_state" not in columns:
        op.add_column(
            "pc_artifact_heads",
            Column("lifecycle_state", identity_string(16), nullable=False, server_default="active"),
        )
    if "replacement_artifact_id" not in columns:
        op.add_column(
            "pc_artifact_heads",
            Column("replacement_artifact_id", identity_string(MAX_ARTIFACT_ID_LENGTH)),
        )
    if "governance_generation" not in columns:
        op.add_column(
            "pc_artifact_heads",
            Column("governance_generation", BigInteger, nullable=False, server_default="0"),
        )


def downgrade() -> None:
    raise NotImplementedError("PowerContext schema migrations are forward-only")
