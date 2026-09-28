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

"""Add Dream provenance and the recurrence ledger from the current runtime."""

from __future__ import annotations

from alembic import op
from sqlalchemy import Column, inspect

from powercontext.builtin.persistence.tables import (
    ARTIFACT_CANDIDATE_VERSIONS_TABLE,
    ARTIFACTS_TABLE,
    DREAM_RUNS_TABLE,
    RECURRENCE_TABLES,
    SHARED_METADATA,
)

revision = "0008_dream_recurrence"
down_revision = "0007_processing_supervisor"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    SHARED_METADATA.create_all(bind, tables=(*RECURRENCE_TABLES, DREAM_RUNS_TABLE), checkfirst=True)
    for table in (ARTIFACTS_TABLE, ARTIFACT_CANDIDATE_VERSIONS_TABLE):
        columns = {str(column["name"]) for column in inspect(bind).get_columns(table.name)}
        if "memory_citations" not in columns:
            op.add_column(table.name, Column("memory_citations", table.c.memory_citations.type, nullable=True))


def downgrade() -> None:
    raise NotImplementedError("PowerContext schema migrations are forward-only")
