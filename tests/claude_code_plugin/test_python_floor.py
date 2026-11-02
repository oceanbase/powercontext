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

"""Claude Code runs the plugin with whatever ``python3`` is on ``PATH``.

On macOS that is often the system Python 3.9, so the plugin entry points must
run there with only the standard library. CI installs Python 3.9 and passes
``--require-claude-code-python-floor``, so the test fails there instead of
skipping.

The entry points fail open, so an error on Python 3.9 would still exit 0. Each
test runs one entry point against a local stand-in for the Server and checks
the output of its normal path.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterator, Mapping
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from shutil import which
from threading import Thread
from typing import NoReturn

import pytest

from tests.claude_code_plugin.conftest import PLUGIN_ROOT

_SCOPE_ID = "scope:python-floor"
_BOOTSTRAP_CONTENT = "bootstrap context on Python 3.9"
_RECALL_CONTENT = "recalled context on Python 3.9"
_RESPONSES: dict[str, tuple[int, object]] = {
    "/v1/scope-bindings/resolve": (200, {"scope_id": _SCOPE_ID}),
    "/v1/context/bootstrap": (
        200,
        {
            "schema": "powercontext.bootstrap-context.v1",
            "status": "ready",
            "reason": None,
            "profile": "powercontext.scope-bootstrap.v1",
            "content": _BOOTSTRAP_CONTENT,
            "content_bytes": len(_BOOTSTRAP_CONTENT.encode()),
            "package_digest": "sha256:" + sha256(_BOOTSTRAP_CONTENT.encode()).hexdigest(),
            "items": [
                {
                    "kind": "atomic-memory",
                    "scope_id": _SCOPE_ID,
                    "artifact": {"family": "atomic-memory", "artifact_id": "memory", "revision": 1},
                    "content_digest": "sha256:" + "a" * 64,
                    "truncated": False,
                }
            ],
            "truncated": False,
            "receipt": {"receipt_id": "bcr_python_floor", "state": "pending"},
        },
    ),
    "/v1/context/bootstrap/receipts": (200, {"receipt_id": "bcr_python_floor", "state": "injected"}),
    "/v1/context/prepare": (
        200,
        {
            "schema": "powercontext.prepared-context.v1",
            "status": "ready",
            "content": _RECALL_CONTENT,
            "content_bytes": len(_RECALL_CONTENT.encode()),
        },
    ),
    "/v1/sources/content": (202, {"status": "accepted", "position": 1}),
    "/v1/stats": (
        200,
        {
            "recall": {
                "totals": {
                    "preparations": 5,
                    "ready_preparations": 4,
                    "comparable_preparations": 3,
                    "baseline_tokens": 3_000,
                    "recalled_tokens": 1_800,
                    "token_reduction": 1_200,
                }
            }
        },
    ),
}


def _unavailable(request: pytest.FixtureRequest, reason: str) -> NoReturn:
    if request.config.getoption("--require-claude-code-python-floor"):
        pytest.fail(reason)
    pytest.skip(reason)


@pytest.fixture(scope="module")
def python_floor(request: pytest.FixtureRequest) -> str:
    uv = which("uv")
    if uv is None:
        _unavailable(request, "uv is required to locate a Python 3.9 interpreter")
    # `uv python find` only searches installed interpreters; it never downloads one.
    result = subprocess.run(
        [uv, "python", "find", "--no-project", "3.9"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        _unavailable(request, "no Python 3.9 interpreter is installed")
    return result.stdout.strip()


@pytest.fixture
def server() -> Iterator[tuple[str, list[str]]]:
    paths: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            self.rfile.read(int(self.headers["Content-Length"]))
            paths.append(self.path)
            if self.path not in _RESPONSES:
                self.send_error(404)
                return
            status, payload = _RESPONSES[self.path]
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - BaseHTTPRequestHandler override.
            pass

    stand_in = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=stand_in.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{stand_in.server_port}", paths
    finally:
        stand_in.shutdown()
        thread.join()
        stand_in.server_close()


def _run(python: str, entry_point: str, payload: Mapping[str, object], *, server_url: str, home: Path) -> str:
    environment = {
        **{name: value for name, value in os.environ.items() if not name.startswith(("CLAUDE_", "POWERCONTEXT_"))},
        "HOME": str(home),
        "CLAUDE_CONFIG_DIR": str(home / "claude"),
        "CLAUDE_PLUGIN_DATA": str(home / "plugin-data"),
        "POWERCONTEXT_CLIENT_CONFIG_FILE": str(home / "client.toml"),
        "POWERCONTEXT_DIAGNOSTIC_STATE_FILE": str(home / "diagnostics.json"),
        "POWERCONTEXT_CLAUDE_SERVER_URL": server_url,
    }
    # -S leaves out site-packages, so the entry point can import only the standard library.
    result = subprocess.run(
        [python, "-S", str(PLUGIN_ROOT / entry_point)],
        input=json.dumps(payload),
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_session_start_injects_bootstrap_context_on_python_3_9(
    python_floor: str, server: tuple[str, list[str]], tmp_path: Path
) -> None:
    server_url, _ = server
    payload = {
        "hook_event_name": "SessionStart",
        "source": "startup",
        "session_id": "python-floor",
        "cwd": str(tmp_path),
    }

    output = _run(python_floor, "hooks/session_start.py", payload, server_url=server_url, home=tmp_path)

    assert json.loads(output)["hookSpecificOutput"]["additionalContext"] == _BOOTSTRAP_CONTENT


def test_prompt_hook_recalls_and_captures_on_python_3_9(
    python_floor: str, server: tuple[str, list[str]], tmp_path: Path
) -> None:
    server_url, paths = server
    payload = {
        "hook_event_name": "UserPromptSubmit",
        "prompt": "hello",
        "session_id": "python-floor",
        "cwd": str(tmp_path),
    }

    output = json.loads(
        _run(python_floor, "hooks/user_prompt_submit.py", payload, server_url=server_url, home=tmp_path)
    )

    assert _RECALL_CONTENT in output["hookSpecificOutput"]["additionalContext"]
    # A failed capture would surface as a diagnostic in systemMessage.
    assert "systemMessage" not in output
    assert "/v1/sources/content" in paths


def test_statusline_shows_token_savings_on_python_3_9(
    python_floor: str, server: tuple[str, list[str]], tmp_path: Path
) -> None:
    server_url, _ = server
    payload = {"session_id": "python-floor", "workspace": {"current_dir": str(tmp_path)}}

    output = _run(python_floor, "scripts/statusline.py", payload, server_url=server_url, home=tmp_path)

    assert "PC online" in output
    assert "saved 1.2k today" in output
