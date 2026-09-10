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

"""Add Profile processing policy and Artifact tag state."""

from __future__ import annotations

from alembic import op

from powercontext.builtin.persistence.tables import ARTIFACT_TAGS_TABLE, PROFILE_POLICIES_TABLE, SHARED_METADATA

revision = "0004_profile_tags"
down_revision = "0003_scope_source_skill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    SHARED_METADATA.create_all(
        op.get_bind(),
        tables=(PROFILE_POLICIES_TABLE, ARTIFACT_TAGS_TABLE),
        checkfirst=True,
    )


def downgrade() -> None:
    raise NotImplementedError("PowerContext schema migrations are forward-only")
