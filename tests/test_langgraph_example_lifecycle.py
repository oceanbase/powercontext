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
import importlib.util
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn


@pytest.mark.parametrize("startup", ["timeout", "failure"])
def test_example_failed_startup_stops_before_removing_database(monkeypatch, startup):
    path = Path(__file__).resolve().parents[1] / "integrations/langgraph/examples/_local_server.py"
    spec = importlib.util.spec_from_file_location("langgraph_local_server_lifecycle", path)
    assert spec is not None and spec.loader is not None
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)
    entered = threading.Event()
    finished = threading.Event()
    state = {}

    def create_app(*, settings):
        state["directory"] = Path(settings.database.url.removeprefix("sqlite+aiosqlite:///")).parent

        async def app(scope, receive, send):
            assert scope["type"] == "lifespan"
            state["loop"] = asyncio.get_running_loop()
            state["thread"] = threading.current_thread()
            state["release"] = asyncio.Event()
            await receive()
            entered.set()
            try:
                if startup == "failure":
                    await send({"type": "lifespan.startup.failed", "message": "controlled startup failure"})
                    return
                await state["release"].wait()
                await send({"type": "lifespan.startup.complete"})
                await receive()
                await send({"type": "lifespan.shutdown.complete"})
            finally:
                state["database_directory_exists_at_exit"] = state["directory"].is_dir()
                finished.set()

        return app

    def create_server(config):
        state["server"] = uvicorn.Server(config)
        return state["server"]

    def create_socket():
        state["listener"] = socket.socket()
        return state["listener"]

    clock_started = False

    def startup_clock():
        nonlocal clock_started
        advance = clock_started and entered.is_set() and startup == "timeout"
        clock_started = True
        return time.monotonic() + (120 if advance else 0)

    monkeypatch.setattr(example, "create_server_app", create_app)
    monkeypatch.setattr(example, "uvicorn", SimpleNamespace(Server=create_server, Config=uvicorn.Config))
    monkeypatch.setattr(example, "socket", SimpleNamespace(socket=create_socket))
    monkeypatch.setattr(example, "time", SimpleNamespace(monotonic=startup_clock, sleep=time.sleep))
    # Uvicorn reports a rejected lifespan as SystemExit in its real Server thread.
    thread_errors = []
    monkeypatch.setattr(threading, "excepthook", lambda failure: thread_errors.append(failure.exc_value))

    try:
        with (
            pytest.raises(RuntimeError, match="local PowerContext Server did not start"),
            example.local_powercontext_server(),
        ):
            pytest.fail("A failed startup must not expose a Server URL")

        assert finished.is_set(), "The lifespan is still using the temporary database"
        assert not state["thread"].is_alive()
        assert state["database_directory_exists_at_exit"]
        assert not state["directory"].exists()
        assert state["listener"].fileno() == -1
        assert not thread_errors
    finally:
        # Keep the red regression run from leaking its deliberately stalled thread.
        state["server"].should_exit = True
        loop = state.get("loop")
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(state["release"].set)
        if "thread" in state:
            state["thread"].join(timeout=5)
        state["listener"].close()
