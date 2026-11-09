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
import json
import multiprocessing
import os
from multiprocessing.process import BaseProcess
from pathlib import Path
from unittest.mock import patch

import httpx2
import pytest
from pydantic_ai.exceptions import UserError
from pydantic_ai.providers.openai_codex import OpenAICodexCredentials

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, InferenceConfig, open_builtin_runtime


def _write_login(path: Path, token: str, account: str) -> bytes:
    data = json.dumps({"tokens": {"access_token": token, "refresh_token": "single-use-grant", "account_id": account}})
    replacement = path.with_suffix(".new")
    replacement.write_text(data)
    replacement.replace(path)
    return path.read_bytes()


def _probe(path: Path, worker: str, connection=None) -> tuple[str, list[tuple[str, str, str]]]:
    """Run the real runtime composition; replace only external provider transport and login path."""
    requests = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append((
            str(request.url),
            request.headers.get("Authorization", ""),
            request.headers.get("chatgpt-account-id", ""),
        ))
        if request.url.host != "chatgpt.com":
            return httpx2.Response(400, json={"error": "invalid_grant"})
        if request.headers.get("Authorization") != "Bearer renewed-access":
            return httpx2.Response(401, json={"error": {"message": "expired", "type": "authentication_error"}})
        item = {
            "type": "message",
            "id": "msg-1",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "ok", "annotations": []}],
        }
        response = {
            "id": "resp-1",
            "object": "response",
            "created_at": 1,
            "model": "test-codex",
            "status": "completed",
            "output": [item],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }
        events = [
            {"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
            {"type": "response.output_item.added", "output_index": 0, "item": {**item, "content": []}},
            {
                "type": "response.content_part.added",
                "output_index": 0,
                "content_index": 0,
                "item_id": "msg-1",
                "part": {"type": "output_text", "text": "", "annotations": []},
            },
            {
                "type": "response.output_text.delta",
                "output_index": 0,
                "content_index": 0,
                "item_id": "msg-1",
                "delta": "ok",
            },
            {"type": "response.output_item.done", "output_index": 0, "item": item},
            {"type": "response.completed", "response": response},
        ]
        content = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, text=content)

    class LocalClient(httpx2.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx2.MockTransport(respond)
            super().__init__(*args, **kwargs)

    getenv = os.getenv

    async def scenario() -> str:
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{path.parent / (worker + '.db')}"),
            inference=InferenceConfig(generation_model="openai-codex:test-codex"),
        )
        async with open_builtin_runtime(config) as runtime:
            if connection is not None:
                connection.send("ready")
                assert connection.recv() == "probe"
            return (await runtime.readiness()).checks["inference.generation"]

    with (
        patch("httpx2.AsyncClient", LocalClient),
        patch(
            "os.getenv",
            side_effect=lambda name, *args: str(path.parent) if name == "CODEX_HOME" else getenv(name, *args),
        ),
        # Guard against reading the real CLI store when testing the old default-provider path.
        patch(
            "pydantic_ai.providers.openai_codex._read_codex_cli_credentials",
            return_value=OpenAICodexCredentials.from_codex_cli_auth(json.loads(path.read_text())),
        ),
    ):
        return asyncio.run(scenario()), requests


def _worker(path: Path, name: str, connection) -> None:
    try:
        connection.send(_probe(path, name, connection))
    finally:
        connection.close()


# Every worker re-imports this module in a fresh interpreter, and the CI matrix shares two cores among eight pytest
# workers, so the exit can trail the reply it already sent by more than ten seconds. A slow exit is not what this test
# is about; a worker that never exits still fails the assertion below.
_WORKER_EXIT_SECONDS = 60


def _require_clean_exit(process: BaseProcess, worker: str) -> None:
    process.join(_WORKER_EXIT_SECONDS)
    assert process.exitcode == 0, f"{worker} exited with {process.exitcode}"


@pytest.mark.parametrize("access_token", ["expired-access", "e30.eyJleHAiOjB9.signature"])
def test_codex_rejected_credentials_do_not_consume_the_cli_refresh_grant(tmp_path: Path, access_token: str) -> None:
    path = tmp_path / "auth.json"
    original = _write_login(path, access_token, "old-account")
    status, requests = _probe(path, "expired")
    assert status == "misconfigured: provider-rejected (HTTP 401)"
    assert requests == [("https://chatgpt.com/backend-api/codex/responses", f"Bearer {access_token}", "old-account")]
    assert path.read_bytes() == original


def test_codex_workers_and_restart_observe_cli_renewal(tmp_path: Path) -> None:
    path = tmp_path / "auth.json"
    _write_login(path, "expired-access", "old-account")
    context = multiprocessing.get_context("spawn")
    workers = []
    try:
        for index in range(2):
            parent, child = context.Pipe()
            process = context.Process(target=_worker, args=(path, f"worker-{index}", child))
            process.start()
            child.close()
            workers.append((process, parent))
        for _, connection in workers:
            assert connection.poll(30), "worker did not start"
            assert connection.recv() == "ready"
        current = _write_login(path, "renewed-access", "renewed-account")
        for _, connection in workers:
            connection.send("probe")
        for index, (process, connection) in enumerate(workers):
            assert connection.poll(30), "worker did not finish"
            status, requests = connection.recv()
            assert status == "ready"
            assert requests == [
                ("https://chatgpt.com/backend-api/codex/responses", "Bearer renewed-access", "renewed-account")
            ]
            _require_clean_exit(process, f"worker {index}")
        status, requests = _probe(path, "restart")
        assert status == "ready"
        assert requests == [
            ("https://chatgpt.com/backend-api/codex/responses", "Bearer renewed-access", "renewed-account"),
        ]
        assert path.read_bytes() == current
    finally:
        for process, connection in workers:
            if process.is_alive():
                process.terminate()
                process.join(10)
            connection.close()


@pytest.mark.parametrize("content", ["not-json-secret", '{"tokens":{"access_token":"secret-value"}}'])
def test_codex_invalid_login_does_not_expose_content(tmp_path: Path, content: str) -> None:
    from powercontext.builtin.inference.codex import CodexCliAuth

    path = tmp_path / "auth.json"
    path.write_text(content)

    async def scenario() -> None:
        async with httpx2.AsyncClient(
            auth=CodexCliAuth(path), transport=httpx2.MockTransport(lambda _: pytest.fail("sent"))
        ) as client:
            with pytest.raises(UserError, match="Codex CLI credentials are unavailable or malformed") as error:
                await client.get("https://chatgpt.com/backend-api/codex/responses")
            assert "secret" not in str(error.value)
            assert str(path) not in str(error.value)

    asyncio.run(scenario())


def test_codex_auth_observes_renewal_between_requests(tmp_path: Path) -> None:
    from powercontext.builtin.inference.codex import CodexCliAuth

    path = tmp_path / "auth.json"
    sent = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        sent.append((request.headers["Authorization"], request.headers["chatgpt-account-id"]))
        return httpx2.Response(200)

    async def scenario() -> None:
        async with httpx2.AsyncClient(auth=CodexCliAuth(path), transport=httpx2.MockTransport(respond)) as client:
            for token, account in [("old-access", "old-account"), ("renewed-access", "renewed-account")]:
                stored = _write_login(path, token, account)
                await client.get("https://chatgpt.com/backend-api/codex/responses")
                assert path.read_bytes() == stored

    asyncio.run(scenario())
    assert sent == [("Bearer old-access", "old-account"), ("Bearer renewed-access", "renewed-account")]
