# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
from contextlib import asynccontextmanager

import httpx
import pytest
from click.utils import strip_ansi
from typer.testing import CliRunner

import powercontext.client.cli as client_cli
from powercontext.cli.app import create_cli
from powercontext.client import PowerContextClient


@pytest.mark.parametrize("command", ["status", "block", "retry"])
@pytest.mark.parametrize("json_output", [False, True])
@pytest.mark.parametrize("imported", [False, True])
def test_topic_processing_cli_preserves_exact_block_identity_and_diagnostics(
    monkeypatch, command, json_output, imported
):
    block = {
        "block_id": "c6561f56-1a60-4888-adf3-14b341ce66d5",
        "attempt_id": "59527d38-c24d-4396-a595-32116ddc5c1f",
        "scope_id": "project",
        "binding_name": "topic-memory-source-window",
        "source_after": 2,
        "source_through": None if imported else 3,
        "budget_source_through": None if imported else 3,
        "legacy_source_through": 3 if imported else None,
        "target_source_through": 7,
        "attempts": 3,
        "requests": 9,
        "tokens": 1000,
        "error_code": "window_attempt_limit",
        "last_error_code": "model_timeout",
        "blocked_at": "2026-10-13T01:00:00Z",
        "retried_at": None,
        "retry_request_generation": None,
    }
    responses = {
        "status": {
            "state": "blocked",
            "source_cursor": 2,
            "source_head": 9,
            "target_source_through": 7,
            "block": block,
        },
        "block": block,
        "retry": {
            "status": "accepted",
            "block": block | {"retried_at": "2026-10-13T01:01:00Z", "retry_request_generation": 8},
        },
    }
    paths = {
        "status": "/v1/topic-memory/processing/status",
        "block": "/v1/topic-memory/processing/blocks/get",
        "retry": "/v1/topic-memory/processing/retry",
    }
    received = []

    def respond(request):
        received.append((request.method, request.url.path, json.loads(request.content)))
        return httpx.Response(200, json=responses[command])

    @asynccontextmanager
    async def client_factory(server_url, **kwargs):
        async with (
            httpx.AsyncClient(transport=httpx.MockTransport(respond)) as transport,
            PowerContextClient(server_url, http_client=transport, **kwargs) as client,
        ):
            yield client

    monkeypatch.setattr(client_cli, "PowerContextClient", client_factory)
    arguments = (["--json"] if json_output else []) + ["topic-memory", command, "--scope-id", "project"]
    payload = {"scope_id": "project"}
    if command != "status":
        arguments += ["--block-id", block["block_id"]]
        payload["block_id"] = block["block_id"]
    result = CliRunner().invoke(create_cli([]), arguments)
    assert result.exit_code == 0, result.output
    assert received == [("POST", paths[command], payload)]
    assert json.loads(result.output) == responses[command]


def test_retry_cli_requires_an_explicit_observed_block_id():
    result = CliRunner().invoke(create_cli([]), ["topic-memory", "retry", "--scope-id", "project"])
    assert result.exit_code == 2
    assert "--block-id" in strip_ansi(result.output)
