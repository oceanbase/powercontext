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

"""Run HTTP/MCP and read-only lease checks inside a test API Pod."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects import registry
from sqlalchemy.ext.asyncio import create_async_engine


def request(base: str, path: str, body: dict[str, Any] | None = None, *, authenticated: bool = True):
    headers = {"Accept": "application/json, text/event-stream"}
    if authenticated:
        headers["Authorization"] = "Bearer " + os.environ["POWERCONTEXT_SERVER_AUTH_TOKEN"]
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(  # noqa: S310 -- URL comes from the test Service.
        base + path, data=None if body is None else json.dumps(body).encode(), headers=headers
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:  # noqa: S310
            raw = response.read().decode()
            if raw.startswith(("event:", "data:")):
                raw = next(line[6:] for line in raw.splitlines() if line.startswith("data: "))
            return response.status, json.loads(raw), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, {}, {}


def create_scope(payload: dict[str, str]):
    base = payload["base"]
    assert request(base, "/v1/scopes", authenticated=False)[0] == 401, "HTTP must require authentication"
    rpc = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "helm-acceptance", "version": "1"},
        },
    }
    assert request(base, "/mcp/", rpc, authenticated=False)[0] == 401, "MCP must require authentication"
    status, result, headers = request(base, "/mcp/", rpc)
    assert status == 200 and "result" in result, "MCP initialization failed"
    assert not any(key.lower() == "mcp-session-id" for key in headers), "MCP must be stateless"
    assert request(base, "/mcp/", {"jsonrpc": "2.0", "id": 2, "method": "ping"})[0] == 200
    status, scope, _ = request(
        base,
        "/v1/scopes",
        {
            "title": "Helm acceptance",
            "summary": "Disposable deployment persistence check",
            "idempotency_key": payload["key"],
        },
    )
    assert status == 201, "Scope creation failed"
    return scope


def read_scope(payload: dict[str, str]):
    status, scope, _ = request(payload["base"], "/v1/scopes/" + payload["scope"])
    assert status == 200 and scope["scope_id"] == payload["scope"], "Scope did not survive API replacement"
    status, result, _ = request(
        payload["base"],
        "/mcp/",
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "get_scope", "arguments": {"scope_id": payload["scope"]}},
        },
    )
    assert status == 200 and "result" in result and not result["result"].get("isError", False), "MCP get_scope failed"
    assert payload["scope"] in json.dumps(result["result"]), "MCP returned the wrong Scope"
    return scope


async def read_lease():
    registry.register("mysql.aoceanbase", "pyobvector", "AsyncOceanBaseDialect")
    engine = create_async_engine(os.environ["POWERCONTEXT_SERVER_DATABASE_URL"])
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text(
                    "SELECT holder_id, supervisor_generation FROM pc_artifact_processing_leases "
                    "WHERE supervisor_group='global' AND lease_expires_at > UTC_TIMESTAMP()"
                )
            )
            row = result.first()
            return None if row is None else list(row)
    finally:
        await engine.dispose()


def main() -> None:
    payload = json.loads(sys.argv[1])
    if payload["action"] == "lease":
        result = asyncio.run(read_lease())
    elif payload["action"] == "create":
        result = create_scope(payload)
    else:
        result = read_scope(payload)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
