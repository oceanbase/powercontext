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

"""Controlled inference must invoke discovered MCP tools without assuming aliases."""

import json
import threading

import httpx
import pytest

from tests.e2e.test_minimax_host import ModelGateway, _objects, _tool_listing


@pytest.fixture
def gateway(request):
    server = ModelGateway(allow_multiple_scope_tools=getattr(request, "param", False))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def model_response(gateway, names, *, discovery):
    messages = [{"role": "user", "content": "List PowerContext scopes"}]
    tools = names
    if discovery == "search":
        messages.append({
            "role": "tool",
            "tool_call_id": "search-result",
            "content": json.dumps({"tools": [{"name": name} for name in names]}),
        })
        tools = ["mcp_invoke"]
    with httpx.Client(trust_env=False, timeout=5) as client:
        response = client.post(
            f"http://127.0.0.1:{gateway.server_port}/v1/chat/completions",
            json={"messages": messages, "tools": [{"function": {"name": name}} for name in tools]},
        )
    assert response.status_code == 200
    return response.json()["choices"][0]


@pytest.mark.parametrize("discovery", ["direct", "search"])
@pytest.mark.parametrize("name", ["mcp__powercontext__list_scopes", "mcp__powercontext_2__list_scopes"])
def test_gateway_invokes_the_discovered_scope_tool(gateway, discovery, name):
    response = model_response(gateway, [name], discovery=discovery)
    call = response["message"]["tool_calls"][0]
    assert response["finish_reason"] == "tool_calls"
    assert call["function"]["name"] == ("mcp_invoke" if discovery == "search" else name)
    assert json.loads(call["function"]["arguments"]) == (
        {"tool_name": name, "arguments": {"limit": 10}} if discovery == "search" else {"limit": 10}
    )
    assert gateway.invoked_tools == [name]
    assert call["id"] in gateway.scope_call_ids
    assert not gateway.errors


@pytest.mark.parametrize("discovery", ["direct", "search"])
@pytest.mark.parametrize(
    "names",
    [[], ["unrelated_tool"], ["mcp__powercontext__list_scopes", "mcp__powercontext_2__list_scopes"]],
    ids=["empty", "missing", "ambiguous"],
)
def test_gateway_rejects_missing_or_ambiguous_scope_tools(gateway, discovery, names):
    response = model_response(gateway, names, discovery=discovery)
    assert "tool_calls" not in response["message"]
    assert response["message"]["content"] == "MINIMAX_MCP_ACCEPTANCE_FAILED"
    assert not gateway.invoked_tools
    assert gateway.errors and "Expected one isolated list_scopes tool" in gateway.errors[0]


@pytest.mark.parametrize("gateway", [True], indirect=True)
@pytest.mark.parametrize("discovery", ["direct", "search"])
def test_installed_default_gateway_invokes_one_discovered_scope_tool(gateway, discovery):
    names = ["mcp__powercontext__list_scopes", "mcp__powercontext_2__list_scopes"]
    response = model_response(gateway, names, discovery=discovery)
    call = response["message"]["tool_calls"][0]
    arguments = json.loads(call["function"]["arguments"])
    selected = arguments["tool_name"] if discovery == "search" else call["function"]["name"]
    assert selected in names
    assert response["finish_reason"] == "tool_calls"
    assert arguments == (
        {"tool_name": selected, "arguments": {"limit": 10}} if discovery == "search" else {"limit": 10}
    )
    assert gateway.invoked_tools == [selected]
    assert call["id"] in gateway.scope_call_ids
    assert not gateway.errors


@pytest.mark.parametrize("gateway", [True], indirect=True)
@pytest.mark.parametrize("discovery", ["direct", "search"])
@pytest.mark.parametrize("names", [[], ["unrelated_tool"]], ids=["empty", "missing"])
def test_installed_default_gateway_still_rejects_missing_scope_tools(gateway, discovery, names):
    response = model_response(gateway, names, discovery=discovery)
    assert "tool_calls" not in response["message"]
    assert response["message"]["content"] == "MINIMAX_MCP_ACCEPTANCE_FAILED"
    assert not gateway.invoked_tools
    assert gateway.errors and "Expected at least one list_scopes tool" in gateway.errors[0]


@pytest.mark.parametrize("prefix", ['{"message": "Scopes listed"}', "Scopes listed"])
def test_scope_evidence_is_read_from_an_appended_structured_content_block(prefix):
    scope = {"scope_id": "seeded-scope", "title": "Seeded scope"}
    content = prefix + "\n[MCP structured content]\n" + json.dumps({"scopes": [scope]})
    assert scope in list(_objects(content))


def test_provider_negotiation_without_a_tool_catalog_is_not_an_mcp_discovery_failure(gateway):
    with httpx.Client(trust_env=False, timeout=5) as client:
        response = client.post(
            f"http://127.0.0.1:{gateway.server_port}/v1/chat/completions",
            json={"messages": [{"role": "user", "content": "Hi"}]},
        )
    assert response.status_code == 200
    assert not gateway.errors
    assert not gateway.invoked_tools


@pytest.mark.parametrize("encoding", ["json", "sse", "sse-crlf"])
def test_native_tools_list_evidence_reads_streamable_http_response_encodings(encoding):
    document = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": "list_scopes"}]}})
    if encoding != "json":
        document = "event: message\ndata: " + document + "\n\n"
    if encoding == "sse-crlf":
        document = document.replace("\n", "\r\n")
    assert _tool_listing(document.encode()) == ["list_scopes"]
