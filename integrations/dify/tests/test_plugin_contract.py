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

"""Behavior regressions through the registered Dify SDK entry points."""

from __future__ import annotations

import json
from copy import deepcopy

import httpx
import pytest
from dify_plugin import DifyPluginEnv
from dify_plugin.core.plugin_registration import PluginRegistration
from dify_plugin.errors.tool import ToolProviderCredentialValidationError
from jsonschema import Draft7Validator
from powercontext_dify.client import CONTRACT
from sdk_driver import PLUGIN, invoke

CREDENTIALS = {"server_url": "http://localhost:9000", "api_token": "test-only-secret", "scope_id": "scope-A"}
SCOPE = {
    "scope_id": "scope-A",
    "title": "SDK regression",
    "summary": "Disposable test scope.",
    "context_references": [],
    "external_references": [],
    "version": 1,
}
ERROR = {"error": {"code": "rejected", "message": "do not expose test-only-secret", "details": None}}
ATOMIC_REF = {"family": "atomic-memory", "artifact_id": "am-1", "revision": 1}
ATOMIC_REVISION = {
    "scope_id": "scope-A",
    **ATOMIC_REF,
    "content": {"kind": "decision", "text": "Use fixed Scope."},
    "sources": [],
    "artifacts": [],
    "content_digest": "sha256:" + "a" * 64,
}


@pytest.fixture(scope="module")
def registry():
    # The SDK resolves tool paths against cwd, like its plugin runner.
    import os

    previous = os.getcwd()
    os.chdir(PLUGIN)
    try:
        yield PluginRegistration(DifyPluginEnv())
    finally:
        os.chdir(previous)


@pytest.fixture
def transport(monkeypatch):
    original = httpx.Client

    def install(handler):
        calls = []

        def dispatch(request):
            payload = json.loads(request.content) if request.content else None
            calls.append((request.url.path, payload))
            if request.url.path == "/v1/scope-bindings/resolve":
                return httpx.Response(200, json=SCOPE)
            return handler(request)

        monkeypatch.setattr(
            httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(dispatch), **kwargs)
        )
        return calls

    return install


def run(registry, tool, parameters, credentials=None):
    return invoke(registry, {"tool": tool, "parameters": parameters, "credentials": credentials or CREDENTIALS})


@pytest.mark.parametrize("historical", [False, True])
def test_atomic_read_keeps_exact_revision_and_only_current_content_etag(registry, transport, historical):
    def respond(request):
        assert request.method == "GET" and not request.content
        if request.url.path.endswith("/revisions/1"):
            return httpx.Response(200, json=ATOMIC_REVISION)
        return httpx.Response(
            200,
            json={**ATOMIC_REVISION, "revision": 2 if historical else 1},
            headers={"ETag": '"revision:2"' if historical else '"revision:1"'},
        )

    transport(respond)
    result = run(registry, "pc_memory_get", {"artifact": json.dumps(ATOMIC_REF)})
    assert result["ok"] is True
    assert result["data"]["revision"] == 1 and result["data"]["content"] == ATOMIC_REVISION["content"]
    if historical:
        assert "etag" not in result["data"]
    else:
        assert result["data"]["etag"] == '"revision:1"'


def test_atomic_revise_sends_content_cas_and_preserves_precondition_conflict(registry, transport):
    def respond(request):
        assert request.method == "PUT" and request.url.path.endswith("/atomic-memory/am-1")
        assert request.headers["If-Match"] == '"revision:1"'
        assert json.loads(request.content) == {"content": {"kind": "constraint", "text": "Retain scope."}}
        return httpx.Response(412, json=ERROR)

    transport(respond)
    result = run(
        registry,
        "pc_memory_revise",
        {
            "artifact": json.dumps(ATOMIC_REF),
            "if_match": '"revision:1"',
            "kind": "constraint",
            "text": "Retain scope.",
        },
    )
    assert result["status"] == "error" and result["error"]["code"] == "conflict"


