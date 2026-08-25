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

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from types import TracebackType
from typing import cast

import pytest
from pydantic import ValidationError
from sqlalchemy import Table
from sqlalchemy.engine import URL, make_url
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.ext.asyncio import AsyncEngine

from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.seekdb import SeekDBConfig, SeekDBProfile
from powercontext.builtin.persistence.seekdb import profile as seekdb_profile_module


class _Begin(AbstractAsyncContextManager[object]):
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class _TrackedEngine:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def begin(self) -> _Begin:
        return _Begin()

    async def dispose(self) -> None:
        self.events.append("engine.dispose")


class _SeekDBInstance:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def connection_options(self) -> dict[str, object]:
        return {"user": "root", "unix_socket": "seekdb.sock"}

    def close(self) -> None:
        self.events.append("instance.close")


class _SeekDBModule:
    def __init__(self, instance: _SeekDBInstance, events: list[str]) -> None:
        self.instance = instance
        self.events = events

    async def aopen(self, path: str) -> _SeekDBInstance:
        self.events.append(f"aopen:{path}")
        return self.instance


class _TerminatingConnection:
    def __init__(self) -> None:
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True


def test_config_requires_an_explicit_non_empty_path() -> None:
    with pytest.raises(ValidationError, match="path"):
        SeekDBConfig.model_validate({})
    with pytest.raises(ValidationError, match="seekDB path must not be empty"):
        SeekDBConfig.model_validate({"path": ""})


def test_dialect_terminates_connections_on_close() -> None:
    connection = _TerminatingConnection()

    seekdb_profile_module.AsyncSeekDBDialect().do_close(cast(DBAPIConnection, connection))

    assert connection.terminated


def test_engine_uses_the_local_socket(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    expected_engine = cast(AsyncEngine, object())

    def create_engine(url: object, **options: object) -> AsyncEngine:
        captured["url"] = url
        captured.update(options)
        return expected_engine

    monkeypatch.setattr(seekdb_profile_module, "create_async_engine", create_engine)
    monkeypatch.setattr(seekdb_profile_module, "_register_seekdb_dialect", lambda: None)

    engine = seekdb_profile_module._create_engine(
        SeekDBConfig(path=tmp_path),
        {"user": "root", "unix_socket": "seekdb.sock"},
    )

    captured_url = captured["url"]
    assert isinstance(captured_url, (str, URL))
    url = make_url(captured_url)
    assert engine is expected_engine
    assert url.drivername == "mysql+aseekdb"
    assert url.username == "root"
    assert url.host == "localhost"
    assert url.database == "test"
    assert url.query["charset"] == "utf8mb4"
    assert captured["connect_args"] == {
        "init_command": "SET autocommit = 0",
        "unix_socket": "seekdb.sock",
    }
    assert captured["hide_parameters"] is True


def test_profile_closes_engine_before_instance(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        events: list[str] = []
        instance = _SeekDBInstance(events)
        module = _SeekDBModule(instance, events)
        engine = _TrackedEngine(events)

        async def create_no_tables(_connection: object, _tables: tuple[Table, ...]) -> None:
            return None

        monkeypatch.setattr(seekdb_profile_module, "_load_binding", lambda: module)
        monkeypatch.setattr(seekdb_profile_module, "_create_engine", lambda _config, _options: engine)
        monkeypatch.setattr(seekdb_profile_module, "create_tables", create_no_tables)

        async with SeekDBProfile.open(SeekDBConfig(path=tmp_path / "seekdb"), tables=()):
            pass

        assert events == [f"aopen:{(tmp_path / 'seekdb').resolve()}", "engine.dispose", "instance.close"]

    asyncio.run(scenario())


def test_profile_finishes_shutdown_before_propagating_repeated_cancellation(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        events: list[str] = []
        close_started = asyncio.Event()
        transaction_started = asyncio.Event()
        release_transaction = asyncio.Event()
        instance = _SeekDBInstance(events)
        module = _SeekDBModule(instance, events)
        engine = _TrackedEngine(events)
        original_close = AsyncDatabase.close

        async def create_no_tables(_connection: object, _tables: tuple[Table, ...]) -> None:
            return None

        async def observe_close(database: AsyncDatabase) -> None:
            close_started.set()
            await original_close(database)

        monkeypatch.setattr(seekdb_profile_module, "_load_binding", lambda: module)
        monkeypatch.setattr(seekdb_profile_module, "_create_engine", lambda _config, _options: engine)
        monkeypatch.setattr(seekdb_profile_module, "create_tables", create_no_tables)
        monkeypatch.setattr(AsyncDatabase, "close", observe_close)

        context = SeekDBProfile.open(SeekDBConfig(path=tmp_path / "seekdb"), tables=())
        profile = await context.__aenter__()

        async def hold_transaction() -> None:
            async with profile.database.transaction():
                events.append("transaction.active")
                transaction_started.set()
                await release_transaction.wait()
            events.append("transaction.complete")

        transaction_task = asyncio.create_task(hold_transaction())
        await transaction_started.wait()
        shutdown_task = asyncio.create_task(context.__aexit__(None, None, None))
        await close_started.wait()

        shutdown_task.cancel("first")
        await asyncio.sleep(0)
        shutdown_task.cancel("second")
        await asyncio.sleep(0)

        assert not shutdown_task.done()
        assert shutdown_task.cancelling() == 2
        assert "instance.close" not in events

        release_transaction.set()
        await transaction_task
        with pytest.raises(asyncio.CancelledError) as cancellation:
            await shutdown_task

        assert cancellation.value.args == ("first",)
        assert shutdown_task.cancelled()
        assert events == [
            f"aopen:{(tmp_path / 'seekdb').resolve()}",
            "transaction.active",
            "transaction.complete",
            "engine.dispose",
            "instance.close",
        ]

    asyncio.run(scenario())


def test_profile_closes_instance_if_open_is_cancelled_repeatedly(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        events: list[str] = []
        started = asyncio.Event()
        finish = asyncio.Event()
        instance = _SeekDBInstance(events)

        class DelayedModule:
            async def aopen(self, path: str) -> _SeekDBInstance:
                events.append(f"aopen:{path}")
                started.set()
                await finish.wait()
                return instance

        monkeypatch.setattr(seekdb_profile_module, "_load_binding", DelayedModule)

        async def open_profile() -> None:
            async with SeekDBProfile.open(SeekDBConfig(path=tmp_path / "seekdb"), tables=()):
                pass

        task = asyncio.create_task(open_profile())
        await started.wait()
        task.cancel("first")
        await asyncio.sleep(0)
        task.cancel("second")
        await asyncio.sleep(0)

        assert not task.done()
        assert task.cancelling() == 2

        finish.set()
        with pytest.raises(asyncio.CancelledError) as cancellation:
            await task

        assert cancellation.value.args == ("first",)
        assert task.cancelled()
        assert events == [f"aopen:{(tmp_path / 'seekdb').resolve()}", "instance.close"]

    asyncio.run(scenario())
