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

"""Opt-in real seekDB/OceanBase database bootstrap acceptance.

OceanBase tests create and drop only generated pc_bootstrap_* databases/users.
The default-name scenario additionally requires an explicitly disposable tenant.
Set POWERCONTEXT_TEST_BOOTSTRAP_OCEANBASE_URL to a real reachable endpoint before
starting pytest; the example hostname "host" is rejected. Setup is checked before
resource cleanup is registered, and cleanup failures never replace test failures.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import Column, Index, Integer, MetaData, String, Table, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from typer.testing import CliRunner

from powercontext.builtin.persistence.bootstrap import (
    DatabaseAccessError,
    DatabaseCreationError,
    MissingDatabaseError,
    mysql_error_code,
)
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.oceanbase.profile import _register_official_dialect
from powercontext.builtin.persistence.seekdb import SeekDBConfig, SeekDBProfile
from powercontext.cli.app import create_cli
from powercontext.server.cli import app as server_app

_METADATA = MetaData()
_RECORDS = Table(
    "pc_bootstrap_records",
    _METADATA,
    Column("id", Integer, primary_key=True),
    Column("value", String(64), nullable=False),
)
Index("ix_pc_bootstrap_records_value", _RECORDS.c.value)
_SEEKDB = os.environ.get("POWERCONTEXT_TEST_BOOTSTRAP_SEEKDB") == "1"
_OCEANBASE = os.environ.get("POWERCONTEXT_TEST_BOOTSTRAP_OCEANBASE_URL")


async def insert_record(profile) -> None:
    async with profile.database.transaction() as connection:
        await connection.execute(_RECORDS.insert().values(id=1, value="preserved"))


async def assert_record(profile, name: str) -> None:
    async with profile.database.transaction() as connection:
        assert await connection.scalar(select(_RECORDS.c.value).where(_RECORDS.c.id == 1)) == "preserved"
        assert (await connection.exec_driver_sql("SELECT DATABASE()")).scalar_one() == name


_MAXIMUM_REPORTED_OUTPUT = 6000


def run_seekdb_scenario(directory: Path, filename: str, *, timeout: int = 90) -> None:
    """Retry once only when the child exceeds the process deadline.

    A native stall can prevent in-process cancellation, so retry with a fresh
    engine instance. Completed failures remain fatal even after diagnostic dumps.
    """
    scenario = Path(__file__).resolve().parents[1] / "fixtures" / filename
    reported: list[str] = []
    for attempt in (1, 2):
        target = directory / f"attempt-{attempt}"
        target.mkdir()
        output = ""
        failed = True
        stalled = False
        try:
            result = subprocess.run(
                [sys.executable, "-u", str(scenario), str(target)],
                cwd=target,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            output = result.stdout + result.stderr
            failed = result.returncode != 0
        except subprocess.TimeoutExpired as error:
            # TimeoutExpired retains bytes even when subprocess.run uses text=True.
            output = (error.stdout or b"").decode(errors="replace") + (error.stderr or b"").decode(errors="replace")
            stalled = True
        finally:
            _retain_scenario_diagnostics(scenario, target, attempt, output, failed or stalled)
        if not failed and not stalled:
            return
        reported.append(f"attempt {attempt}:\n{_bounded_output(output)}")
        if attempt == 2 or not stalled:
            break
    pytest.fail(f"{filename} failed after {len(reported)} attempt(s)\n" + "\n".join(reported))


def test_seekdb_scenario_runner_does_not_retry_completed_assertion_failure(tmp_path: Path) -> None:
    scenario = tmp_path / "assertion_after_diagnostic.py"
    scenario.write_text('print("Timeout (0: diagnostic dump", flush=True)\nraise AssertionError("contract failed")\n')

    with pytest.raises(pytest.fail.Exception, match="AssertionError: contract failed"):
        run_seekdb_scenario(tmp_path, str(scenario))
    assert not (tmp_path / "attempt-2").exists()


def _bounded_output(output: str) -> str:
    return output if len(output) <= _MAXIMUM_REPORTED_OUTPUT else "..." + output[-_MAXIMUM_REPORTED_OUTPUT:]


def _retain_scenario_diagnostics(scenario: Path, directory: Path, attempt: int, output: str, failed: bool) -> None:
    diagnostic_root = os.environ.get("POWERCONTEXT_TEST_BOOTSTRAP_DIAGNOSTICS")
    if not diagnostic_root:
        return
    try:
        destination = Path(diagnostic_root) / scenario.stem
        destination.mkdir(parents=True, exist_ok=True)
        (destination / f"attempt-{attempt}.log").write_text(output, encoding="utf-8")
        if failed:
            # Retain bounded native log tails before short_tmp_path removes storage.
            for path in (directory / "seekdb" / "log").glob("*"):
                if path.is_file() and path.name.endswith((".log", ".log.wf")):
                    with (
                        path.open("rb") as source,
                        gzip.open(destination / f"attempt-{attempt}.{path.name}.tail.gz", "wb") as target,
                    ):
                        source.seek(max(0, path.stat().st_size - 1024 * 1024))
                        target.write(source.read())
    except OSError as error:
        print(f"seekdb diagnostics unavailable: {type(error).__name__}", file=sys.stderr)


@pytest.mark.skipif(not _SEEKDB, reason="set POWERCONTEXT_TEST_BOOTSTRAP_SEEKDB=1")
@pytest.mark.parametrize("name", [None, "team_context", "团队库"])
def test_seekdb_creates_selected_database_and_preserves_data(short_tmp_path: Path, name: str | None) -> None:
    pytest.importorskip("pylibseekdb")

    async def scenario() -> None:
        config = (
            SeekDBConfig(path=short_tmp_path / "seekdb")
            if name is None
            else SeekDBConfig(path=short_tmp_path / "seekdb", database=name)
        )
        async with SeekDBProfile.open(config, tables=(_RECORDS,)) as profile:
            await insert_record(profile)
            with pytest.raises(RuntimeError, match="abort business write"):
                async with profile.database.transaction() as connection:
                    await connection.execute(_RECORDS.update().values(value="must roll back"))
                    raise RuntimeError("abort business write")  # noqa: TRY003
            await assert_record(profile, name or "powercontext")
        async with SeekDBProfile.open(config, tables=(_RECORDS,)) as profile:
            await assert_record(profile, name or "powercontext")

    asyncio.run(scenario())


@pytest.mark.skipif(not _SEEKDB, reason="set POWERCONTEXT_TEST_BOOTSTRAP_SEEKDB=1")
def test_seekdb_rejects_backtick_database_without_side_effects(short_tmp_path: Path) -> None:
    pytest.importorskip("pylibseekdb")

    async def scenario() -> None:
        path = short_tmp_path / "seekdb"
        retained = SeekDBConfig(path=path, database="retained_context")
        async with SeekDBProfile.open(retained, tables=(_RECORDS,)) as profile:
            await insert_record(profile)
            async with profile.database.transaction() as connection:
                before = await connection.exec_driver_sql(
                    "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s", ("quoted`db",)
                )
                assert before.all() == []
            with pytest.raises(ValidationError, match="Choose a name without backticks"):
                async with SeekDBProfile.open(SeekDBConfig(path=path, database="quoted`db"), tables=(_RECORDS,)):
                    pytest.fail("Unsupported database names must be rejected before initialization")
            async with profile.database.transaction() as connection:
                after = await connection.exec_driver_sql(
                    "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s", ("quoted`db",)
                )
                assert after.all() == []
            await assert_record(profile, retained.database)
        async with SeekDBProfile.open(retained, tables=(_RECORDS,)) as profile:
            await assert_record(profile, retained.database)

    asyncio.run(scenario())


@pytest.mark.skipif(not _SEEKDB, reason="set POWERCONTEXT_TEST_BOOTSTRAP_SEEKDB=1")
def test_seekdb_inspection_reuses_an_existing_database_without_provisioning(short_tmp_path: Path) -> None:
    pytest.importorskip("pylibseekdb")

    async def scenario() -> None:
        path = short_tmp_path / "seekdb"
        retained = SeekDBConfig(path=path, database="retained_context")
        async with SeekDBProfile.open(retained, tables=(_RECORDS,)) as profile:
            await insert_record(profile)
        # A second identical attempt proves inspection never created the database.
        for _attempt in range(2):
            with pytest.raises(MissingDatabaseError, match="does not exist"):
                async with SeekDBProfile.open_existing(SeekDBConfig(path=path, database="absent_context"), tables=()):
                    pytest.fail("Inspection must not provision a missing database")
        async with SeekDBProfile.open_existing(retained, tables=()) as profile:
            await assert_record(profile, retained.database)
            async with profile.database.transaction() as connection:
                absent = await connection.exec_driver_sql(
                    "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s",
                    ("absent_context",),
                )
                assert absent.all() == []

    asyncio.run(scenario())


@pytest.mark.skipif(not _SEEKDB, reason="set POWERCONTEXT_TEST_BOOTSTRAP_SEEKDB=1")
def test_seekdb_db_migrate_plans_and_applies_a_missing_selected_database(short_tmp_path: Path) -> None:
    pytest.importorskip("pylibseekdb")
    path = short_tmp_path / "seekdb"
    retained = SeekDBConfig(path=path, database="retained_context")

    async def seed() -> None:
        async with SeekDBProfile.open(retained, tables=()):
            pass

    asyncio.run(seed())
    environment = short_tmp_path / "server.env"
    environment.write_text(
        f"POWERCONTEXT_SERVER_DATABASE_KIND=seekdb\n"
        f"POWERCONTEXT_SERVER_DATABASE_PATH={path}\n"
        "POWERCONTEXT_SERVER_DATABASE_DATABASE=absent_context\n",
        encoding="utf-8",
    )

    cli = create_cli([server_app])
    for command in ("status", "verify"):
        result = CliRunner().invoke(cli, ["server", "db-migrate", command, "--env-file", str(environment)])
        assert result.exit_code == 1, result.output
        payload = json.loads(result.stdout)
        assert payload["state"] == "blocked"
        assert payload["error"] == "database_missing"
        assert "absent_context" in payload["message"]
        assert "does not exist" in payload["message"]
        assert "CREATE DATABASE IF NOT EXISTS `absent_context`" in payload["message"]
        assert "No fallback database was selected" in payload["message"]

    planned = CliRunner().invoke(
        cli, ["server", "db-migrate", "plan", "--env-file", str(environment), "--backup", "skip"]
    )
    assert planned.exit_code == 0, planned.output
    plan = json.loads(planned.stdout)
    assert plan["state"] == "uninitialized"

    async def verify() -> None:
        async with (
            SeekDBProfile.open_existing(retained, tables=()) as profile,
            profile.database.transaction() as connection,
        ):
            assert (await connection.exec_driver_sql("SELECT DATABASE()")).scalar_one() == retained.database
            absent = await connection.exec_driver_sql(
                "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s",
                ("absent_context",),
            )
            assert absent.all() == []

    asyncio.run(verify())

    applied = CliRunner().invoke(
        cli,
        [
            "server",
            "db-migrate",
            "apply",
            "--env-file",
            str(environment),
            "--backup",
            "skip",
            "--plan-id",
            plan["plan_id"],
            "--yes",
            "--maintenance-confirmed",
        ],
    )
    assert applied.exit_code == 0, applied.output
    assert json.loads(applied.stdout)["state"] == "ready"
    verified = CliRunner().invoke(cli, ["server", "db-migrate", "verify", "--env-file", str(environment)])
    assert verified.exit_code == 0, verified.output
    assert json.loads(verified.stdout)["state"] == "ready"


@pytest.mark.skipif(not _SEEKDB, reason="set POWERCONTEXT_TEST_BOOTSTRAP_SEEKDB=1")
def test_seekdb_legacy_choice_preserves_old_data_and_separates_new_database(short_tmp_path: Path) -> None:
    pytest.importorskip("pylibseekdb")
    run_seekdb_scenario(short_tmp_path, "seekdb_legacy_choice.py", timeout=360)


@pytest.mark.skipif(not _SEEKDB, reason="set POWERCONTEXT_TEST_BOOTSTRAP_SEEKDB=1")
def test_seekdb_legacy_migration_cli_preserves_json_with_sql_echo(short_tmp_path: Path) -> None:
    pytest.importorskip("pylibseekdb")
    # One child owns the engine throughout. The process deadline also bounds
    # native startup and shutdown, which asyncio cancellation cannot interrupt.
    run_seekdb_scenario(short_tmp_path, "seekdb_legacy_migration_cli.py")


@pytest.mark.skipif(not _SEEKDB, reason="set POWERCONTEXT_TEST_BOOTSTRAP_SEEKDB=1")
def test_seekdb_concurrent_profiles_initialize_one_database(short_tmp_path: Path) -> None:
    """Exercise concurrent clients in one embedded owner process, not multiple engine-owner processes."""
    pytest.importorskip("pylibseekdb")

    async def scenario() -> None:
        config = SeekDBConfig(path=short_tmp_path / "seekdb", database="concurrent_context")

        async def initialize() -> None:
            async with (
                SeekDBProfile.open(config, tables=(_RECORDS,)) as profile,
                profile.database.transaction() as connection,
            ):
                assert (await connection.exec_driver_sql("SELECT DATABASE()")).scalar_one() == config.database

        async def initialize_all() -> None:
            async with asyncio.timeout(90), asyncio.TaskGroup() as group:
                for _ in range(3):
                    group.create_task(initialize())

        await initialize_all()
        async with SeekDBProfile.open(config, tables=(_RECORDS,)) as profile:
            await insert_record(profile)
        await initialize_all()
        async with SeekDBProfile.open(config, tables=(_RECORDS,)) as profile:
            await assert_record(profile, config.database)

    asyncio.run(scenario())


@dataclass
class _OceanBaseInstance:
    engine: AsyncEngine
    database_name: str
    databases: list[str] = field(default_factory=list)
    created_users: list[str] = field(default_factory=list)

    async def reserve_database(self, name: str) -> None:
        # Negative cases can use invalid application identifiers; quote independently.
        if name != "powercontext" and not name.startswith("pc_bootstrap_"):
            pytest.fail("Only bootstrap test databases may be reserved", pytrace=False)
        async with asyncio.timeout(15), self.engine.connect() as connection:
            result = await connection.exec_driver_sql(
                "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s", (name,)
            )
            if result.first() is not None:
                pytest.fail(
                    f"Refusing to use existing database {name!r}; use an exclusive disposable tenant", pytrace=False
                )
        self.databases.append(name)


@asynccontextmanager
async def _oceanbase_instance(value: str | None) -> AsyncIterator[_OceanBaseInstance]:
    try:
        config = OceanBaseConfig(url=SecretStr(value or ""))
    except ValidationError:
        pytest.fail(
            "Invalid POWERCONTEXT_TEST_BOOTSTRAP_OCEANBASE_URL: supply a real disposable MySQL user tenant URL "
            "with an explicit port, charset=utf8mb4 and percent-encoded credentials",
            pytrace=False,
        )
    url = make_url(config.url.get_secret_value())
    if (url.host or "").casefold() == "host":
        pytest.fail("Replace example hostname 'host' in POWERCONTEXT_TEST_BOOTSTRAP_OCEANBASE_URL", pytrace=False)
    _register_official_dialect()
    engine = create_async_engine(
        url.set(database=""),
        hide_parameters=True,
        connect_args={"connect_timeout": 10, "init_command": "SET autocommit = 0"},
    )
    instance = _OceanBaseInstance(engine, "pc_bootstrap_" + uuid4().hex[:20])
    failure: BaseException | None = None
    try:
        try:
            # The first reservation also checks connectivity; failed setup owns no database.
            await instance.reserve_database(instance.database_name)
        except (SQLAlchemyError, TimeoutError) as error:
            code = mysql_error_code(error) if isinstance(error, DBAPIError) else None
            pytest.fail(
                f"OceanBase bootstrap setup failed ({type(error).__name__}, code={code}). "
                "Check host/port, DNS, network and tenant credentials in POWERCONTEXT_TEST_BOOTSTRAP_OCEANBASE_URL",
                pytrace=False,
            )
        yield instance
    except BaseException as error:
        failure = error
        raise
    finally:
        quote = engine.dialect.identifier_preparer.quote_identifier
        cleanup: list[tuple[str, tuple[str, ...]]] = [
            ("DROP USER %s@%s", (user, "%")) for user in reversed(instance.created_users)
        ]
        cleanup.extend((f"DROP DATABASE IF EXISTS {quote(name)}", ()) for name in reversed(instance.databases))
        errors: list[str] = []
        try:
            for statement, parameters in cleanup:
                try:
                    async with asyncio.timeout(15), engine.begin() as connection:
                        await connection.exec_driver_sql(statement, parameters)
                except Exception as error:
                    code = mysql_error_code(error) if isinstance(error, DBAPIError) else None
                    errors.append(f"Cleanup failed: {type(error).__name__}, code={code}")
        finally:
            try:
                async with asyncio.timeout(15):
                    await engine.dispose()
            except Exception as error:
                errors.append(f"Engine disposal failed: {type(error).__name__}")
            if errors:
                message = "; ".join(errors) + ". Inspect the disposable tenant for leftover bootstrap resources."
                if failure is not None:
                    failure.add_note(message)
                else:
                    pytest.fail(message, pytrace=False)


def oceanbase_config(name: str) -> OceanBaseConfig:
    assert _OCEANBASE is not None
    url = make_url(_OCEANBASE).set(database=name)
    return OceanBaseConfig(url=SecretStr(url.render_as_string(hide_password=False)))


@pytest.mark.skipif(not _OCEANBASE, reason="set POWERCONTEXT_TEST_BOOTSTRAP_OCEANBASE_URL for a disposable tenant")
def test_oceanbase_creates_custom_database_and_preserves_data() -> None:
    async def scenario() -> None:
        async with _oceanbase_instance(_OCEANBASE) as instance:
            name = instance.database_name
            config = oceanbase_config(name)
            async with OceanBaseProfile.open(config, tables=(_RECORDS,)) as profile:
                await insert_record(profile)
            async with OceanBaseProfile.open(config, tables=(_RECORDS,)) as profile:
                await assert_record(profile, name)

    asyncio.run(scenario())


@pytest.mark.skipif(not _OCEANBASE, reason="set POWERCONTEXT_TEST_BOOTSTRAP_OCEANBASE_URL for a disposable tenant")
def test_oceanbase_rejects_backtick_database_without_side_effects() -> None:
    async def scenario() -> None:
        async with _oceanbase_instance(_OCEANBASE) as instance:
            admin, name = instance.engine, instance.database_name
            config = oceanbase_config(name)
            invalid_name = name + "`db"
            await instance.reserve_database(invalid_name)
            async with OceanBaseProfile.open(config, tables=(_RECORDS,)) as profile:
                await insert_record(profile)
            async with admin.connect() as connection:
                before = await connection.exec_driver_sql(
                    "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s", (invalid_name,)
                )
                assert before.all() == []
            with pytest.raises(ValidationError, match="Choose a name without backticks"):
                async with OceanBaseProfile.open(oceanbase_config(invalid_name), tables=(_RECORDS,)):
                    pytest.fail("Unsupported database names must be rejected before initialization")
            async with admin.connect() as connection:
                after = await connection.exec_driver_sql(
                    "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s", (invalid_name,)
                )
                assert after.all() == []
            async with OceanBaseProfile.open(config, tables=(_RECORDS,)) as profile:
                await assert_record(profile, name)

    asyncio.run(scenario())


@pytest.mark.skipif(not _OCEANBASE, reason="set POWERCONTEXT_TEST_BOOTSTRAP_OCEANBASE_URL for a disposable tenant")
def test_oceanbase_concurrent_initialization_preserves_data() -> None:
    async def scenario() -> None:
        async with _oceanbase_instance(_OCEANBASE) as instance:
            name = instance.database_name
            config = oceanbase_config(name)

            async def initialize() -> None:
                async with (
                    OceanBaseProfile.open(config, tables=(_RECORDS,)) as profile,
                    profile.database.transaction() as connection,
                ):
                    assert (await connection.exec_driver_sql("SELECT DATABASE()")).scalar_one() == name

            async def initialize_all() -> None:
                async with asyncio.timeout(90), asyncio.TaskGroup() as group:
                    for _ in range(4):
                        group.create_task(initialize())

            await initialize_all()
            async with OceanBaseProfile.open(config, tables=(_RECORDS,)) as profile:
                await insert_record(profile)
            await initialize_all()
            async with OceanBaseProfile.open(config, tables=(_RECORDS,)) as profile:
                await assert_record(profile, name)

    asyncio.run(scenario())


@pytest.mark.skipif(
    not _OCEANBASE or os.environ.get("POWERCONTEXT_TEST_BOOTSTRAP_OCEANBASE_DEFAULT") != "1",
    reason="default-name test requires an exclusive disposable tenant and POWERCONTEXT_TEST_BOOTSTRAP_OCEANBASE_DEFAULT=1",
)
def test_oceanbase_omitted_database_initializes_default() -> None:
    async def scenario() -> None:
        async with _oceanbase_instance(_OCEANBASE) as instance:
            await instance.reserve_database("powercontext")
            config = oceanbase_config("")
            async with OceanBaseProfile.open(config, tables=(_RECORDS,)) as profile:
                await insert_record(profile)
            async with OceanBaseProfile.open(config, tables=(_RECORDS,)) as profile:
                await assert_record(profile, "powercontext")

    asyncio.run(scenario())


@pytest.mark.skipif(
    not _OCEANBASE or os.environ.get("POWERCONTEXT_TEST_BOOTSTRAP_PRIVILEGES") != "1",
    reason="privilege test requires disposable-user CREATE USER/GRANT privileges and POWERCONTEXT_TEST_BOOTSTRAP_PRIVILEGES=1",
)
def test_oceanbase_reuses_database_without_create_privilege_and_guides_missing_database() -> None:
    async def scenario() -> None:
        assert _OCEANBASE is not None
        async with _oceanbase_instance(_OCEANBASE) as instance:
            admin, name = instance.engine, instance.database_name
            username = "pc_boot_" + uuid4().hex[:12]
            password = uuid4().hex
            missing = "pc_bootstrap_missing_" + uuid4().hex[:12]
            async with OceanBaseProfile.open(oceanbase_config(name), tables=(_RECORDS,)) as profile:
                await insert_record(profile)
            async with admin.begin() as connection:
                await connection.exec_driver_sql("CREATE USER %s@%s IDENTIFIED BY %s", (username, "%", password))
                instance.created_users.append(username)
                await connection.exec_driver_sql(
                    f"GRANT SELECT, INSERT, UPDATE, DELETE ON `{name}`.* TO %s@%s", (username, "%")
                )
            admin_url = make_url(_OCEANBASE)
            tenant = (admin_url.username or "").partition("@")[2]
            login = username + ("@" + tenant if tenant else "")
            user_url = admin_url.set(username=login, password=password, database=name)
            config = OceanBaseConfig(url=SecretStr(user_url.render_as_string(hide_password=False)))
            async with OceanBaseProfile.open(config, tables=(_RECORDS,)) as profile:
                await assert_record(profile, name)
            missing_url = user_url.set(database=missing).render_as_string(hide_password=False)
            await instance.reserve_database(missing)
            with pytest.raises((DatabaseCreationError, DatabaseAccessError), match="administrator") as error:
                async with OceanBaseProfile.open(OceanBaseConfig(url=SecretStr(missing_url)), tables=(_RECORDS,)):
                    pytest.fail("Unprivileged account must not create a database")
            assert "CREATE DATABASE IF NOT EXISTS" in str(error.value)
            assert password not in str(error.value)
            async with admin.connect() as connection:
                result = await connection.exec_driver_sql(
                    "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = %s", (missing,)
                )
                assert result.first() is None

    asyncio.run(scenario())