def test_atomic_retire_uses_zero_state_version_and_recoverable_forgotten_state(registry, transport):
    receipt = {
        "changed": True,
        "records": [
            {
                "artifact": ATOMIC_REF,
                "kind": "decision",
                "text": "Use fixed Scope.",
                "state": "forgotten",
                "state_version": 1,
                "merged_into_id": None,
            }
        ],
    }

    def respond(request):
        assert request.url.path == "/v1/atomic-memory/lifecycle"
        assert json.loads(request.content) == {
            "scope_id": "scope-A",
            "target": {"artifact": ATOMIC_REF, "state_version": 0},
            "state": "forgotten",
        }
        return httpx.Response(200, json=receipt)

    transport(respond)
    result = run(registry, "pc_memory_retire", {"artifact": json.dumps(ATOMIC_REF), "state_version": 0})
    assert result["ok"] is True and result["data"] == receipt


@pytest.mark.parametrize("tool", ["pc_memory_get", "pc_memory_revise", "pc_memory_retire"])
def test_legacy_citation_input_is_rejected_before_http(registry, transport, tool):
    calls = transport(lambda _request: pytest.fail("Legacy citation input must not reach HTTP"))
    parameters = {
        "citation": json.dumps(
            {
                "memory_ref": {"family": "memory", "artifact_id": "m-1", "revision": 1},
                "entry_id": "entry-1",
                "entry_version_id": "v-1",
            }
        )
    }
    if tool == "pc_memory_revise":
        parameters.update(kind="decision", text="Read only.")
    result = run(registry, tool, parameters)
    assert result["ok"] is False and result["error"]["code"] == "invalid_request"
    assert [path for path, _ in calls] == ["/v1/scope-bindings/resolve"]


def test_sdk_registers_exact_catalog_and_json_text_inputs(registry):
    _, _, loaded = registry.tools_mapping["powercontext"]
    assert {name: entry.operation for name, (_, entry) in loaded.items()} == CONTRACT["tools"]
    for declaration, _ in loaded.values():
        assert not {
            "scope_id",
            "tag_filter",
            "expected_revision",
            "max_bytes",
            "include_code",
            "assembly",
            "api_token",
            "server_url",
        } & {parameter.name for parameter in declaration.parameters}
    draft = next(p for p in loaded["pc_handoff_finalize"][0].parameters if p.name == "draft")
    assert draft.type.value == "string"
    decoded = json.loads(draft.llm_description.split("Decoded JSON Schema: ", 1)[1])
    assert {"objective", "state", "next_action", "omissions"} <= decoded["properties"].keys()
    assert loaded["pc_remember"][0].description.human.zh_hans == "按明确保存意图写入整理后的记忆。"


@pytest.mark.parametrize(
    "tool,parameters,name,value",
    [
        ("pc_prepare_context", {"query": "scope"}, "scope_id", "scope-B"),
        ("pc_prepare_context", {"query": "scope"}, "max_bytes", 1),
        ("pc_prepare_context", {"query": "scope"}, "include_code", True),
        ("pc_prepare_context", {"query": "scope"}, "assembly", {}),
        ("pc_search", {"query": "scope"}, "tag_filter", {"tags": ["private"]}),
        ("pc_remember", {"kind": "decision", "text": "Retain."}, "expected_revision", 1),
    ],
)
def test_server_controlled_parameters_are_rejected_at_the_sdk_boundary(
    registry, transport, tool, parameters, name, value
):
    transport(lambda _request: pytest.fail("Server-controlled input must not reach the operation"))
    result = run(registry, tool, {**parameters, name: value})
    assert result["error"]["code"] == "invalid_request"


