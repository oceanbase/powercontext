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

import asyncio
import json

import httpx
import pytest
from bridge import Connection, MemoryIdentity, binding_key, execute
from powercontext.client import PowerContextClient, ServerResponseError

CONNECTION = Connection(base_url="https://memory.example", token="credential", namespace="workspace-1")
IDENTITY = MemoryIdentity(app_id="app-1", subject_id="user-1")
SCOPE = {
    "scope_id": "server-scope",
    "title": "Scope",
    "summary": "Integration scope",
    "context_references": [],
    "external_references": [],
    "version": 1,
}


def test_host_identity_resolves_scope_and_preserves_prepare_contract():
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        assert request.headers["authorization"] == "Bearer credential"
        if request.url.path.endswith("scope-bindings/resolve"):
            assert body["allow_default"] is False
            assert body["binding_keys"] == [binding_key("workspace-1", IDENTITY).model_dump()]
            return httpx.Response(200, json=SCOPE)
        assert request.url.path == "/v1/context/prepare"
        assert body == {
            "scope_id": "server-scope",
            "query": "help",
            "max_bytes": 2000,
            "assembly": {"format": "markdown", "sections": [], "show": []},
        }
        return httpx.Response(
            200,
            json={"schema": "powercontext.prepared-context.v1", "status": "empty", "content": None, "content_bytes": 0},
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = PowerContextClient(CONNECTION.base_url, token="credential", http_client=http)
            result = await execute(
                client,
                CONNECTION,
                IDENTITY,
                "prepare_context",
                {"query": "help", "max_bytes": 2000, "assembly": {"sections": []}},
            )
            assert result["status"] == "empty"

    asyncio.run(scenario())


def test_model_cannot_override_scope():
    async def scenario():
        async with PowerContextClient(CONNECTION.base_url) as client:
            with pytest.raises(ValueError, match="host setting"):
                await execute(client, CONNECTION, IDENTITY, "prepare_context", {"query": "help", "scope_id": "other"})

    asyncio.run(scenario())


def test_missing_binding_does_not_fall_back_or_write():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(404, json={"error": {"code": "not_found", "message": "Missing binding"}})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(ServerResponseError):
                await execute(
                    PowerContextClient(CONNECTION.base_url, http_client=http),
                    CONNECTION,
                    IDENTITY,
                    "prepare_context",
                    {"query": "help"},
                )

    asyncio.run(scenario())
    assert paths == ["/v1/scope-bindings/resolve"]


def test_binding_identity_separates_workspaces_apps_and_subjects():
    keys = {
        binding_key(namespace, MemoryIdentity(app_id=app, subject_kind=kind, subject_id=subject)).external_id
        for namespace in ("workspace-1", "workspace-2")
        for app in ("app-1", "app-2")
        for kind in ("user", "business")
        for subject in ("one", "two")
    }
    assert len(keys) == 16


def test_capture_is_bounded_redacted_and_has_stable_retry_identity():
    captures = []

    def handler(request):
        if request.url.path.endswith("scope-bindings/resolve"):
            return httpx.Response(200, json=SCOPE)
        assert request.url.path == "/v1/sources/content"
        body = json.loads(request.content)
        captures.append(body)
        return httpx.Response(
            202,
            json={"status": "accepted", "source": {"name": "content", "source_id": body["source_id"]}, "position": 1},
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = PowerContextClient(CONNECTION.base_url, http_client=http)
            observation = {
                "event_id": "run-1:1",
                "event": "tool_result",
                "sequence": 1,
                "payload": {"arguments": '{"apiKey":"private-credential"}', "result": "中" * 2000},
                "metadata": {"secret": "leaky-metadata"},
                "max_bytes": 512,
            }
            for _ in range(2):
                result = await execute(client, CONNECTION, IDENTITY, "capture_event", observation)
                assert result["status"] == "accepted"

    asyncio.run(scenario())
    assert captures[0]["source_id"] == captures[1]["source_id"]
    assert len(captures[0]["content"].encode()) <= 512
    assert "private-credential" not in json.dumps(captures)
    assert "leaky-metadata" not in json.dumps(captures)
    assert json.loads(captures[0]["content"])["truncated"] is True
