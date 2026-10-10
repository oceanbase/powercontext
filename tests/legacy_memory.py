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

"""Recreate the public schema shape that held legacy Memory references before migration."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

LEGACY_CITATION_TABLES = ("pc_artifacts", "pc_artifact_candidate_versions")


async def add_legacy_citation_columns(connection: AsyncConnection) -> None:
    """Add the nullable ``memory_citations`` columns that migration converts and then drops."""

    column_type = "MEDIUMBLOB" if connection.dialect.name == "mysql" else "BLOB"
    for table in LEGACY_CITATION_TABLES:
        await connection.execute(text(f"ALTER TABLE {table} ADD COLUMN memory_citations {column_type}"))