def test_workflow_results_expose_context_references_and_complete_handoffs(registry):
    _, _, loaded = registry.tools_mapping["powercontext"]
    for declaration, _ in loaded.values():
        Draft7Validator.check_schema(declaration.output_schema)
        result = declaration.output_schema["properties"]["result"]
        assert result["type"] == "object"
        assert result["properties"]
        # A workflow must also be able to route an unsuccessful call with an empty result.
        Draft7Validator({**declaration.output_schema, **result}).validate({})
    context = loaded["pc_prepare_context"][0].output_schema["properties"]["result"]
    content = Draft7Validator({**loaded["pc_prepare_context"][0].output_schema, **context["properties"]["content"]})
    content.validate("已保留的上下文")
    content.validate(None)
    memory = loaded["pc_memory_get"][0].output_schema["properties"]["result"]
    assert {"artifact_id", "revision", "content", "etag"} <= memory["properties"].keys()
    draft = loaded["pc_handoff_prepare"][0].output_schema["properties"]["result"]
    assert {"objective", "state", "disposition", "next_action", "omissions"} <= draft["properties"].keys()
    prepared = loaded["pc_handoff_finalize"][0].output_schema["properties"]["result"]
    assert {"schema", "scope_id", "base", "content"} <= prepared["properties"].keys()


def test_workflow_rendering_keeps_nullable_values_and_integer_revision_validation(registry):
    _, _, loaded = registry.tools_mapping["powercontext"]
    for tool, field in (
        ("pc_experience_generate", "candidate"),
        ("pc_handoff_activate", "draft"),
        ("pc_handoff_prepare", "next_action"),
    ):
        schema = loaded[tool][0].output_schema
        validator = Draft7Validator({**schema, "$ref": f"#/properties/result/properties/{field}"})
        validator.validate(None)
        assert not validator.is_valid("malformed object")
    schema = loaded["pc_handoff_commit"][0].output_schema
    revision = Draft7Validator({**schema, "$ref": "#/properties/result/properties/reference/properties/revision"})
    revision.validate(2)
    assert not revision.is_valid(2.5)
    assert not revision.is_valid(True)


def test_nullable_candidate_receipt_stays_null_through_sdk_messages(registry, transport):
    receipt = {"status": "no_op", "candidate": None}
    transport(lambda _request: httpx.Response(200, json=receipt))
    result = run(registry, "pc_experience_generate", {"source_refs": '[{"name":"content","source_id":"turn-1"}]'})
    assert result["ok"] is True and result["data"] == receipt


@pytest.mark.parametrize("empty", [False, True])
def test_context_workflow_output_preserves_success_and_empty_content(registry, transport, empty):
    body = {
        "schema": "powercontext.prepared-context.v1",
        "status": "empty" if empty else "ready",
        "content": None if empty else "已保留的上下文",
        "content_bytes": 0 if empty else len("已保留的上下文".encode()),
    }
    transport(lambda _request: httpx.Response(200, json=body))
    result = run(registry, "pc_prepare_context", {"query": "scope"})
    assert result["ok"] is True
    assert result["status"] == ("empty" if empty else "success")
    assert result["data"] == body


@pytest.mark.parametrize("body", [None, {}, [], "not-json"])
def test_empty_or_malformed_write_receipt_never_reports_success(registry, transport, body):
    def respond(_request):
        return httpx.Response(200, text="not-json") if body == "not-json" else httpx.Response(200, json=body)

    calls = transport(respond)
    result = run(registry, "pc_remember", {"kind": "decision", "text": "Use fixed Scope."})
    assert result["ok"] is False
    assert result["status"] == "unknown"
    assert result["error"]["code"] == "invalid_response"
    assert "Check Server state" in result["error"]["message"]
    assert sum(path == "/v1/memory/remember" for path, _ in calls) == 1


def test_timeout_does_not_retry_or_expose_transport_credentials(registry, transport):
    def respond(request):
        raise httpx.ReadTimeout("test-only-secret", request=request)

    calls = transport(respond)
    result = run(registry, "pc_remember", {"kind": "decision", "text": "Use fixed Scope."})
    assert result["status"] == "unknown"
    assert "test-only-secret" not in json.dumps(result)
    assert sum(path == "/v1/memory/remember" for path, _ in calls) == 1


