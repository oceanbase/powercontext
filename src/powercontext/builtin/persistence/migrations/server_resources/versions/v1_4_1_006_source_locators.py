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

"""Add position metadata for exact Source lineage edges."""

import json
from pathlib import Path

from alembic import op

revision = "v1_4_1_006"
down_revision = "v1_4_1_005"


def upgrade():
    resource = Path(__file__).parents[1] / "schemas/sqlite-v1_4_1_006.json"
    op.get_bind().exec_driver_sql(json.loads(resource.read_text(encoding="utf-8"))["create"])
