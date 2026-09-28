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

"""Add Artifact Processing Supervisor and Scope discovery state."""

from __future__ import annotations

from alembic import op
from sqlalchemy import BigInteger, Boolean, Column, DateTime, Text, inspect, text
from sqlalchemy.dialects.mysql import MEDIUMTEXT

from powercontext.builtin.persistence.tables import (
    ARTIFACT_PROCESSING_INTENTS_TABLE,
    ARTIFACT_PROCESSING_MIGRATION_RECEIPTS_TABLE,
    ARTIFACT_PROCESSING_SCHEMA_TABLE,
    ARTIFACT_PROCESSING_SEQUENCES_TABLE,
    RECEIPT_MIGRATION_REVIEW_TABLE,
    SHARED_METADATA,
    TOPIC_MEMORY_PROCESSING_TARGETS_TABLE,
)

revision = "0007_processing_supervisor"
down_revision = "0006_topic_memory_budget"
branch_labels = None
depends_on = None

_NEW_TABLES = (
    ARTIFACT_PROCESSING_SEQUENCES_TABLE,
    ARTIFACT_PROCESSING_INTENTS_TABLE,
    TOPIC_MEMORY_PROCESSING_TARGETS_TABLE,
    ARTIFACT_PROCESSING_SCHEMA_TABLE,
    ARTIFACT_PROCESSING_MIGRATION_RECEIPTS_TABLE,
    RECEIPT_MIGRATION_REVIEW_TABLE,
)


def upgrade() -> None:
    bind = op.get_bind()
    SHARED_METADATA.create_all(bind, tables=_NEW_TABLES, checkfirst=True)
    _add_processing_scan_columns(bind)
    _add_scope_search_columns(bind)


def _add_processing_scan_columns(bind) -> None:
    table_name = "pc_artifact_processing_binding_states"
    columns = {str(column["name"]) for column in inspect(bind).get_columns(table_name)}
    additions = (
        Column("last_schedule_checkpoint_at", DateTime(timezone=False)),
        Column("scan_generation", BigInteger, nullable=False, server_default="0"),
        Column("scan_in_progress", Boolean, nullable=False, server_default="0"),
        Column("scan_upper_pending_sequence", BigInteger),
    )
    for column in additions:
        if column.name not in columns:
            op.add_column(table_name, column)


def _add_scope_search_columns(bind) -> None:
    search_type = Text().with_variant(MEDIUMTEXT(), "mysql")
    mysql = bind.dialect.name == "mysql"
    additions = {
        "pc_scopes": ("scope_id_search", "title_search", "summary_search"),
        "pc_scope_external_references": ("value_search",),
        "pc_scope_bindings": ("external_id_search",),
    }
    added: list[tuple[str, str]] = []
    for table_name, names in additions.items():
        columns = {str(column["name"]) for column in inspect(bind).get_columns(table_name)}
        for name in names:
            if name in columns:
                continue
            op.add_column(
                table_name,
                Column(name, search_type, nullable=mysql, server_default=None if mysql else ""),
            )
            added.append((table_name, name))

    bind.execute(
        text(
            "UPDATE pc_scopes SET "
            "scope_id_search = scope_id, title_search = title, summary_search = summary "
            "WHERE scope_id_search IS NULL OR scope_id_search = '' "
            "OR title_search IS NULL OR title_search = '' "
            "OR summary_search IS NULL OR summary_search = ''"
        )
    )
    bind.execute(
        text(
            "UPDATE pc_scope_external_references SET value_search = value "
            "WHERE value_search IS NULL OR value_search = ''"
        )
    )
    bind.execute(
        text(
            "UPDATE pc_scope_bindings SET external_id_search = external_id "
            "WHERE external_id_search IS NULL OR external_id_search = ''"
        )
    )
    if mysql:
        for table_name, name in added:
            op.alter_column(table_name, name, existing_type=MEDIUMTEXT(), nullable=False, server_default=None)


def downgrade() -> None:
    raise NotImplementedError("PowerContext schema migrations are forward-only")
