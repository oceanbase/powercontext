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

"""Render the registered vector layout without invoking runtime initializers."""

from __future__ import annotations

import copy
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, inspect
from sqlalchemy.engine import AdaptedConnection
from sqlalchemy.exc import OperationalError

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.persistence.atomic_memory_index import atomic_memory_profile_fingerprint

from .models import MigrationError, digest


class ServerVectorProfile:
    """Bind frozen schema templates to one exact embedding space."""

    resource = "profiles/vector.json"

    def __init__(self, directory: Path, profile: EmbeddingProfile):
        if profile.dimension < 1 or profile.distance != "l2" or profile.normalization != "unit":
            raise MigrationError(
                "unsupported_upgrade_configuration", "Server vectors require a positive unit-normalized L2 profile."
            )
        self.profile = profile.model_copy(deep=True)
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        self.source_revision = manifest.get("source_revision", "s1300")
        source = (directory / self.resource).read_text(encoding="utf-8")
        self.layout: dict[str, Any] = json.loads(
            source
            .replace("__DIMENSION__", str(profile.dimension))
            .replace("__VECTOR_BYTES__", str(profile.dimension * 4))
            .replace("__ATOMIC_FINGERPRINT__", atomic_memory_profile_fingerprint(profile))
        )
        if self.layout.get("format") != "powercontext.server-vector-profile.v1":
            raise MigrationError("invalid_bundle", "The Server vector schema registration is invalid.")
        self.checksum = digest({
            "template": json.loads(source),
            "profile": profile.model_dump(mode="json"),
        })

    def sqlite_layouts(self, directory: Path) -> dict[str, list[list[Any]]]:  # noqa: C901 - Compose the registered revision layouts in chain order.
        core = json.loads((directory / "schemas/sqlite.json").read_text(encoding="utf-8"))
        archive = json.loads((directory / "schemas/sqlite-s1401.json").read_text(encoding="utf-8"))["create"]
        layouts = {
            self.source_revision: core["source"] + self.layout["sqlite"]["source"],
            "s1400": core["target"] + self.layout["sqlite"]["target"],
        }
        # The frozen s1400 table rebuild uses ALTER TABLE RENAME, whose stored
        # definition quotes the final name. Match those exact reviewed bytes.
        for row in layouts["s1400"]:
            if row[0] == "table" and row[1] in self.layout["sqlite"]["rebuilt_tables"]:
                row[3] = row[3].replace(f"CREATE TABLE {row[1]} ", f'CREATE TABLE "{row[1]}" ', 1)
        layouts["s1401"] = layouts["s1400"] + [
            ["table", "pc_memory_artifact_archive", "pc_memory_artifact_archive", archive]
        ]
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        if "s1402" in manifest["revision_resources"]:
            receipt = json.loads((directory / "schemas/sqlite-s1402.json").read_text(encoding="utf-8"))["create"]
            layouts["s1402"] = layouts["s1401"] + [
                ["table", "pc_portable_restore_receipts", "pc_portable_restore_receipts", receipt]
            ]
        if "v1_4_1_004" in manifest["revision_resources"]:
            owner = json.loads((directory / "schemas/sqlite-v1_4_1_004.json").read_text(encoding="utf-8"))["create"]
            layouts["v1_4_1_004"] = layouts["s1402"] + [
                ["table", "pc_access_owner_recovery", "pc_access_owner_recovery", owner]
            ]
        if "v1_4_1_005" in manifest["revision_resources"]:
            layouts["v1_4_1_005"] = copy.deepcopy(layouts["v1_4_1_004"])
            statement = json.loads((directory / "schemas/sqlite-v1_4_1_005.json").read_text(encoding="utf-8"))[
                "statement"
            ]
            for row in layouts["v1_4_1_005"]:
                if row[0] == "table" and row[1] == "pc_atomic_memory_current":
                    # SQLite records ALTER TABLE's exact rewritten definition.
                    # Render it with SQLite while retaining every vector object.
                    with closing(sqlite3.connect(":memory:")) as connection:
                        connection.execute(row[3])
                        connection.execute(statement)
                        row[3] = connection.execute(
                            "SELECT sql FROM sqlite_schema WHERE name='pc_atomic_memory_current'"
                        ).fetchone()[0]
        if "v1_4_1_006" in manifest["revision_resources"]:
            plugin = json.loads((directory / "schemas/sqlite-v1_4_1_006.json").read_text(encoding="utf-8"))["create"]
            layouts["v1_4_1_006"] = layouts["v1_4_1_005"] + [
                ["table", "pc_source_write_plugins", "pc_source_write_plugins", plugin]
            ]
        artifact_revision = "v1_5_0_001"
        if artifact_revision in manifest["revision_resources"]:
            artifact = json.loads((directory / f"schemas/sqlite-{artifact_revision}.json").read_text())["layout"]
            previous = {(row[0], row[1]): row for row in layouts["v1_4_1_006"]}
            for row in artifact:
                key = (row[0], row[1])
                if key not in previous or row[1] == "pc_artifact_processing_binding_states":
                    previous[key] = row
            layouts[artifact_revision] = list(previous.values())
        if "v1_5_0_002" in manifest["revision_resources"]:
            evidence = json.loads((directory / "schemas/sqlite-v1_5_0_002.json").read_text(encoding="utf-8"))["create"]
            layouts["v1_5_0_002"] = layouts["v1_5_0_001"] + [
                ["table", name, name, statement] for statement in evidence if (name := _created_table(statement))
            ]
        if "v1_5_0_003" in manifest["revision_resources"]:
            evidence = json.loads((directory / "schemas/sqlite-v1_5_0_003.json").read_text(encoding="utf-8"))["create"]
            layouts["v1_5_0_003"] = layouts["v1_5_0_002"] + [
                ["table", name, name, statement] for statement in evidence if (name := _created_table(statement))
            ]
        for entries in layouts.values():
            entries.sort(key=lambda row: (row[0], row[1]))
        return layouts

    def sqlite_upgrade(self, connection: Connection, revision: str) -> None:
        operations = {self.source_revision: "initialize", "s1400": "upgrade"}
        if revision in operations:
            for statement in self.layout["sqlite"][operations[revision]]:
                connection.exec_driver_sql(statement)

    def verify_declaration(self, connection: Connection, *, required: bool) -> None:
        stored = read_embedding_profile(connection)
        if stored is None:
            if required:
                raise MigrationError("embedding_profile_mismatch", "The managed vector profile declaration is missing.")
        elif stored != self.profile:
            raise MigrationError(
                "embedding_profile_mismatch", "The configured embedding profile differs from the managed database."
            )

    def mysql_tables(self) -> dict[str, str]:
        return copy.deepcopy(self.layout["mysql"])


