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

"""Observable hook behavior, including cancellation and untrusted server output."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import time

import httpx
import pytest

from powercontext.client import PowerContextClient
from powercontext.client.integration.core import execute
from powercontext.client.integration.models import Connection, HookRequest
from powercontext.http import GetHandoffReportRequest, ReportFormat


def prepare_request(**changes) -> HookRequest:
    fields = {
        "id": "request-1",
        "operation": "prepare_context",
        "arguments": {"scope_id": "scope:one", "query": "context", "max_bytes": 512},
        "connection": Connection(base_url="http://127.0.0.1:8000"),
        "deadline": time() + 5,
    }
    return HookRequest.model_validate(fields | changes)


@pytest.mark.parametrize(
    "body,expected",
    [
        (
            {"schema": "powercontext.prepared-context.v1", "status": "ready", "content": "hello", "content_bytes": 5},
            "ok",
        ),
        (
            {"schema": "powercontext.prepared-context.v1", "status": "empty", "content": None, "content_bytes": 0},
            "empty",
        ),
        (
            {"schema": "powercontext.prepared-context.v1", "status": "ready", "content": "hello", "content_bytes": 1},
            "failed",
        ),
        (
            {"schema": "powercontext.prepared-context.v1", "status": "empty", "content": "hidden", "content_bytes": 0},
            "failed",
        ),
        (
            {
                "schema": "powercontext.prepared-context.v1",
                "status": "ready",
                "content": "x" * 513,
                "content_bytes": 513,
            },
            "failed",
        ),
    ],
)
def test_context_is_bounded_and_empty_is_not_a_failure(body, expected) -> None:
    async def scenario():
        result = await execute(
            prepare_request(), transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
        )
        assert result.outcome == expected
        if expected == "failed":
            assert result.error == "invalid_response"
            assert result.value is None

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "operation,arguments,expected",
    [
        ("prepare_context", {"scope_id": "one", "query": "hello"}, "failed"),
        ("capture_content_source", {"scope_id": "one", "source_id": "stable", "content": "hello"}, "unknown"),
    ],
)
def test_deadline_cancels_read_and_does_not_claim_a_write_failed(operation, arguments, expected) -> None:
    async def scenario():
        accepted = []

        async def slow(request):
            accepted.append(json.loads(request.content))
            await asyncio.sleep(1)
            return httpx.Response(200, json={})

        result = await execute(
            prepare_request(operation=operation, arguments=arguments, deadline=time() + 0.03),
            transport=httpx.MockTransport(slow),
        )
        assert result.outcome == expected
        assert result.error == "deadline"
        assert len(accepted) == 1

    asyncio.run(scenario())


def test_sdk_downloads_large_reports_and_honors_redirects_while_hooks_remain_bounded() -> None:
    content = b"# Handoff\n" + b"evidence\n" * 150_000

    def respond(request):
        if request.url.path != "/report":
            return httpx.Response(307, headers={"Location": "/report"})
        return httpx.Response(200, content=content, headers={"Content-Type": "text/markdown"})

    async def scenario():
        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(respond), follow_redirects=True) as http,
            PowerContextClient("http://127.0.0.1:8000", http_client=http) as client,
        ):
            request = GetHandoffReportRequest.model_validate({
                "selection": {"mode": "exact", "scope_ids": ["scope:one"]},
                "format": ReportFormat.MARKDOWN,
            })
            assert await client.download_handoff_report(request) == content
        hook_request = prepare_request(
            operation="get_handoff_report",
            arguments=request.model_dump(mode="json") | {"download": True},
        )
        redirected = await execute(hook_request, transport=httpx.MockTransport(respond))
        assert redirected.outcome == "failed"
        assert redirected.status_code == 307
        oversized = await execute(
            hook_request,
            transport=httpx.MockTransport(lambda _: httpx.Response(200, content=content)),
        )
        assert oversized.outcome == "failed"
        assert oversized.body_error == "response_too_large"

    asyncio.run(scenario())


@pytest.mark.parametrize("status,expected", [(202, "unknown"), (401, "failed"), (403, "failed")])
def test_hook_body_limit_preserves_write_outcomes_and_stops_reading(status, expected) -> None:
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(20):
                yield b"x" * 100_000
            pytest.fail("Hook read an unbounded response")

    request = prepare_request(
        operation="capture_content_source",
        arguments={"scope_id": "scope:one", "source_id": "source:one", "content": "hello"},
    )
    result = asyncio.run(
        execute(
            request,
            transport=httpx.MockTransport(lambda _: httpx.Response(status, stream=Body())),
        )
    )
    assert result.outcome == expected
    assert result.status_code == status
    assert result.body_error == "response_too_large"


def test_expired_write_never_reaches_the_server() -> None:
    request = prepare_request(operation="capture_content_source", deadline=time() - 1)
    result = asyncio.run(execute(request))
    assert (result.outcome, result.error) == ("failed", "deadline")


def test_bridge_preserves_scope_per_request_and_never_prints_credentials() -> None:
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(body["scope_id"])
            content = body["scope_id"]
            response = json.dumps({
                "schema": "powercontext.prepared-context.v1",
                "status": "ready",
                "content": content,
                "content_bytes": len(content),
            }).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        frames = []
        for scope in ("scope:one", "scope:two"):
            request = prepare_request(
                id=scope,
                arguments={"scope_id": scope, "query": "hello"},
                connection={"base_url": f"http://127.0.0.1:{server.server_port}", "authorization": "private-token"},
            )
            body = request.model_dump(mode="json")
            body["connection"]["authorization"] = "private-token"
            frames.append(json.dumps(body) + "\n")
        result = subprocess.run(
            [sys.executable, "-m", "powercontext.client.integration.bridge"],
            input="".join(frames),
            text=True,
            capture_output=True,
            timeout=10,
            check=True,
        )
        responses = [json.loads(line) for line in result.stdout.splitlines()]
        assert [value["value"]["content"] for value in responses] == seen == ["scope:one", "scope:two"]
        assert "private-token" not in result.stdout + result.stderr
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("exit_code", [0, 2])
def test_installed_client_runs_native_adapters_with_sibling_imports_and_preserves_io(tmp_path, exit_code) -> None:
    (tmp_path / "helper.py").write_text('VALUE = "sibling"\n')
    script = tmp_path / "native adapter.py"
    script.write_text(
        "import json, sys\n"
        "from helper import VALUE\n"
        "from powercontext.client import PowerContextClient\n"
        "print(json.dumps({'python': sys.executable, 'input': sys.stdin.read(), 'helper': VALUE, 'args': sys.argv[1:]}))\n"
        "sys.stderr.write('native diagnostic\\n')\n"
        f"raise SystemExit({exit_code})\n"
    )
    executable = shutil.which("powercontext-hook")
    assert executable is not None
    result = subprocess.run(
        [executable, "--script", str(script), "--", "--cwd", "project with spaces", "--help"],
        env=dict(os.environ, PATH=str(Path(executable).parent)),
        input="native payload",
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == exit_code
    payload = json.loads(result.stdout)
    assert payload["input"] == "native payload"
    assert payload["helper"] == "sibling"
    assert payload["args"] == ["--cwd", "project with spaces", "--help"]
    assert Path(payload["python"]).resolve() == Path(sys.executable).resolve()
    assert result.stderr == "native diagnostic\n"
    assert not (tmp_path / "__pycache__").exists()
