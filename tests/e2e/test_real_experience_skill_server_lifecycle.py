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

"""Fixture startup deadlines must not abandon live ASGI resources."""

import asyncio
import threading
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from tests.e2e.real_experience_skill import harness
from tests.e2e.real_experience_skill.settings import IsolatedServerSettings


@pytest.mark.parametrize("launcher", ["baseline", "configured"])
def test_startup_timeout_waits_for_initialization_and_lifespan_cleanup(tmp_path, monkeypatch, launcher):
    entered = threading.Event()
    initialized = threading.Event()
    cleaned = threading.Event()
    threads = []
    errors = []

    def initialize():
        time.sleep(0.15)
        initialized.set()

    @asynccontextmanager
    async def lifespan(app):
        try:
            entered.set()
            # Match the Server's synchronous database initialization boundary.
            await asyncio.to_thread(initialize)
            yield
        finally:
            cleaned.set()

    def thread_factory(*args, **kwargs):
        thread = threading.Thread(*args, **kwargs)
        threads.append(thread)
        return thread

    clock_reads = 0

    def monotonic():
        nonlocal clock_reads
        assert entered.wait(5)
        clock_reads += 1
        # Only advance the launcher's deadline; Uvicorn and the ASGI app use real time.
        return time.monotonic() + (100 if clock_reads > 1 else 0)

    monkeypatch.setattr(harness, "create_server_app", lambda **kwargs: FastAPI(lifespan=lifespan))
    monkeypatch.setattr(harness, "threading", SimpleNamespace(Thread=thread_factory))
    monkeypatch.setattr(harness, "time", SimpleNamespace(monotonic=monotonic, sleep=time.sleep))
    monkeypatch.setattr(threading, "excepthook", lambda args: errors.append(args.exc_value))
    try:
        with pytest.raises(harness.RealCodexE2EError, match="did not start"):
            if launcher == "baseline":
                harness._start_server(tmp_path / "runtime.db", tmp_path / "scheduler.db")
            else:
                harness._start_configured_server(IsolatedServerSettings(), tmp_path / "scheduler.db")
        assert all(not thread.is_alive() for thread in threads)
        assert initialized.is_set()
        assert cleaned.is_set()
        assert not errors
    finally:
        # Keep an intentionally broken launcher from leaking into the next test.
        for thread in threads:
            thread.join(timeout=5)


def test_server_stop_waits_for_lifespan_resources(tmp_path, monkeypatch):
    cleaned = threading.Event()

    @asynccontextmanager
    async def lifespan(app):
        yield
        await asyncio.to_thread(time.sleep, 0.15)
        cleaned.set()

    monkeypatch.setattr(harness, "create_server_app", lambda **kwargs: FastAPI(lifespan=lifespan))
    server = harness._start_configured_server(IsolatedServerSettings(), tmp_path / "scheduler.db")
    server.stop()
    assert cleaned.is_set()
    assert not server.thread.is_alive()
    assert server.listener.fileno() == -1