def _created_table(statement: str) -> str | None:
    """Name a created table without rewriting its frozen bytes."""

    prefix = "CREATE TABLE "
    if not statement.startswith(prefix):
        return None
    return statement[len(prefix) :].split(" ", 1)[0].strip('`"')


def read_embedding_profile(connection: Connection) -> EmbeddingProfile | None:
    """Read a managed configuration declaration; never infer it from vector bytes."""
    if "pc_server_storage_profile" not in inspect(connection).get_table_names():
        return None
    try:
        rows = connection.exec_driver_sql("SELECT singleton,embedding_profile FROM pc_server_storage_profile").all()
        if len(rows) != 1 or rows[0][0] != 1:
            raise ValueError("The profile declaration must contain one singleton")  # noqa: TRY301, TRY003
        return EmbeddingProfile.model_validate_json(rows[0][1])
    except (ValueError, TypeError) as error:
        raise MigrationError(
            "embedding_profile_mismatch", "The managed vector profile declaration is invalid."
        ) from error


def load_sqlite_vector_extension(connection: Connection) -> None:
    """Allow generic storage readers to validate a managed vector database."""
    try:
        connection.exec_driver_sql("SELECT vec_version()")
    except OperationalError as error:
        if not isinstance(error.orig, sqlite3.Error) or "no such function: vec_version" not in str(error.orig):
            raise
    else:
        return
    import sqlite_vec

    driver = connection.connection.dbapi_connection
    if isinstance(driver, AdaptedConnection):

        async def load(raw):
            await raw.enable_load_extension(True)
            try:
                await raw.load_extension(sqlite_vec.loadable_path())
            finally:
                await raw.enable_load_extension(False)

        driver.run_async(load)
    elif isinstance(driver, sqlite3.Connection):
        driver.enable_load_extension(True)
        try:
            sqlite_vec.load(driver)
        finally:
            driver.enable_load_extension(False)
    else:
        raise MigrationError("unsupported_backend", "The managed SQLite vector profile needs a supported driver.")
