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

"""Read-only Dream preflight before business initialization.

This domain check does not replace RFC 1771's full-database readiness gate.
"""

from sqlalchemy import CheckConstraint, Connection, inspect
from sqlalchemy.engine.reflection import Inspector
from sqlalchemy.ext.asyncio import AsyncEngine

from powercontext.builtin.persistence.tables import CANDIDATE_HEADS_TABLE, CANDIDATE_VERSIONS_TABLE


class DreamSchemaNotReadyError(RuntimeError):
    """Existing storage requires explicit unified maintenance before business use."""

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        super().__init__(
            f"{reason}: {detail}. Use powercontext server db-migrate with a supported full-database bundle; "
            "ordinary startup does not migrate Dream storage."
        )


async def assert_dream_schema_ready(engine: AsyncEngine) -> None:
    """Check before create_all, domain initializers, or workers; never execute DDL."""
    async with engine.connect() as connection:
        await connection.run_sync(check_dream_schema)


def check_dream_schema(connection: Connection) -> None:
    inspector = inspect(connection)
    names = set(inspector.get_table_names())
    if "pc_schema_revision" in names:
        # Until RFC 1771's complete Server gate is integrated, never turn the
        # four-table acceptance prototype into a business DB using create_all.
        raise DreamSchemaNotReadyError(
            "migration_framework_not_ready", "Full Server readiness for versioned databases is not integrated"
        )
    if names & {"pc_catalog_change_candidate_heads", "pc_catalog_change_candidate_versions"}:
        raise DreamSchemaNotReadyError("unknown_baseline", "Unpublished Catalog Change storage is unsupported")
    old = names & {"pc_artifact_candidate_heads", "pc_artifact_candidate_versions"}
    current = names & {"pc_candidate_heads", "pc_candidate_versions"}
    if old:
        reason = "recovery_required" if current else "migration_required"
        raise DreamSchemaNotReadyError(reason, "Legacy or ambiguous Candidate storage")
    if current and len(current) != 2:
        raise DreamSchemaNotReadyError("recovery_required", "Incomplete Candidate table pair")
    if any(name.startswith(("pc_candidate_heads_", "pc_candidate_versions_", "pc_dream_upgrade_")) for name in names):
        raise DreamSchemaNotReadyError("recovery_required", "Unregistered Candidate migration residue")

    _check_candidate_constraints(inspector, names)

    for name, required in (
        ("pc_artifacts", {"memory_citations"}),
        ("pc_dream_runs", {"proposal_fingerprint"}),
        ("pc_artifact_processing_intents", {"consecutive_dream_attempts"}),
    ):
        if name in names and not required <= {column["name"] for column in inspector.get_columns(name)}:
            raise DreamSchemaNotReadyError(
                "migration_required", "Dream provenance, deduplication or scheduling needs upgrading"
            )
    if "pc_dream_runs" in names and not any(
        index["name"] == "ix_pc_dream_runs_proposal_fingerprint"
        and index["column_names"] == ["scope_id", "principal_key", "proposal_fingerprint"]
        for index in inspector.get_indexes("pc_dream_runs")
    ):
        raise DreamSchemaNotReadyError("migration_required", "Dream deduplication index is missing")


def _check_candidate_constraints(inspector: Inspector, names: set[str]) -> None:
    for table in (CANDIDATE_VERSIONS_TABLE, CANDIDATE_HEADS_TABLE):
        if table.name not in names:
            continue
        columns = {column["name"] for column in inspector.get_columns(table.name)}
        required_checks = {
            constraint.name for constraint in table.constraints if isinstance(constraint, CheckConstraint)
        }
        checks = {constraint["name"] for constraint in inspector.get_check_constraints(table.name)}
        required_keys = {
            (tuple(key.column_keys), tuple(element.target_fullname for element in key.elements), key.ondelete)
            for key in table.foreign_key_constraints
        }
        actual_keys = set()
        for key in inspector.get_foreign_keys(table.name):
            ondelete = key.get("options", {}).get("ondelete")
            # MySQL-compatible servers omit their default RESTRICT action from
            # SHOW CREATE TABLE; NO ACTION has the same immediate semantics.
            if inspector.dialect.name == "mysql" and ondelete in {None, "NO ACTION"}:
                ondelete = "RESTRICT"
            actual_keys.add((
                tuple(key["constrained_columns"]),
                tuple(f"{key['referred_table']}.{column}" for column in key["referred_columns"]),
                ondelete,
            ))
        if not set(table.c.keys()) <= columns or not required_checks <= checks or not required_keys <= actual_keys:
            raise DreamSchemaNotReadyError("migration_required", "Candidate columns or constraints need upgrading")
