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

"""Opt-in acceptance against a real HTTP Server, SQLite, and no model provider."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path

import pytest

from powercontext_eval_longmemeval_v2.adapter import (
    PowerContextHTTPRuntime,
    PowerContextMemory,
    PowerContextMemoryAdapterError,
)

_SERVER = """
import socket
import sys
import uvicorn
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, MetricsConfig, ServerSettings
from pathlib import Path
root = Path(sys.argv[2])
app = create_server_app(
    settings=ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{root / 'receipt.db'}"),
        mcp=McpConfig(enabled=False), metrics=MetricsConfig(enabled=False),
    ),
    scheduler_path=root / "scheduler.db",
)
uvicorn.Server(uvicorn.Config(app, log_level="error")).run(sockets=[socket.socket(fileno=int(sys.argv[1]))])
"""


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("POWERCONTEXT_TEST_RECEIPT_HTTP") != "1", reason="opt-in real Server HTTP acceptance"
)
def test_same_response_receipt_through_real_http_sqlite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        with (tmp_path / "server.log").open("w") as log:
            server = subprocess.Popen(
                [sys.executable, "-c", _SERVER, str(listener.fileno()), str(tmp_path)],
                pass_fds=(listener.fileno(),),
                stdout=log,
                stderr=log,
            )
            try:
                _exercise_server(base_url, tmp_path, server)
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)


def _exercise_server(base_url: str, tmp_path: Path, server: subprocess.Popen) -> None:
    class ObservedHTTPRuntime(PowerContextHTTPRuntime):
        def __init__(self) -> None:
            super().__init__(base_url, token=None, timeout_seconds=1)
            self.responses: list[Mapping[str, object]] = []

        def prepare_context(self, payload: Mapping[str, object]) -> Mapping[str, object]:
            result = super().prepare_context(payload)
            self.responses.append(result)
            return result

    runtime = ObservedHTTPRuntime()
    deadline = time.monotonic() + 30
    while True:
        if server.poll() is not None:
            pytest.fail(f"Server exited before readiness; inspect {tmp_path / 'server.log'}")
        try:
            runtime.get_readiness()
            break
        except PowerContextMemoryAdapterError:
            if time.monotonic() >= deadline:
                pytest.fail(f"Server did not become ready; inspect {tmp_path / 'server.log'}")
            time.sleep(0.05)
    scope = runtime.create_scope(
        {"title": "Synthetic receipt", "summary": "HTTP acceptance", "idempotency_key": "receipt"}
    )
    remembered = runtime.remember_memory(
        {
            "scope_id": scope["scope_id"],
            "kind": "fact",
            "text": "The budget review is on Thursday.",
        }
    )
    audit_path = tmp_path / "audit.jsonl"
    adapter = PowerContextMemory(
        {
            "scope_id": scope["scope_id"],
            "audit_path": str(audit_path),
            "base_url": base_url,
            "query_strategy": "prepared-context",
            "receipt_diagnostics": True,
            "receipt_server_revision": "923e3d4cec8038d0799f6893b0ec8e746c870434",
        }
    )
    adapter.configure_runtime(runtime=runtime)
    adapter.set_query_context(query_invocation_id="synthetic-query-1")
    items = adapter.query("budget review")
    assert len(runtime.responses) == 1
    response = runtime.responses[0]
    [event] = [json.loads(line) for line in audit_path.read_text().splitlines()]
    diagnostics = event["prepared_context_diagnostics"]
    assert diagnostics["receipt_status"] == "available"
    assert diagnostics["used_bytes"] == response["content_bytes"]
    assert items == [{"type": "text", "value": response["content"]}]
    assert diagnostics["content_digest"] == "sha256:" + hashlib.sha256(items[0]["value"].encode()).hexdigest()
    assert diagnostics["selected"][0]["artifact_address"] == {
        "scope_id": scope["scope_id"],
        "artifact": remembered["records"][0]["artifact"],
    }
    assert event["query_invocation_id"] == "synthetic-query-1"
    assert "Thursday" not in audit_path.read_text()
    assert diagnostics["candidate_count"] is None
