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

"""Add the durable Work Ledger and its scheduler coordination tables.

The revision follows master's published ``v1_5_0_001`` head rather than being
inserted before it: databases already migrated by a released binary are stamped
at ``v1_5_0_001``, and re-parenting that revision would leave them with no
upgrade path to create the ledger tables. The release-based name follows the
same rule that named ``v1_4_1_004``.
"""

import json
from pathlib import Path

from alembic import op

revision = "v1_5_0_002"
down_revision = "v1_5_0_001"


def upgrade():
    resource = Path(__file__).parents[1] / "schemas/sqlite-v1_5_0_002.json"
    connection = op.get_bind()
    for statement in json.loads(resource.read_text())["create"]:
        connection.exec_driver_sql(statement)
