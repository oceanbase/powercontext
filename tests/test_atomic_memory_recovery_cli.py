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
def test_atomic_processing_cli_preserves_exact_block_identity_and_diagnostics(monkeypatch, command, json_output):
    block = {
        "block_id": "c6561f56-1a60-4888-adf3-14b341ce66d5",
        "scope_id": "project",
        "binding_name": "memory-source-window",
        "source_after": 2,
        "source_through": 3,
        "input_fingerprint": "a" * 64,
        "estimated_input_tokens": 55823,
        "effective_budget": 48000,
        "token_estimator_id": "character:weighted",
        "failure_location": "atomic_memory.extract",
        "error_code": "input_budget_exceeded",
        "blocked_at": "2026-10-13T01:00:00Z",
        "retried_at": None,
        "retry_request_generation": None,
    }
    responses = {
        "status": {
            "state": "blocked",
            "source_cursor": 2,
            "source_head": 9,
            "block": block,
        },
        "block": block,
        "retry": {
            "status": "accepted",
            "block": block | {"retried_at": "2026-10-13T01:01:00Z", "retry_request_generation": 8},
        },
    }
    paths = {
        "status": "/v1/atomic-memory/processing/status",
        "block": "/v1/atomic-memory/processing/blocks/get",
        "retry": "/v1/atomic-memory/processing/retry",
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
    arguments = (["--json"] if json_output else []) + ["atomic-memory", command, "--scope-id", "project"]
    payload = {"scope_id": "project"}
    if command != "status":
        arguments += ["--block-id", block["block_id"]]
        payload["block_id"] = block["block_id"]
    result = CliRunner().invoke(create_cli([]), arguments)
    assert result.exit_code == 0, result.output
    assert received == [("POST", paths[command], payload)]
    assert json.loads(result.output) == responses[command]


def test_retry_cli_requires_an_explicit_observed_block_id():
    result = CliRunner().invoke(create_cli([]), ["atomic-memory", "retry", "--scope-id", "project"])
    assert result.exit_code == 2
    assert "--block-id" in strip_ansi(result.output)
