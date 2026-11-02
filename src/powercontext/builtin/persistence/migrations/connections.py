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

"""Dedicated backend connections without business schema initialization."""

from __future__ import annotations

import asyncio
import socket
import sqlite3
from collections.abc import Callable, Mapping
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, TypeVar

from sqlalchemy import Connection, create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from powercontext.builtin.persistence.bootstrap import (
    DatabaseAccessError,
    DatabaseCreationError,
    MissingDatabaseError,
    ensure_database,
    require_existing_database,
)
from powercontext.builtin.persistence.database import begin_mysql_read_snapshot
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.oceanbase import profile as oceanbase_profile
from powercontext.builtin.persistence.seekdb import SeekDBConfig
from powercontext.builtin.persistence.seekdb import profile as seekdb_profile
from powercontext.builtin.persistence.sqlite import SQLiteConfig

from .locking import local_migration_lock
from .models import MigrationError, digest
from .oceanbase import oceanbase_migration_lock, pending_run

_T = TypeVar("_T")


@dataclass(frozen=True)
class BackendIdentity:
    """Stable maintenance identity, independent of credentials and proxy routes."""

    product: Literal["sqlite", "seekdb", "oceanbase"]
    database_id: str
    database_name: str


class MaintenanceConnections:
    """Run synchronous migration code inside the official async driver bridge.

    The connection and lock verifier are valid only during the supplied callback.
    A callback must commit its completed work explicitly; connection shutdown
    rolls back unfinished transactions. A missing seekdb inspection supplies no
    connection and does not start an engine or create any directory.
    """

    def __init__(
        self,
        config: SQLiteConfig | SeekDBConfig | OceanBaseConfig,
        *,
        evidence_directory: Path | None = None,
        lock_coordination: Literal["single-host"] | None = None,
        acknowledge_previous_run: str | None = None,
    ) -> None:
        self.config = config
        self.evidence_directory = evidence_directory.expanduser().resolve() if evidence_directory is not None else None
        self.lock_coordination = lock_coordination
        self.acknowledge_previous_run = acknowledge_previous_run

    def coordination(self, identity: BackendIdentity) -> dict[str, str]:
        if isinstance(self.config, SQLiteConfig):
            return {"mode": "database-file"}
        if isinstance(self.config, SeekDBConfig):
            return {"mode": "engine-directory", "directory": str(self.config.path.expanduser().resolve())}
        coordination = {
            "mode": self.lock_coordination or "unselected",
            "host": socket.gethostname(),
            "directory": str(self.evidence_directory / identity.database_id) if self.evidence_directory else "",
        }
        if self.evidence_directory is not None:
            previous = pending_run(self.evidence_directory, identity.database_id)
            if previous is not None:
                coordination["pending_run_id"] = previous
        return coordination

    def run(
        self,
        operation: Callable[[Connection | None, BackendIdentity, Callable[[], None]], _T],
        *,
        writable: bool = False,
        allow_missing_database: bool = False,
    ) -> _T:
        if isinstance(self.config, SQLiteConfig):
            return self._sqlite(operation, self.config, writable=writable)
        if isinstance(self.config, SeekDBConfig):
            path = self.config.path.expanduser().resolve()
            identity = BackendIdentity(
                "seekdb",
                digest({"backend": "seekdb", "path": str(path), "database": self.config.database}),
                self.config.database,
            )
            if not path.exists() and not writable:
                return operation(None, identity, lambda: None)
            with local_migration_lock(path):
                if path.exists() and not path.is_dir():
                    raise MigrationError("unsupported_target", "The configured seekdb target must be a directory.")
                nonempty = path.exists() and any(path.iterdir())
                if not writable and not nonempty:
                    return operation(None, identity, lambda: None)
                if nonempty and not seekdb_profile.has_native_storage(path):
                    raise MigrationError(
                        "unsupported_target", "A nonempty seekdb directory must contain recognized native storage."
                    )
                initial_inode = _directory_inode(path) if path.exists() else None
                return asyncio.run(
                    self._seekdb(
                        operation,
                        self.config,
                        path,
                        identity,
                        initial_inode,
                        writable=writable,
                        allow_missing_database=allow_missing_database,
                    )
                )
        return asyncio.run(self._oceanbase(operation, self.config, writable=writable))

    @staticmethod
    def _sqlite(
        operation: Callable[[Connection | None, BackendIdentity, Callable[[], None]], _T],
        config: SQLiteConfig,
        *,
        writable: bool,
    ) -> _T:
        url = make_url(config.url)
        if (
            not config.is_persistent
            or url.query
            or not url.database
            or url.username
            or url.password
            or url.host
            or url.port
        ):
            raise MigrationError("unsupported_target", "Maintenance requires a regular persistent SQLite file URL.")
        target = Path(url.database).absolute().resolve()
        identity = BackendIdentity("sqlite", digest({"kind": "sqlite", "path": str(target)}), target.name)
        if not target.exists():
            return operation(None, identity, lambda: None)
        if not target.is_file() or target.stat().st_nlink != 1:
            raise MigrationError("unsupported_target", "Use a regular SQLite file without hard-link aliases.")
        with local_migration_lock(target) if writable else nullcontext():
            uri = target.as_uri() + ("?mode=rw" if writable else "?mode=ro")
            engine = create_engine(
                "sqlite://", creator=lambda: sqlite3.connect(uri, uri=True, isolation_level=None, timeout=0)
            )
            try:
                with engine.connect() as connection:
                    connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                    if not writable:
                        connection.exec_driver_sql("PRAGMA query_only=ON")
                    connection.exec_driver_sql("BEGIN IMMEDIATE" if writable else "BEGIN")
                    return operation(connection, identity, lambda: None)
            finally:
                engine.dispose()

    async def _seekdb(  # noqa: C901
        self,
        operation: Callable[[Connection | None, BackendIdentity, Callable[[], None]], _T],
        config: SeekDBConfig,
        path: Path,
        identity: BackendIdentity,
        initial_inode: tuple[int, int] | None,
        *,
        writable: bool,
        allow_missing_database: bool,
    ) -> _T:
        try:
            module = seekdb_profile._load_binding()
        except (seekdb_profile.SeekDBUnavailableError, ImportError) as error:
            raise MigrationError("backend_unavailable", "The embedded seekdb binding is unavailable.") from error
        try:
            instance = await seekdb_profile._open_instance(module, path)
        except RuntimeError as error:
            raise MigrationError("backend_open_failed", "The embedded seekdb instance could not be opened.") from error
        try:
            engine = seekdb_profile._create_engine(
                config.model_copy(update={"echo": False}), instance.connection_options()
            )
            try:
                await _require_explicit_database(config, instance.connection_options())
                if writable:
                    await _provision_database(
                        engine,
                        lambda: seekdb_profile._create_engine(
                            config.model_copy(update={"echo": False}), instance.connection_options(), database=""
                        ),
                        name=config.database,
                    )
                else:
                    try:
                        await require_existing_database(engine, name=config.database)
                    except MissingDatabaseError as error:
                        if allow_missing_database:
                            if initial_inode != _directory_inode(path):
                                raise MigrationError(
                                    "migration_lock_lost", "The seekdb maintenance target was lost."
                                ) from error
                            return operation(None, identity, lambda: None)
                        raise MigrationError("database_missing", str(error)) from error
                    except DatabaseAccessError as error:
                        raise MigrationError("database_access_denied", str(error)) from error
                inode = _directory_inode(path)
                if initial_inode is not None and initial_inode != inode:
                    raise MigrationError("migration_lock_lost", "The seekdb directory changed during engine startup.")
                async with engine.connect() as connection:
                    if not writable:
                        await begin_mysql_read_snapshot(connection)

                    def execute(sync: Connection) -> _T:
                        def verify() -> None:
                            if sync.closed or sync.invalidated or _directory_inode(path) != inode:
                                raise MigrationError("migration_lock_lost", "The seekdb maintenance target was lost.")

                        verify()
                        if sync.exec_driver_sql("SELECT DATABASE()").scalar_one() != identity.database_name:
                            raise MigrationError("target_identity_unavailable", "The seekdb database identity changed.")
                        # MySQL's SQLAlchemy begin hook sends no SQL. Match the
                        # explicit transaction ownership used by AsyncDatabase.
                        if writable:
                            sync.exec_driver_sql("START TRANSACTION")
                        result = operation(sync, identity, verify)
                        verify()
                        return result

                    return await connection.run_sync(execute)
            finally:
                await _dispose_engine(engine)
        finally:
            instance.close()

    async def _oceanbase(
        self,
        operation: Callable[[Connection | None, BackendIdentity, Callable[[], None]], _T],
        config: OceanBaseConfig,
        *,
        writable: bool,
    ) -> _T:
        evidence_directory = self.evidence_directory
        if writable and (self.lock_coordination != "single-host" or evidence_directory is None):
            raise MigrationError(
                "coordination_required", "Select single-host coordination and one persistent evidence directory."
            )
        oceanbase_profile._register_official_dialect()
        engine = create_async_engine(
            config.url.get_secret_value(),
            connect_args={"init_command": "SET autocommit = 0"},
            echo=False,
            hide_parameters=True,
            pool_pre_ping=config.pool_pre_ping,
            poolclass=NullPool,
        )
        try:
            async with engine.connect() as connection:
                if not writable:
                    await begin_mysql_read_snapshot(connection)
                identity = await connection.run_sync(_oceanbase_identity)
                if not writable:
                    return await connection.run_sync(lambda sync: operation(sync, identity, lambda: None))
                if evidence_directory is None:
                    raise MigrationError("coordination_required", "Select one persistent evidence directory.")
                async with engine.connect() as contender:
                    other_identity = await contender.run_sync(_oceanbase_identity)
                    if other_identity != identity:
                        raise MigrationError(
                            "target_identity_unavailable", "Maintenance sessions reached different database identities."
                        )
                    async with oceanbase_migration_lock(
                        connection,
                        database_id=identity.database_id,
                        directory=evidence_directory,
                        acknowledged_run=self.acknowledge_previous_run,
                    ) as lock:

                        def execute(sync: Connection) -> _T:
                            def verify() -> None:
                                lock.verify_sync(sync)

                            verify()
                            result = operation(sync, identity, verify)
                            verify()
                            return result

                        return await connection.run_sync(execute)
        finally:
            await _dispose_engine(engine)


