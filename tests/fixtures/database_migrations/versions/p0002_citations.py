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

"""Add immutable revision citations using fixed historical types."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import MEDIUMBLOB

revision = "p0002"
down_revision = "p0001"


def upgrade() -> None:
    for table in ("pc_artifacts", "pc_artifact_candidate_versions"):
        op.add_column(table, sa.Column("memory_citations", sa.LargeBinary().with_variant(MEDIUMBLOB(), "mysql")))
