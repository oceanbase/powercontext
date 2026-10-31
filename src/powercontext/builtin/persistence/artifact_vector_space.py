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

"""Server-owned OceanBase/seekDB VECTOR spaces, prepared from durable declarations."""

import re
from hashlib import sha256

from pyobvector import VECTOR
from sqlalchemy import (
    Column,
    ForeignKeyConstraint,
    Index,
    MetaData,
    Table,
    UniqueConstraint,
    select,
    update,
)

from powercontext.builtin.artifacts.memory.canonical import canonical_json
from powercontext.builtin.artifacts.memory.models import EmbeddingProfile
from powercontext.builtin.persistence.tables import (
    ARTIFACT_VECTOR_INDEXES_TABLE,
    ARTIFACT_VECTOR_SPACES_TABLE,
    ARTIFACT_VECTORS_TABLE,
    SHARED_METADATA,
)


def native_vector_table(space):
    identity = space["space_id"]
    if re.fullmatch(r"[0-9a-f]{32}", identity) is None or space["physical_table"] != f"pc_av_{identity}":
        raise ValueError("invalid registered vector space table identity")  # noqa: TRY003
    profile = EmbeddingProfile.model_validate(space["profile_json"])
    fingerprint = sha256(canonical_json(profile.model_dump(mode="json"))).hexdigest()
    if (
        (profile.dimension, profile.distance, profile.normalization, fingerprint)
        != (space["dimension"], space["distance"], space["normalization"], space["profile_fingerprint"])
        or profile.dimension < 1
        or space["storage_format_version"] != "float32-le-v1"
    ):
        raise ValueError("inconsistent vector space profile or storage format")  # noqa: TRY003
    metadata = MetaData()
    # Foreign key targets resolve in isolated metadata without provisioning them here.
    for name in ("pc_artifacts", "pc_artifact_search_states"):
        target = SHARED_METADATA.tables[name]
        Table(
            name,
            metadata,
            *(Column(column.name, column.type, primary_key=True) for column in target.primary_key.columns),
        )
    return Table(
        space["physical_table"],
        metadata,
        *(
            Column(
                column.name,
                VECTOR(profile.dimension) if column.name == "embedding" else column.type,
                primary_key=column.primary_key,
                nullable=column.nullable,
            )
            for column in ARTIFACT_VECTORS_TABLE.columns
        ),
        UniqueConstraint("build_id", "index_def_id", "artifact_id", "fragment_id", name=f"uq_av_{identity}"),
        ForeignKeyConstraint(
            ("scope_id", "family", "artifact_id", "revision"),
            ("pc_artifacts.scope_id", "pc_artifacts.family", "pc_artifacts.artifact_id", "pc_artifacts.revision"),
        ),
        ForeignKeyConstraint(
            ("scope_id", "family"), ("pc_artifact_search_states.scope_id", "pc_artifact_search_states.family")
        ),
        Index(f"ix_av_{identity}", "scope_id", "family", "build_id", "space_id", "index_def_id", "artifact_id"),
    )


def native_space_layout(space):
    """The one exact framework template shared by preparation and migration."""
    from sqlalchemy.dialects import mysql
    from sqlalchemy.schema import CreateTable

    from powercontext.builtin.persistence.migrations.mysql_schema import _parse_table

    table = native_vector_table(space)
    _, layout = _parse_table(str(CreateTable(table).compile(dialect=mysql.dialect())))
    for index in table.indexes:
        layout["indexes"][index.name] = {
            "columns": [[column.name, None, "asc"] for column in index.columns],
            "unique": index.unique,
        }
    return layout


def inspect_native_space(space, actual, *, allow_incomplete=False):
    """Return missing indexes only for an exact, declared creation intermediate."""
    import copy

    from powercontext.builtin.persistence.migrations.mysql_schema import _matches

    expected = native_space_layout(space)
    if space["backend"] != "mysql" or space["provision_state"] not in {"declared", "ready"}:
        raise ValueError("invalid native vector space declaration")  # noqa: TRY003
    incomplete = allow_incomplete and space["provision_state"] == "declared"
    if actual is None:
        if incomplete:
            return tuple(expected["indexes"])
        raise ValueError("vector space physical table is absent")  # noqa: TRY003
    partial = copy.deepcopy(expected)
    missing = tuple(name for name in expected["indexes"] if name not in actual["indexes"])
    # Unique constraints are part of CREATE TABLE, only standalone indexes can
    # be absent after interruption between table and index creation.
    allowed_missing = {index.name for index in native_vector_table(space).indexes}
    if missing and (not incomplete or not set(missing) <= allowed_missing):
        raise ValueError("vector space required indexes are absent")  # noqa: TRY003
    for name in missing:
        del partial["indexes"][name]
    if not _matches({"tables": {"space": actual}}, {"tables": {"space": partial}}):
        raise ValueError("vector space physical structure differs from its registered format")  # noqa: TRY003
    return missing


