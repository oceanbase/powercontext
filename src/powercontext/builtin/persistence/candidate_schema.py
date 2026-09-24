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

"""Upgrade Candidate storage before domain initialization or worker startup."""

from sqlalchemy import CheckConstraint, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.schema import CreateTable

from powercontext.builtin.persistence.tables import CANDIDATE_HEADS_TABLE, CANDIDATE_VERSIONS_TABLE


class CandidateMigrationError(RuntimeError):
    """Candidate storage cannot safely be exposed to readers or workers."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"Candidate migration incomplete: {reason}")


async def migrate_candidate_schema(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        tables = set(await connection.run_sync(lambda sync: inspect(sync).get_table_names()))
        _require(
            not tables & {"pc_catalog_change_candidate_heads", "pc_catalog_change_candidate_versions"},
            "unpublished Catalog Change schema requires explicit recovery",
        )
        candidate_tables = {
            "pc_artifact_candidate_heads",
            "pc_artifact_candidate_versions",
            "pc_candidate_heads",
            "pc_candidate_versions",
        }
        if not tables & candidate_tables:
            return

        sqlite = connection.dialect.name == "sqlite"
        foreign_keys = None
        locked = False
        try:
            if sqlite:
                foreign_keys = await connection.scalar(text("PRAGMA foreign_keys"))
                await connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
                await connection.commit()
                await connection.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                locked = await connection.scalar(text("SELECT GET_LOCK('powercontext_candidate_schema', 60)")) == 1
                _require(locked, "unable to acquire migration lock")
            await _upgrade(connection)
            await connection.commit()
        except BaseException:
            await connection.rollback()
            raise
        finally:
            if sqlite and foreign_keys is not None:
                await connection.exec_driver_sql(f"PRAGMA foreign_keys={'ON' if foreign_keys else 'OFF'}")
                await connection.commit()
            elif locked:
                await connection.execute(text("SELECT RELEASE_LOCK('powercontext_candidate_schema')"))
                await connection.commit()


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise CandidateMigrationError(reason)


async def _upgrade(connection: AsyncConnection) -> None:
    tables = set(await connection.run_sync(lambda sync: inspect(sync).get_table_names()))
    _require(
        not tables & {"pc_catalog_change_candidate_heads", "pc_catalog_change_candidate_versions"},
        "unpublished Catalog Change schema requires explicit recovery",
    )
    for suffix in ("versions", "heads"):
        old, new = f"pc_artifact_candidate_{suffix}", f"pc_candidate_{suffix}"
        _require(not (old in tables and new in tables), "ambiguous old and new Candidate tables")
    for suffix in ("versions", "heads"):
        old, new = f"pc_artifact_candidate_{suffix}", f"pc_candidate_{suffix}"
        if old in tables:
            await connection.exec_driver_sql(f"ALTER TABLE {old} RENAME TO {new}")
            tables.remove(old)
            tables.add(new)
    present = tables & {"pc_candidate_heads", "pc_candidate_versions"}
    if not present:
        return
    _require(len(present) == 2, "missing Candidate head or version table")
    if connection.dialect.name == "sqlite":
        await _sqlite_constraints(connection)
    else:
        await _mysql_constraints(connection)
    await _verify(connection)


async def _sqlite_constraints(connection: AsyncConnection) -> None:
    # Foreign keys are disabled only on this dedicated startup connection; the
    # exclusive write transaction makes both rebuilds and reference rewrites atomic.
    for table in (CANDIDATE_VERSIONS_TABLE, CANDIDATE_HEADS_TABLE):
        ddl = str(await connection.scalar(text("SELECT sql FROM sqlite_master WHERE name=:name"), {"name": table.name}))
        if "pc_scopes" in ddl and (table is CANDIDATE_VERSIONS_TABLE or "ck_pc_candidate_kind" in ddl):
            continue
        names = {
            column["name"]
            for column in await connection.run_sync(lambda sync, name=table.name: inspect(sync).get_columns(name))
        }
        temporary = table.name + "_migrating"
        _require(
            not await connection.run_sync(lambda sync, name=temporary: inspect(sync).has_table(name)),
            "unexpected temporary migration table",
        )
        sql = str(CreateTable(table).compile(dialect=connection.dialect))
        await connection.exec_driver_sql(sql.replace(f"CREATE TABLE {table.name}", f"CREATE TABLE {temporary}"))
        fields = ", ".join(column.name for column in table.columns if column.name in names)
        # All identifiers come from the fixed SQLAlchemy metadata above.
        await connection.exec_driver_sql(f"INSERT INTO {temporary} ({fields}) SELECT {fields} FROM {table.name}")  # noqa: S608
        for left, right in ((table.name, temporary), (temporary, table.name)):
            mismatch = await connection.scalar(
                text(
                    f"SELECT COUNT(*) FROM (SELECT {fields} FROM {left} EXCEPT SELECT {fields} FROM {right})"  # noqa: S608
                )
            )
            _require(mismatch == 0, "copied Candidate contents differ")
        await connection.exec_driver_sql(f"DROP TABLE {table.name}")
        await connection.exec_driver_sql(f"ALTER TABLE {temporary} RENAME TO {table.name}")


async def _mysql_constraints(connection: AsyncConnection) -> None:  # noqa: C901 - resumable DDL steps
    names = {
        column["name"]
        for column in await connection.run_sync(lambda sync: inspect(sync).get_columns("pc_candidate_heads"))
    }
    if "candidate_kind" not in names:
        await connection.exec_driver_sql(
            "ALTER TABLE pc_candidate_heads ADD COLUMN candidate_kind VARCHAR(16) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL DEFAULT 'artifact'"
        )
    if "result_payload" not in names:
        await connection.exec_driver_sql("ALTER TABLE pc_candidate_heads ADD COLUMN result_payload MEDIUMBLOB NULL")
    checks = await connection.run_sync(lambda sync: inspect(sync).get_check_constraints("pc_candidate_heads"))
    existing = {check["name"] for check in checks}
    old = "ck_pc_artifact_candidate_heads_terminal_result"
    if old in existing:
        await connection.exec_driver_sql(f"ALTER TABLE pc_candidate_heads DROP CHECK {old}")
    for constraint in CANDIDATE_HEADS_TABLE.constraints:
        if (
            isinstance(constraint, CheckConstraint)
            and constraint.name in {"ck_pc_candidate_heads_terminal_result", "ck_pc_candidate_kind"}
            and constraint.name not in existing
        ):
            await connection.exec_driver_sql(
                f"ALTER TABLE pc_candidate_heads ADD CONSTRAINT {constraint.name} CHECK ({constraint.sqltext})"
            )
    for table in (CANDIDATE_VERSIONS_TABLE, CANDIDATE_HEADS_TABLE):
        keys = await connection.run_sync(lambda sync, name=table.name: inspect(sync).get_foreign_keys(name))
        if not any(key["referred_table"] == "pc_scopes" for key in keys):
            await connection.exec_driver_sql(
                f"ALTER TABLE {table.name} ADD CONSTRAINT fk_{table.name}_scope FOREIGN KEY (scope_id) REFERENCES pc_scopes (scope_id) ON DELETE CASCADE"
            )
        if table is CANDIDATE_HEADS_TABLE:
            for key in keys:
                if (
                    key["referred_table"] == "pc_candidate_versions"
                    and key.get("options", {}).get("ondelete") != "CASCADE"
                ):
                    # Constraint identifiers originate from the trusted database schema.
                    name = connection.dialect.identifier_preparer.quote(str(key["name"]))
                    await connection.exec_driver_sql(f"ALTER TABLE pc_candidate_heads DROP FOREIGN KEY {name}")
            if not any(
                key["referred_table"] == "pc_candidate_versions" and key.get("options", {}).get("ondelete") == "CASCADE"
                for key in keys
            ):
                await connection.exec_driver_sql(
                    "ALTER TABLE pc_candidate_heads ADD CONSTRAINT fk_pc_candidate_head_version FOREIGN KEY (scope_id, candidate_id, version) REFERENCES pc_candidate_versions (scope_id, candidate_id, version) ON DELETE CASCADE"
                )


async def _verify(connection: AsyncConnection) -> None:
    heads, versions = CANDIDATE_HEADS_TABLE, CANDIDATE_VERSIONS_TABLE
    missing = await connection.scalar(
        select(heads.c.candidate_id)
        .outerjoin(
            versions,
            (heads.c.scope_id == versions.c.scope_id)
            & (heads.c.candidate_id == versions.c.candidate_id)
            & (heads.c.version == versions.c.version),
        )
        .where(versions.c.candidate_id.is_(None))
        .limit(1)
    )
    _require(missing is None, "Candidate head has no matching proposal version")
    if connection.dialect.name == "sqlite":
        violations = (await connection.exec_driver_sql("PRAGMA foreign_key_check")).all()
        _require(not violations, "foreign key verification failed")
