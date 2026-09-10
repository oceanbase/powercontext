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

"""Add durable artifact processing and Topic Memory state."""

from __future__ import annotations

from alembic import op

from powercontext.builtin.persistence.tables import (
    ARTIFACT_PROCESSING_AUTO_WAVE_TARGETS_TABLE,
    ARTIFACT_PROCESSING_BINDING_STATES_TABLE,
    ARTIFACT_PROCESSING_LEASES_TABLE,
    ARTIFACT_PROCESSING_PENDING_TABLE,
    SHARED_METADATA,
    TOPIC_MEMORY_TABLES,
)

revision = "0005_topic_memory"
down_revision = "0004_profile_tags"
branch_labels = None
depends_on = None

_NEW_TABLES = (
    ARTIFACT_PROCESSING_LEASES_TABLE,
    ARTIFACT_PROCESSING_BINDING_STATES_TABLE,
    ARTIFACT_PROCESSING_PENDING_TABLE,
    ARTIFACT_PROCESSING_AUTO_WAVE_TARGETS_TABLE,
    *TOPIC_MEMORY_TABLES,
)


def upgrade() -> None:
    SHARED_METADATA.create_all(op.get_bind(), tables=_NEW_TABLES, checkfirst=True)


def downgrade() -> None:
    raise NotImplementedError("PowerContext schema migrations are forward-only")