def registered_native_layouts(declarations, tables, *, allow_declared=False):
    """Admit only declared identities with the exact supported physical format."""
    layouts = {}
    names = set()
    for space in declarations:
        name = space["physical_table"]
        if name in names or (space["provision_state"] != "ready" and not allow_declared):
            raise ValueError("native vector space preparation is incomplete or duplicated")  # noqa: TRY003
        names.add(name)
        actual = tables.get(name)
        inspect_native_space(space, actual, allow_incomplete=allow_declared)
        if actual is not None:
            layouts[name] = actual
    return layouts


def _physical_layout(connection, name):
    from sqlalchemy import inspect

    from powercontext.builtin.persistence.migrations.mysql_schema import _parse_table

    if not inspect(connection).has_table(name):
        return None
    # The name has already passed native_vector_table's fixed hex identity check.
    row = connection.exec_driver_sql(f"SHOW CREATE TABLE `{name}`").one()
    database = connection.exec_driver_sql("SELECT DATABASE()").scalar_one()
    return _parse_table(row[1], database=database)[1]


def verify_native_space(connection, space):
    table = native_vector_table(space)
    inspect_native_space(space, _physical_layout(connection, table.name))


def provision_native_space(connection, space):
    """Converge using atomic database DDL, then inspect before publishing ready."""
    from sqlalchemy.exc import DBAPIError
    from sqlalchemy.schema import CreateTable

    table = native_vector_table(space)
    actual = _physical_layout(connection, table.name)
    missing = inspect_native_space(space, actual, allow_incomplete=True)
    if space["provision_state"] == "ready":
        return
    if actual is None:
        # IF NOT EXISTS serializes competing CREATEs at the backend. It is not
        # admission: the resulting definition must still match exactly.
        connection.execute(CreateTable(table, if_not_exists=True))
        missing = inspect_native_space(space, _physical_layout(connection, table.name), allow_incomplete=True)
    for index in table.indexes:
        if index.name not in missing:
            continue
        try:
            index.create(connection)
        except DBAPIError as error:
            # A concurrent creator may have won this particular index. No other
            # failure is normalized, and the full structure is checked below.
            arguments = getattr(error.orig, "args", ())
            if not arguments or arguments[0] != 1061:
                raise
    verify_native_space(connection, space)
    connection.execute(
        update(ARTIFACT_VECTOR_SPACES_TABLE)
        .where(
            ARTIFACT_VECTOR_SPACES_TABLE.c.space_id == space["space_id"],
            ARTIFACT_VECTOR_SPACES_TABLE.c.provision_state == "declared",
        )
        .values(provision_state="ready")
    )


async def prepare_family_spaces(database, family):
    """Await only this family's durable spaces, outside registration transactions."""
    if database.engine.dialect.name == "sqlite":
        return
    spaces = ARTIFACT_VECTOR_SPACES_TABLE
    definitions = ARTIFACT_VECTOR_INDEXES_TABLE
    async with database.transaction() as connection:
        declarations = (
            (
                await connection.execute(
                    select(spaces).where(
                        spaces.c.space_id.in_(select(definitions.c.space_id).where(definitions.c.family == family))
                    )
                )
            )
            .mappings()
            .all()
        )
    for space in declarations:
        # DDL may commit independently. This connection owns no content or
        # plugin registration writes; ready is committed only after verification.
        async with database.transaction() as connection:
            current = (
                (await connection.execute(select(spaces).where(spaces.c.space_id == space["space_id"])))
                .mappings()
                .one()
            )
            await connection.run_sync(lambda sync, current=current: provision_native_space(sync, current))


def provision_native_spaces(connection):
    """Prepare persisted native declarations during unmanaged initialization."""
    spaces = (
        connection
        .execute(select(ARTIFACT_VECTOR_SPACES_TABLE).where(ARTIFACT_VECTOR_SPACES_TABLE.c.backend == "mysql"))
        .mappings()
        .all()
    )
    for space in spaces:
        provision_native_space(connection, space)
