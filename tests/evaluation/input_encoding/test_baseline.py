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

"""Database admission checks with controlled OceanBase drivers and real SQLite."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from sqlalchemy.exc import OperationalError

from evaluation.performance.input_encoding import runner
from evaluation.performance.input_encoding.__main__ import main
from evaluation.performance.input_encoding.dataset import digest, write_json
from powercontext.builtin.persistence.bootstrap import MissingDatabaseError
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.oceanbase import profile as oceanbase
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft
from powercontext.server.settings import ServerSettings
from tests.e2e.rfc2086_real_acceptance import inspect_database


class _DatabaseEngine:
    dialect = SimpleNamespace(paramstyle="named")

    def __init__(self, name, databases, statements):
        self.name = name
        self.databases = databases
        self.statements = statements
        self.closed = False

    @asynccontextmanager
    async def connect(self):
        if self.name and self.name not in self.databases:
            raise OperationalError(None, None, Exception(1049, "synthetic missing database"))
        yield self

    def begin(self):
        return self.connect()

    async def exec_driver_sql(self, statement):
        self.statements.append(statement)
        if statement.startswith("CREATE DATABASE"):
            self.databases.add(statement.split("`", 2)[1])
        return SimpleNamespace(first=lambda: ("ob_compatibility_mode", "MYSQL"), scalar_one=lambda: self.name)

    async def scalar(self, statement):
        self.statements.append(str(statement))
        return "synthetic OceanBase"

    async def run_sync(self, callback):
        return ["pc_scopes", "pc_sources", "pc_artifacts", "pc_artifact_heads"]

    async def dispose(self):
        self.closed = True


@pytest.mark.parametrize("exists", [False, True])
def test_baseline_database_inspection_never_provisions_a_database(monkeypatch, exists):
    databases = {"existing"} if exists else set()
    statements = []

    @asynccontextmanager
    async def initialized(profile, **kwargs):
        # Schema reflection is controlled; connection/provisioning uses the real profile path.
        yield SimpleNamespace(database=SimpleNamespace(transaction=profile.database.engine.begin))

    monkeypatch.setattr(
        oceanbase, "_create_engine", lambda config, url: _DatabaseEngine(url.database, databases, statements)
    )
    monkeypatch.setattr(oceanbase, "_initialized_profile", initialized)
    settings = ServerSettings(
        database=OceanBaseConfig(
            url=SecretStr("mysql+aoceanbase://user:synthetic@localhost:2881/existing?charset=utf8mb4")
        )
    )
    if exists:
        report = asyncio.run(inspect_database(settings))
        assert report["state"] == "connected"
        assert report["schema_changed"] is report["data_changed"] is False
        assert all(report["required_tables_present"].values())
    else:
        with pytest.raises(MissingDatabaseError):
            asyncio.run(inspect_database(settings))
    assert databases == ({"existing"} if exists else set())
    assert not any(statement.startswith("CREATE") for statement in statements)


@pytest.mark.parametrize("state", ["missing", "existing", "removed_before_reopen"])
@pytest.mark.parametrize("override", [False, True])
def test_run_never_provisions_external_databases(tmp_path, monkeypatch, state, override):
    databases = set() if state == "missing" else {"evaluation"}
    statements = []
    engines = []
    completed = []
    environment = tmp_path / "fixture.env"
    database = {
        "kind": "oceanbase",
        "url": "mysql+aoceanbase://user:private-fixture-password@localhost:2881/evaluation?charset=utf8mb4",
    }
    environment.write_text(
        "POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=openai-chat:fixture\nOPENAI_API_KEY=private-fixture-key\n"
        + (
            ""
            if override
            else f"POWERCONTEXT_SERVER_DATABASE_KIND=oceanbase\nPOWERCONTEXT_SERVER_DATABASE_URL={database['url']}\n"
        )
    )
    database_file = tmp_path / "database.json"
    database_file.write_text(json.dumps(database))
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps(["fixture-case"]))
    args = SimpleNamespace(
        dataset=tmp_path / "unused-dataset",
        case_ids=cases,
        limit=1,
        repeats=1,
        seed=2086,
        output=tmp_path / "run",
        env_file=environment,
        database_config=database_file if override else None,
        protection_config=None,
        max_output_tokens=4096,
        cohort="existing-database-fixture",
        concurrency=1,
        timeout=10,
    )

    def create_engine(config, url):
        engine = _DatabaseEngine(url.database, databases, statements)
        engines.append(engine)
        return engine

    @asynccontextmanager
    async def initialized(profile, **kwargs):
        # Keep the real connection/provisioning path; isolate schema and model execution.
        yield profile

    @asynccontextmanager
    async def open_runtime(config, **kwargs):
        async with oceanbase.OceanBaseProfile.open(config.database, tables=()):
            yield object()

    async def attempt(runtime, instance, job, directory, *args, **kwargs):
        completed.append(job)
        write_json(directory / "attempts" / f"{digest(job)[:24]}.json", {**job, "status": "succeeded"})
        if state == "removed_before_reopen" and len(completed) == 3:
            databases.remove("evaluation")

    monkeypatch.setattr(oceanbase, "_create_engine", create_engine)
    monkeypatch.setattr(oceanbase, "_initialized_profile", initialized)
    monkeypatch.setattr(runner, "load_catalog", lambda path: SimpleNamespace(require=lambda name: object()))
    monkeypatch.setattr(runner, "open_builtin_runtime", open_runtime)
    monkeypatch.setattr(runner, "_attempt", attempt)
    failure = None
    try:
        asyncio.run(runner.run(args))
    except MissingDatabaseError as error:
        failure = error

    assert databases == ({"evaluation"} if state == "existing" else set())
    assert not any(statement.startswith("CREATE") for statement in statements)
    assert engines and all(engine.closed for engine in engines)
    segments = [json.loads(path.read_text()) for path in (args.output / "segments").glob("*.json")]
    if state == "existing":
        assert failure is None
        assert {job["mode"] for job in completed} == {"off", "shadow", "active"}
        assert segments[0]["status"] == "completed"
    else:
        assert isinstance(failure, MissingDatabaseError)
        assert segments[0]["status"] == "failed"
        assert len(completed) == (0 if state == "missing" else 3)
    for path in args.output.rglob("*.json"):
        assert "private-fixture" not in path.read_text()


@pytest.mark.parametrize("include_kind", [False, True])
def test_run_cli_uses_existing_sqlite_override_with_optional_kind(tmp_path, monkeypatch, include_kind):
    database = {"url": f"sqlite+aiosqlite:///{tmp_path / 'existing.db'}"}
    if include_kind:
        database["kind"] = "sqlite"
    config = BuiltinConfig.model_validate({
        "database": database,
        "runtime": {"artifact_processing_families": (), "profile_schedule_enabled": False},
    })

    async def seed():
        async with open_builtin_runtime(config, scheduler_path=tmp_path / "seed-scheduler.db") as runtime:
            assert runtime.scopes is not None
            return await runtime.scopes.create(
                ScopeDraft(title="Existing database", summary="Keep this Scope", idempotency_key="existing")
            )

    existing = asyncio.run(seed())
    environment = tmp_path / "fixture.env"
    environment.write_text("POWERCONTEXT_SERVER_DATABASE_KIND=sqlite\n")
    database_file = tmp_path / "database.json"
    database_file.write_text(json.dumps(database))
    cases = tmp_path / "cases.json"
    cases.write_text('["fixture-case"]')
    output = tmp_path / "run"
    completed = []

    async def attempt(runtime, instance, job, directory, *args, **kwargs):
        assert await runtime.scopes.get(existing.scope_id) == existing
        completed.append(job["mode"])
        key = digest(job)[:24]
        write_json(
            directory / "attempts" / f"{key}.json",
            {
                **job,
                "attempt_id": key,
                "segment_id": kwargs["segment_id"],
                "status": "no_proposal",
            },
        )

    monkeypatch.setattr(runner, "load_catalog", lambda path: SimpleNamespace(require=lambda name: object()))
    monkeypatch.setattr(runner, "_attempt", attempt)
    monkeypatch.setattr(
        "sys.argv",
        [
            "input_encoding",
            "run",
            "--output",
            str(output),
            "--env-file",
            str(environment),
            "--database-config",
            str(database_file),
            "--case-ids",
            str(cases),
            "--repeats",
            "1",
        ],
    )

    main()

    assert set(completed) == {"off", "shadow", "active"}
    assert json.loads((output / "manifest.json").read_text())["database_kind"] == "sqlite"
    assert (output / "report.md").is_file()
    assert not (output / "experience.db").exists()
    segments = [json.loads(path.read_text()) for path in (output / "segments").glob("*.json")]
    assert len(segments) == 1 and segments[0]["status"] == "completed"
