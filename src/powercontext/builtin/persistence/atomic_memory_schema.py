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

"""The authoritative Atomic Memory Family state table."""

from sqlalchemy import BigInteger, CheckConstraint, Column, Index, Table

from powercontext.builtin.persistence.tables import SHARED_METADATA, identity_string
from powercontext.limits import MAX_ARTIFACT_ID_LENGTH, MAX_SCOPE_ID_LENGTH

ATOMIC_MEMORY_STATES_TABLE = Table(
    "pc_atomic_memory_states",
    SHARED_METADATA,
    Column("scope_id", identity_string(MAX_SCOPE_ID_LENGTH), primary_key=True),
    Column("artifact_id", identity_string(MAX_ARTIFACT_ID_LENGTH), primary_key=True),
    Column("state", identity_string(16), nullable=False),
    Column("state_version", BigInteger, nullable=False),
    Column("merged_into_id", identity_string(MAX_ARTIFACT_ID_LENGTH)),
    CheckConstraint("state IN ('active', 'forgotten', 'merged', 'retired')", name="ck_pc_atomic_memory_state"),
    CheckConstraint("state_version >= 0", name="ck_pc_atomic_memory_state_version"),
    CheckConstraint(
        "(state = 'merged' AND merged_into_id IS NOT NULL AND merged_into_id <> artifact_id) "
        "OR (state <> 'merged' AND merged_into_id IS NULL)",
        name="ck_pc_atomic_memory_merge_target",
    ),
)
Index(
    "ix_pc_atomic_memory_states_management", ATOMIC_MEMORY_STATES_TABLE.c.scope_id, ATOMIC_MEMORY_STATES_TABLE.c.state
)

ATOMIC_MEMORY_TABLES = (ATOMIC_MEMORY_STATES_TABLE,)