@pytest.mark.parametrize("status,code", [(401, "authentication_failed"), (403, "forbidden"), (409, "conflict")])
def test_explicit_server_rejection_is_sanitized_failure(registry, transport, status, code):
    transport(lambda _request: httpx.Response(status, json=ERROR, headers={"x-powercontext-request-id": "test-123"}))
    result = run(registry, "pc_remember", {"kind": "decision", "text": "Use fixed Scope."})
    assert result["status"] == "error"
    assert result["error"]["code"] == code
    assert result["error"]["request_id"] == "test-123"
    assert "test-only-secret" not in json.dumps(result)


@pytest.mark.parametrize(
    "content",
    [
        '{"credentials":{"api_key":"test-only-secret"}}',
        'Text before {"nested":{"password":"test-only-secret"}} text after',
        "access_token=test-only-secret",
        '{"api_token":"test-only-secret"}',
        "Authorization: Bearer test-only-secret",
    ],
)
def test_capture_rejects_nested_and_mixed_text_secrets_before_write(registry, transport, content):
    calls = transport(lambda _request: pytest.fail("Capture must be rejected before HTTP write"))
    result = run(registry, "pc_capture_source", {"source_id": "turn-1", "content": content})
    assert result["error"]["code"] == "sensitive_content"
    assert all(path != "/v1/sources/content" for path, _ in calls)
    assert "test-only-secret" not in json.dumps(result)


@pytest.mark.parametrize(
    "parameters",
    [
        {"kind": "decision", "text": "中" * 2731},
        {"kind": "unknown", "text": "Not a supported memory kind."},
        {"kind": "decision", "text": "   "},
        {"kind": "decision", "text": "Override attempt", "scope_id": "scope-B"},
    ],
)
def test_invalid_memory_inputs_cannot_write_or_override_scope(registry, transport, parameters):
    calls = transport(lambda _request: pytest.fail("Invalid input must not write"))
    result = run(registry, "pc_remember", parameters)
    assert result["error"]["code"] == "invalid_request"
    assert all(path != "/v1/memory/remember" for path, _ in calls)


def test_query_has_character_budget_and_empty_search_is_success(registry, transport):
    calls = transport(lambda _request: httpx.Response(200, json={"mode": "text", "hits": []}))
    result = run(registry, "pc_search", {"query": "中" * 4000})
    assert result["ok"] is True and result["status"] == "empty"
    assert calls[-1][1]["limit"] == 8
    assert calls[0][1] == {"allow_default": False, "explicit_scope_id": "scope-A"}


@pytest.mark.parametrize("limit", [0, 9, True, "8"])
def test_search_rejects_limit_outside_eight_hit_budget(registry, transport, limit):
    transport(lambda _request: pytest.fail("Invalid limit must not search"))
    assert run(registry, "pc_search", {"query": "scope", "limit": limit})["error"]["code"] == "invalid_request"


@pytest.mark.parametrize(
    "body",
    [
        {"schema": "powercontext.prepared-context.v1", "status": "empty", "content": "", "content_bytes": 0},
        {"schema": "powercontext.prepared-context.v1", "status": "ready", "content": "中", "content_bytes": 1},
        {"schema": "powercontext.prepared-context.v1", "status": "ready", "content": None, "content_bytes": 0},
    ],
)
def test_context_rejects_inconsistent_utf8_or_status_contract(registry, transport, body):
    transport(lambda _request: httpx.Response(200, json=body))
    result = run(registry, "pc_prepare_context", {"query": "scope"})
    assert result["status"] == "error" and result["error"]["code"] == "invalid_response"


