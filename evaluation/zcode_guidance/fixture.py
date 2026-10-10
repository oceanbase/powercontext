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

"""Controlled replies and a complete contract-derived MCP catalog, with no domain writes."""

import asyncio
import json
from typing import Any

import jsonschema
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.factory import create_server_app
from powercontext.server.mcp import create_mcp_server
from powercontext.server.settings import ServerSettings

SCOPE = "zcode-guidance-fixture-scope"
CANDIDATE = "zcode-guidance-candidate"
PREFIX = "mcp__plugin_powercontext_powercontext__"


def catalog() -> list[dict[str, Any]]:
    # Construct the same projection as the real Server. No runtime lifespan or persistence is started.
    app = create_server_app(
        settings=ServerSettings(_env_file=None, database=SQLiteConfig(url="sqlite+aiosqlite:///:memory:"))
    )

    async def tools() -> list[dict[str, Any]]:
        server = create_mcp_server(app)
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.parameters,
                "annotations": tool.annotations.model_dump(exclude_none=True) if tool.annotations else {},
            }
            for tool in await server.list_tools()
        ]

    return asyncio.run(tools())


class GuidanceFixture:
    def __init__(self, case: str, tools: list[dict[str, Any]]) -> None:
        self.case = case
        self.tools = tools
        self.turn = 0
        self.calls: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self.version = 1
        self.app = FastAPI()
        self.app.post("/mcp")(self.mcp)
        self.app.get("/health/ready")(lambda: {"status": "ready"})
        self.app.get("/v1/scopes/{scope_id}")(lambda scope_id: self.scope())
        self.app.post("/v1/scope-bindings/resolve")(lambda: {"scope_id": SCOPE})
        self.app.post("/v1/context/prepare")(
            lambda: {
                "schema": "powercontext.prepared-context.v1",
                "status": "empty",
                "content": None,
                "content_bytes": 0,
            }
        )

    def scope(self) -> dict[str, Any]:
        return {
            "scope_id": SCOPE,
            "title": "ZCode guidance fixture",
            "summary": "Synthetic controlled Scope",
            "version": 1,
            "parent_scope_id": None,
            "context_refs": [],
            "external_refs": [],
        }

    def reply(self, name: str, arguments: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        if name == "resolve_scope_binding":
            return {"scope_id": SCOPE}, False
        if name == "get_scope":
            return self.scope(), False
        if arguments.get("scope_id") != SCOPE:
            return {"error": {"code": "FIXTURE_SCOPE_MISMATCH", "message": "Use the resolved synthetic Scope."}}, True
        if name == "search_memory":
            return {"memory": None, "mode": None, "hits": []}, False
        if name == "remember_memory" and self.case == "explicit-save":
            memory = {"family": "memory", "artifact_id": "memory", "revision": 1}
            return {
                "memory": memory,
                "entry": {
                    "citation": {
                        "memory_ref": memory,
                        "entry_id": "fixture-entry",
                        "entry_version_id": "fixture-version",
                    },
                    "version": 1,
                    "kind": "decision",
                    "text": arguments["text"],
                    "state": "active",
                    "source_refs": [],
                    "artifact_refs": [],
                },
            }, False
        if name == "remember_memory":
            return {
                "error": {"code": "FIXTURE_WRITE_DENIED", "message": "Synthetic save was denied; nothing was saved."}
            }, True
        if name == "get_artifact_candidate":
            return {
                "candidate_id": CANDIDATE,
                "version": self.version,
                "family": "experience",
                "status": "pending",
                "proposal": {
                    "situation": "A proposal changes during review.",
                    "action": "Review its exact version.",
                    "outcome": "No unreviewed content is approved.",
                    "lesson": "Approve only the version you reviewed."
                    if self.version == 1
                    else "Re-read a changed candidate before any new approval.",
                },
                "source_refs": [],
                "artifact_refs": [],
                "memory_citations": [],
                "target": None,
                "reason": None,
                "result_artifact": None,
                "decision_reason": None,
            }, False
        if name == "approve_artifact_candidate":
            self.version = 2
            return {
                "error": {
                    "code": "candidate_conflict",
                    "message": "The Candidate version is stale.",
                    "details": {"expected_version": arguments["expected_version"], "current_version": 2},
                }
            }, True
        return {
            "error": {
                "code": "FIXTURE_UNEXPECTED_OPERATION",
                "message": "This operation is outside the fixture scenario.",
            }
        }, True

    async def mcp(self, request: Request) -> Response:
        message = await request.json()
        self.requests.append(message)
        if "id" not in message:
            return Response(status_code=204)
        method = message.get("method")
        if method == "initialize":
            result = {
                "protocolVersion": message["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "PowerContext guidance fixture", "version": "1"},
            }
        elif method == "tools/list":
            result = {"tools": self.tools}
        elif method == "tools/call":
            params = message["params"]
            name, arguments = params["name"], params.get("arguments", {})
            schema = next((tool["inputSchema"] for tool in self.tools if tool["name"] == name), None)
            try:
                if schema is None:
                    raise ValueError("unknown_tool")
                jsonschema.validate(arguments, schema)
                body, failed = self.reply(name, arguments)
            except (jsonschema.ValidationError, ValueError):
                body, failed = (
                    {"error": {"code": "FIXTURE_INVALID_ARGUMENTS", "message": "Invalid MCP arguments."}},
                    True,
                )
            result = {"content": [{"type": "text", "text": json.dumps(body)}], "isError": failed}
            self.calls.append(
                {
                    "turn": self.turn,
                    "rpc_id": message["id"],
                    "name": name,
                    "arguments": arguments,
                    "result": body,
                    "is_error": failed,
                }
            )
        else:
            result = {}
        return JSONResponse({"jsonrpc": "2.0", "id": message["id"], "result": result})
