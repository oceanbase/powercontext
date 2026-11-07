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

"""Validate legacy database selection in a bounded embedded owner process."""

from __future__ import annotations

import asyncio
import faulthandler
import json
import sys
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pymysql
from httpx import ASGITransport, AsyncClient

from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.seekdb import SeekDBConfig, SeekDBProfile
from powercontext.builtin.persistence.seekdb import profile as seekdb_profile
from powercontext.server.factory import create_server_app
from powercontext.server.settings import ServerSettings


def database_snapshot(options: dict[str, object]) -> None:
    """Inspect only this test's embedded socket, with bounded diagnostic reads."""
    socket = options.get("unix_socket")
    if not isinstance(socket, str):
        return
    for query in (
        "SHOW FULL PROCESSLIST",
        "SELECT * FROM oceanbase.__all_virtual_trans_stat LIMIT 20",
        "SELECT * FROM oceanbase.__all_virtual_lock_wait_stat LIMIT 20",
    ):
        try:
            with (
                pymysql.connect(
                    unix_socket=socket,
                    user="root",
                    autocommit=True,
                    connect_timeout=2,
                    read_timeout=2,
                    write_timeout=2,
                ) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(query)
                print(
                    json.dumps(
                        {
                            "query": query,
                            "columns": [column[0] for column in cursor.description],
                            "rows": cursor.fetchall(),
                        },
                        default=str,
                    ),
                    file=sys.stderr,
                    flush=True,
                )
        except (pymysql.Error, OSError) as error:
            print(f"diagnostic unavailable: {type(error).__name__}", file=sys.stderr, flush=True)


async def diagnose(options: dict[str, object], current_phase: Callable[[], str]) -> None:
    while True:
        await asyncio.sleep(30)
        print(f"pending phase: {current_phase()}", file=sys.stderr, flush=True)
        for task in asyncio.all_tasks():
            if task is asyncio.current_task():
                continue
            waiting = task.get_coro()
            while waiting is not None:
                frame = getattr(waiting, "cr_frame", None) or getattr(waiting, "gi_frame", None)
                if frame is not None:
                    print(
                        f"await {frame.f_code.co_filename}:{frame.f_lineno} {frame.f_code.co_name}",
                        file=sys.stderr,
                        flush=True,
                    )
                waiting = getattr(waiting, "cr_await", None) or getattr(waiting, "gi_yieldfrom", None)
        await asyncio.to_thread(database_snapshot, options.copy())


async def scenario(directory: Path) -> None:
    path = directory / "seekdb"
    old = SeekDBConfig(path=path, database="test")
    options: dict[str, object] = {}
    phase = "starting"
    original_open = seekdb_profile._open_instance

    async def observed_open(module: ModuleType, path: Path) -> seekdb_profile._SeekDBInstance:
        instance = await original_open(module, path)
        options.clear()
        options.update(instance.connection_options())
        print(f"{phase}: native open completed", file=sys.stderr, flush=True)
        return instance

    def stage(value: str) -> None:
        nonlocal phase
        phase = value
        print(phase, file=sys.stderr, flush=True)

    async def assert_preserved(app) -> None:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            response = await client.get(f"/v1/scopes/{retained['scope_id']}")
            assert response.status_code == 200, response.text
            assert response.json() == retained
            assert (await client.get("/v1/server-info")).json()["server_id"] == original_identity

    observer = patch.object(seekdb_profile, "_open_instance", observed_open)
    observer.start()
    watchdog = asyncio.create_task(diagnose(options, lambda: phase), name="seekdb-bootstrap-diagnostics")
    try:
        # Two fresh Server databases execute the complete registered migration
        # chain; the other startups verify admission and preserved readback.
        async with asyncio.timeout(300):
            stage("seed: opening test")
            # Use the Server's registered schema and public write path so the
            # retained deployment remains valid through full startup admission.
            app = create_server_app(settings=ServerSettings(database=old, workspace=directory))
            async with (
                app.router.lifespan_context(app),
                AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client,
            ):
                stage("seed: insert")
                response = await client.post(
                    "/v1/scopes",
                    json={"title": "Preserved scope", "summary": "Retained legacy data", "idempotency_key": "legacy"},
                )
                assert response.status_code == 201, response.text
                retained = response.json()
                original_identity = (await client.get("/v1/server-info")).json()["server_id"]
                stage("seed: closing")
            stage("implicit choice: opening")
            app = create_server_app(settings=ServerSettings(database=SeekDBConfig(path=path), workspace=directory))
            try:
                async with app.router.lifespan_context(app):
                    raise AssertionError("Implicit database switch must be rejected")  # noqa: TRY003
            except MigrationError as error:
                assert error.code == "explicit_database_required"
                assert "database='test'" in str(error)
                assert "database='powercontext'" in str(error)
            stage("old data: reopening test")
            app = create_server_app(settings=ServerSettings(database=old, workspace=directory))
            async with app.router.lifespan_context(app):
                stage("explicit legacy choice: server started")
                await assert_preserved(app)
            async with SeekDBProfile.open_existing(old, tables=()) as profile:
                stage("old data: read")
                async with profile.database.transaction() as connection:
                    assert (await connection.exec_driver_sql("SELECT DATABASE()")).scalar_one() == "test"
                    assert (
                        await connection.exec_driver_sql(
                            "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s",
                            ("powercontext",),
                        )
                    ).first() is None
                stage("old data: closing")
            stage("explicit choice: opening powercontext")
            app = create_server_app(
                settings=ServerSettings(database=SeekDBConfig(path=path, database="powercontext"), workspace=directory)
            )
            async with (
                app.router.lifespan_context(app),
                AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client,
            ):
                stage("explicit default choice: server started")
                assert (await client.get(f"/v1/scopes/{retained['scope_id']}")).status_code == 404
                assert (await client.get("/v1/server-info")).json()["server_id"] != original_identity
            async with SeekDBProfile.open_existing(
                SeekDBConfig(path=path, database="powercontext"), tables=()
            ) as profile:
                stage("explicit choice: read empty database")
                async with profile.database.transaction() as connection:
                    assert (await connection.exec_driver_sql("SELECT DATABASE()")).scalar_one() == "powercontext"
                stage("explicit choice: closing")
            stage("final read: reopening test")
            app = create_server_app(settings=ServerSettings(database=old, workspace=directory))
            async with app.router.lifespan_context(app):
                stage("final read: check preserved data")
                await assert_preserved(app)
                stage("final read: closing")
            stage("complete")
    finally:
        observer.stop()
        watchdog.cancel()
        with suppress(asyncio.CancelledError):
            await watchdog


if __name__ == "__main__":
    faulthandler.dump_traceback_later(45, repeat=True)
    try:
        asyncio.run(scenario(Path(sys.argv[1])))
    finally:
        faulthandler.cancel_dump_traceback_later()
