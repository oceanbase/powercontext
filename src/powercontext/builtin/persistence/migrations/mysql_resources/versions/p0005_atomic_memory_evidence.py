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

"""Create the frozen additive evidence layout without backfilling trust."""

from alembic import context, op

revision = "p0005"
down_revision = "p0004"


def upgrade() -> None:
    context.config.attributes["execute_revision"](revision)
    op.execute("INSERT INTO pc_atomic_memory_evidence_schema (schema_version) VALUES ('p0005')")
