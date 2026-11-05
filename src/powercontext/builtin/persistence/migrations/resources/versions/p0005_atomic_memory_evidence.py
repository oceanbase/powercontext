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

"""Frozen additive evidence revision; never synthesize historical declarations."""

import json
from pathlib import Path

from alembic import op

revision = "p0005"
down_revision = "p0004"


def upgrade() -> None:
    statements = json.loads((Path(__file__).parents[1] / "schemas/atomic_memory_evidence_v1.json").read_text())
    connection = op.get_bind()
    for statement in statements["sqlite" if connection.dialect.name == "sqlite" else "mysql"]:
        connection.exec_driver_sql(statement)
