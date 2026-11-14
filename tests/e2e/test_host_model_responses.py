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

"""The Responses API host model fake that the real-host Codex cases drive."""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from tests.e2e.host_models.responses import ResponsesModelFixture

MEMORY_TOOLS = [
    {
        "type": "namespace",
        "name": "mcp__powercontext",
        "tools": [{"type": "function", "name": "remember_memory", "parameters": {"type": "object"}}],
    }
]


def _output_item(body: str) -> dict[str, Any]:
    (done,) = [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ") and '"response.output_item.done"' in line
    ]
    return done["item"]


def test_pending_call_is_repeated_at_its_address_when_a_request_offers_no_tools() -> None:
    model = ResponsesModelFixture()
    model.plan([("remember_memory", {"kind": "decision", "text": "Synthetic decision."})])
    client = TestClient(model.app)

    issued = client.post("/v1/responses", json={"input": [], "tools": MEMORY_TOOLS})
    # A host retry may resend the turn before the call's result, and without the tool list.
    repeated = client.post("/v1/responses", json={"input": [], "tools": []})

    assert (issued.status_code, repeated.status_code) == (200, 200)
    first, second = _output_item(issued.text), _output_item(repeated.text)
    assert first["type"] == "function_call"
    assert (first["namespace"], first["name"]) == ("mcp__powercontext", "remember_memory")
    assert second == first