async def _provision_database(
    engine: AsyncEngine,
    instance_engine: Callable[[], AsyncEngine],
    *,
    name: str,
) -> None:
    """Turn provisioning failures into the maintenance error contract.

    The bootstrap layer already produced administrator guidance; without this
    translation ``db-migrate`` would report a traceback instead of its documented
    blocked JSON payload.
    """
    try:
        await ensure_database(engine, instance_engine, name=name)
    except DatabaseAccessError as error:
        raise MigrationError("database_access_denied", str(error)) from error
    except DatabaseCreationError as error:
        raise MigrationError("database_creation_required", str(error)) from error


async def _require_explicit_database(config: SeekDBConfig, options: Mapping[str, object]) -> None:
    """Turn the expected legacy-database refusal into the maintenance error contract.

    An uncaught exception would replace the documented blocked JSON payload with a
    traceback, so the operator-facing guidance is preserved inside a MigrationError.
    """
    try:
        await seekdb_profile._require_explicit_legacy_database(config, options)
    except seekdb_profile.LegacySeekDBDatabaseError as error:
        raise MigrationError("explicit_database_required", str(error)) from error


def _directory_inode(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
    except OSError as error:
        raise MigrationError("migration_lock_lost", "The seekdb maintenance directory is unavailable.") from error
    return stat.st_dev, stat.st_ino


def _oceanbase_identity(connection: Connection) -> BackendIdentity:
    try:
        mode = connection.exec_driver_sql("SHOW VARIABLES LIKE 'ob_compatibility_mode'").first()
        if mode is None or len(mode) < 2 or str(mode[1]).upper() != "MYSQL":
            raise MigrationError("unsupported_target", "OceanBase maintenance requires a MySQL user tenant.")
        database_name, tenant_id = connection.exec_driver_sql("SELECT DATABASE(), effective_tenant_id()").one()
        cluster_ids = (
            connection
            .exec_driver_sql("SELECT DISTINCT VALUE FROM oceanbase.GV$OB_PARAMETERS WHERE NAME = 'cluster_id'")
            .scalars()
            .all()
        )
        tenant_created, tenant_type = connection.exec_driver_sql(
            "SELECT CREATE_TIME, TENANT_TYPE FROM oceanbase.DBA_OB_TENANTS WHERE TENANT_ID = effective_tenant_id()"
        ).one()
        if (
            not isinstance(database_name, str)
            or not database_name
            or not isinstance(tenant_id, int)
            or tenant_id <= 0
            or len(cluster_ids) != 1
            or int(cluster_ids[0]) <= 0
            or not isinstance(tenant_created, datetime)
        ):
            raise MigrationError("target_identity_unavailable", "The database did not return a stable identity.")
        if tenant_type != "USER":
            raise MigrationError("unsupported_target", "OceanBase maintenance requires a MySQL user tenant.")
        database_id = digest({
            "backend": "oceanbase",
            "cluster_id": int(cluster_ids[0]),
            "tenant_id": tenant_id,
            "tenant_created": tenant_created.isoformat(),
            "database_name": database_name,
        })
        return BackendIdentity("oceanbase", database_id, database_name)
    except (SQLAlchemyError, ValueError, TypeError) as error:
        raise MigrationError(
            "target_identity_unavailable", "The connected OceanBase identity could not be verified."
        ) from error


async def _dispose_engine(engine: AsyncEngine) -> None:
    task = asyncio.create_task(engine.dispose())
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await seekdb_profile._finish_task(task)
        raise
