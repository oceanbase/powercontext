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

"""Register the Work Ledger tables through the shared MySQL DDL executor.

The revision follows ``v1_4_1_006`` so both the SQLite and MySQL histories stay
linear behind master's Source-write-plugin revision, and keeps the release-based
name that history introduced.
"""

from alembic import context

revision = "v1_4_1_007"
down_revision = "v1_4_1_006"


def upgrade() -> None:
    context.config.attributes["execute_revision"](revision)