def test_missing_binding_never_falls_back_or_calls_memory(registry, monkeypatch):
    original = httpx.Client
    calls = []

    def dispatch(request):
        calls.append(json.loads(request.content))
        return httpx.Response(404, json=ERROR)

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(dispatch), **kwargs))
    credentials = {**CREDENTIALS, "scope_id": None, "binding_external_id": "deployment:missing"}
    result = run(registry, "pc_search", {"query": "scope"}, credentials)
    assert result["error"]["code"] == "not_found"
    assert calls == [
        {
            "allow_default": False,
            "binding_keys": [{"integration": "dify", "kind": "configured-scope", "external_id": "deployment:missing"}],
        }
    ]


@pytest.mark.parametrize(
    "configuration",
    [
        {"scope_id": "", "binding_external_id": ""},
        {"binding_external_id": "deployment:app"},
        {"server_url": "http://private.example"},
        {"server_url": "https://bad host.example"},
        {"context_assembly": "null"},
        {"context_assembly": '{"unknown": true}'},
    ],
)
def test_invalid_configuration_is_sanitized_without_network(registry, transport, configuration):
    transport(lambda _request: pytest.fail("Invalid configuration must not access Server"))
    result = run(registry, "pc_search", {"query": "scope"}, {**CREDENTIALS, **configuration})
    assert result["error"]["code"] == "invalid_configuration"
    assert "test-only-secret" not in json.dumps(result)


def test_provider_checks_protected_scope_without_writing(registry, transport):
    _, provider_class, _ = registry.tools_mapping["powercontext"]
    calls = transport(lambda _request: httpx.Response(403, json=ERROR))
    provider = provider_class()
    with pytest.raises(ToolProviderCredentialValidationError, match="cannot access"):
        provider.validate_credentials(deepcopy(CREDENTIALS))
    assert [path for path, _ in calls] == ["/v1/scope-bindings/resolve", "/v1/scopes/scope-A"]


def test_combined_generation_reference_budget_is_enforced(registry, transport):
    transport(lambda _request: pytest.fail("Reference overflow must not generate"))
    ref = {"name": "content", "source_id": "turn-1"}
    result = run(
        registry,
        "pc_experience_generate",
        {
            "source_refs": json.dumps([ref] * 17),
            "artifact_refs": json.dumps([ATOMIC_REF] * 16),
        },
    )
    assert result["error"]["code"] == "invalid_request"


def test_wrong_explicit_scope_resolution_cannot_access_memory(registry, monkeypatch):
    original = httpx.Client
    calls = []

    def dispatch(request):
        calls.append(request.url.path)
        return httpx.Response(200, json={**SCOPE, "scope_id": "scope-B"})

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(dispatch), **kwargs))
    result = run(registry, "pc_search", {"query": "scope"})
    assert result["error"]["code"] == "invalid_response"
    assert calls == ["/v1/scope-bindings/resolve"]


def test_partial_error_receipt_survives_without_raw_diagnostics(registry, transport):
    source = {"name": "content", "source_id": "turn-1"}
    body = {
        "error": {
            "code": "processing_unavailable",
            "message": "test-only-secret",
            "details": {"source": source, "diagnostic": "test-only-secret"},
        }
    }
    transport(lambda _request: httpx.Response(503, json=body))
    result = run(registry, "pc_capture_source", {"source_id": "turn-1", "content": "Explicit evidence."})
    assert result["status"] == "unknown"
    assert result["data"] == {"source": source}
    assert "test-only-secret" not in json.dumps(result)


def test_oversized_write_response_reports_unknown_without_truncation(registry, transport):
    calls = transport(lambda _request: httpx.Response(200, content=b"x" * (4 * 1024 * 1024 + 1)))
    result = run(registry, "pc_remember", {"kind": "decision", "text": "Bounded response."})
    assert result["status"] == "unknown"
    assert result["error"]["code"] == "response_too_large"
    assert result["data"] is None
    assert sum(path == "/v1/memory/remember" for path, _ in calls) == 1
