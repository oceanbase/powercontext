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
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, inspect
from sqlalchemy.engine import AdaptedConnection
from sqlalchemy.exc import OperationalError

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.persistence.atomic_memory_index import atomic_memory_profile_fingerprint

from .models import MigrationError, digest

_SQLITE_LAYOUTS: dict[str, dict[str, list[list[Any]]]] = {}


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

    def sqlite_layouts(self, directory: Path) -> dict[str, list[list[Any]]]:
        from alembic import command

        from powercontext.builtin.runtime.config import BuiltinConfig
        from powercontext.builtin.runtime.processing_registry import canonical_processing_manifest

        from .bundle import ALEMBIC_CONTEXT_LOCK, MigrationBundle, sqlite_inventory
        from .sqlite_compatibility import reference

        resources = MigrationBundle._read_resources(directory)
        key = digest({
            "profile": self.checksum,
            "resources": {name: hashlib.sha256(value).hexdigest() for name, value in resources.items()},
        })
        with ALEMBIC_CONTEXT_LOCK:
            if key not in _SQLITE_LAYOUTS:
                # Replay released schema definitions, including any later feature
                # revisions. No version-specific suffix needs adding here.
                base = MigrationBundle(directory)
                layouts = {}
                with reference([]) as connection:
                    load_sqlite_vector_extension(connection)
                    config = base.config(connection)
                    config.attributes["configuration"] = {
                        "processing_manifest": canonical_processing_manifest(BuiltinConfig()),
                    }
                    for revision in base.revisions:
                        command.upgrade(config, revision)
                        self.sqlite_upgrade(connection, revision)
                        layouts[revision] = sqlite_inventory(
                            connection, exclude=frozenset({"pc_schema_revision", *base.retained_objects})
                        )
                if len(_SQLITE_LAYOUTS) >= 16:
                    del _SQLITE_LAYOUTS[next(iter(_SQLITE_LAYOUTS))]
                _SQLITE_LAYOUTS[key] = layouts
            return copy.deepcopy(_SQLITE_LAYOUTS[key])

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
