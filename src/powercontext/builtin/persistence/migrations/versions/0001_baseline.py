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

"""Create the validated PowerContext baseline schema."""

from __future__ import annotations

from collections import defaultdict

from alembic import op
from sqlalchemy import MetaData, Table

from powercontext.builtin.persistence.migration import baseline_tables

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    grouped: dict[MetaData, list[Table]] = defaultdict(list)
    for table in baseline_tables():
        grouped[table.metadata].append(table)
    for metadata, tables in grouped.items():
        metadata.create_all(bind, tables=tables, checkfirst=True)


def downgrade() -> None:
    raise NotImplementedError("PowerContext schema migrations are forward-only")
