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
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any

import httpx
import pytest
from anthropic import APIConnectionError as AnthropicConnectionError
from anthropic import AsyncAnthropic
from openai import APIConnectionError, AsyncOpenAI
from pydantic import BaseModel, SecretStr
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from powercontext.builtin.inference.carrier_checks import inspect_sse
from powercontext.builtin.inference.errors import InferencePrivacyError, find_privacy_error
from powercontext.builtin.inference.models import InferenceUsage
from powercontext.builtin.inference.privacy import InferenceProtectionConfig, PrivacyPolicy, UpstreamProfile
from powercontext.builtin.inference.protected_transport import ProtectedAsyncTransport
from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIStructuredGenerator

SECRET = "synthetic-forbidden-secret"  # noqa: S105 - synthetic forbidden-value fixture
ENDPOINT = "https://example.invalid/v1/chat/completions"


def config(endpoint: str = ENDPOINT, **profile_values: Any) -> InferenceProtectionConfig:
    profile_values = {
        "context_window_tokens": 100000,
        "max_output_tokens": 256,
        "validated_utf8_token_bound": True,
        **profile_values,
    }
    return InferenceProtectionConfig(
        mode="enforcing",
        forbidden_values=(SecretStr(SECRET),),
        targets={
            "test": UpstreamProfile(endpoint=endpoint, model="test-model", protocol="openai-chat", **profile_values)
        },
    )


def body(content: Any, **extra: Any) -> bytes:
    extra = {"max_tokens": 10, **extra}
    return json.dumps({"model": "test-model", "messages": [{"role": "user", "content": content}], **extra}).encode()


@pytest.mark.parametrize(
    "content",
    [
        SECRET,
        [{"type": "text", "text": "synthetic-forbidden-"}, {"type": "text", "text": "secret"}],
        '{"argument":"synthetic-forbidden-\\u0073ecret"}',
    ],
)
def test_rule_checks_decoded_nested_and_adjacent_text(content: Any) -> None:
    result = PrivacyPolicy(config()).inspect_json(body(content), phase="before_send", target_profile="test")
    assert result.decision == "block"
    assert result.reason_codes == ("forbidden_content",)
    assert SECRET not in result.model_dump_json()


@pytest.mark.parametrize("forbidden", ["sensitive\nvalue", 'sensitive"value'])
@pytest.mark.parametrize("phase", ["before_send", "before_release"])
def test_nested_json_arrays_do_not_hide_escaped_forbidden_scalars(forbidden: str, phase) -> None:
    policy = PrivacyPolicy(config().model_copy(update={"forbidden_values": (SecretStr(forbidden),)}))
    nested = json.dumps([{"argument": json.dumps([forbidden])}])
    raw = body(nested) if phase == "before_send" else response(nested)
    assert forbidden not in raw.decode()
    result = policy.inspect_json(raw, phase=phase, target_profile="test")
    assert result.decision == "block" and result.reason_codes == ("forbidden_content",)


@pytest.mark.parametrize("root", ["[]", '[{"value":"safe"}]'])
def test_support_for_nested_arrays_does_not_admit_array_root_protocol_requests(root: str) -> None:
    result = PrivacyPolicy(config()).inspect_json(root.encode(), phase="before_send", target_profile="test")
    assert result.decision == "withhold"
    assert result.reason_codes == ("json_invalid_or_too_deep",)


@pytest.mark.parametrize("nested", ['[{"value":"safe","value":"other"}]', '{"items":[{"x":1,"x":2}]}'])
def test_nested_array_duplicate_keys_are_withheld(nested: str) -> None:
    result = PrivacyPolicy(config()).inspect_json(body(nested), phase="before_send", target_profile="test")
    assert result.decision == "withhold" and result.reason_codes == ("json_duplicate_key",)


def test_json_looking_prose_is_preserved_but_nested_json_over_profile_depth_is_withheld() -> None:
    policy = PrivacyPolicy(config().model_copy(update={"max_json_depth": 8}))
    ordinary = policy.inspect_json(
        body("[pytest failed; no JSON document follows"), phase="before_send", target_profile="test"
    )
    assert ordinary.decision == "allow"
    nested = "[" * 10 + '"safe"' + "]" * 10
    result = policy.inspect_json(body(nested), phase="before_send", target_profile="test")
    assert result.decision == "withhold" and result.reason_codes == ("json_invalid_or_too_deep",)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"model":"test-model","messages":[],"hidden":"synthetic-forbidden-secret","hidden":"clean"}',
        b'{"model":"test-model","messages":[{"role":"user","content":"synthetic-forbidden-\\u0073ecret"}]}',
    ],
)
def test_rule_scans_values_before_duplicate_key_normalization(raw: bytes) -> None:
    assert PrivacyPolicy(config()).inspect_json(raw, phase="before_send", target_profile="test").decision == "block"


def test_numeric_spelling_is_not_lost_before_inspection() -> None:
    policy = PrivacyPolicy(config().model_copy(update={"forbidden_values": (SecretStr("1.2300e+20"),)}))
    result = policy.inspect_json(
        b'{"model":"test-model","messages":[],"temperature":1.2300e+20}',
        phase="before_send",
        target_profile="test",
    )
    assert result.decision == "block"


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (b'{"model":"test-model","model":"test-model"}', "json_duplicate_key"),
        (b'{"model":"test-model",', "json_invalid_or_too_deep"),
        (b'{"model":"test-model","temperature":1e8192}', "json_invalid_or_too_deep"),
        (b'{"model":"test-model","\\ud800":"invalid key"}', "json_invalid_or_too_deep"),
        (body([{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}]), "content_unsupported"),
        (body("clean", previous_response_id="opaque-state"), "content_field_unsupported"),
        (body("clean", stream=True), "streaming_unsupported"),
        (body("clean", n=2), "multiple_choices_unsupported"),
    ],
)
def test_unverified_carriers_and_hidden_state_are_withheld(raw: bytes, reason: str) -> None:
    result = PrivacyPolicy(config()).inspect_json(raw, phase="before_send", target_profile="test")
    assert result.decision == "withhold"
    assert result.reason_codes == (reason,)
    assert result.coverage.complete is False


@pytest.mark.parametrize("protocol", ["openai-chat", "openai-responses", "anthropic-messages"])
@pytest.mark.parametrize("schema_location", ["tool", "output"])
def test_text_schemas_can_define_opaque_carrier_property_names(protocol, schema_location) -> None:
    schema = {
        "type": "object",
        "properties": {
            name: {"type": "string"}
            for name in ("file_id", "image", "audio", "id", "source", "url", "data", "document")
        },
    }
    request = {"model": "test-model", "max_tokens": 10, "messages": [{"role": "user", "content": "clean"}]}
    if protocol == "openai-chat":
        request.update(
            {"tools": [{"type": "function", "function": {"name": "describe_file", "parameters": schema}}]}
            if schema_location == "tool"
            else {"response_format": {"type": "json_schema", "json_schema": {"name": "file", "schema": schema}}}
        )
    elif protocol == "openai-responses":
        request = {"model": "test-model", "max_output_tokens": 10, "input": "clean", "store": False}
        request.update(
            {"tools": [{"type": "function", "name": "describe_file", "parameters": schema}]}
            if schema_location == "tool"
            else {"text": {"format": {"type": "json_schema", "name": "file", "schema": schema}}}
        )
    else:
        request.update(
            {"tools": [{"name": "describe_file", "input_schema": schema}]}
            if schema_location == "tool"
            else {"output_config": {"format": {"type": "json_schema", "schema": schema}}}
        )
    protection = config()
    protection = protection.model_copy(
        update={"targets": {"test": protection.targets["test"].model_copy(update={"protocol": protocol})}}
    )
    policy = PrivacyPolicy(protection)
    admitted = policy.inspect_json(json.dumps(request).encode(), phase="before_send", target_profile="test")
    assert admitted.decision == "allow" and admitted.coverage.complete

    schema["description"] = SECRET
    rejected = policy.inspect_json(json.dumps(request).encode(), phase="before_send", target_profile="test")
    assert rejected.decision == "block" and rejected.reason_codes == ("forbidden_content",)


def test_anthropic_tool_input_property_names_are_text_data_but_result_images_are_opaque() -> None:
    protection = config()
    protection = protection.model_copy(
        update={"targets": {"test": protection.targets["test"].model_copy(update={"protocol": "anthropic-messages"})}}
    )
    policy = PrivacyPolicy(protection)
    request: dict[str, Any] = {
        "model": "test-model",
        "max_tokens": 10,
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call",
                        "name": "describe_file",
                        "input": {
                            "file_id": "local-name",
                            "id": "user-data-id",
                            "type": "document",
                            "source": {"type": "url", "url": "https://example.invalid/manual.pdf"},
                        },
                    }
                ],
            },
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call", "content": "clean"}]},
        ],
    }
    admitted = policy.inspect_json(json.dumps(request).encode(), phase="before_send", target_profile="test")
    assert admitted.decision == "allow" and admitted.coverage.complete
    request["messages"] = [
        request["messages"][0],
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AA=="}}
                    ],
                }
            ],
        },
    ]
    rejected = policy.inspect_json(json.dumps(request).encode(), phase="before_send", target_profile="test")
    assert rejected.decision == "withhold" and rejected.reason_codes == ("content_unsupported",)


def test_destination_and_output_window_are_independent_of_secrets() -> None:
    policy = PrivacyPolicy(config(context_window_tokens=100, max_output_tokens=10, validated_utf8_token_bound=True))
    result = policy.inspect_json(body("clean", max_tokens=10), phase="before_send", target_profile="test")
    assert result.reason_codes == ("context_window_exceeded",)
    changed = policy.inspect_json(
        body("clean"), phase="before_send", target_profile="test", endpoint=ENDPOINT + "/other"
    )
    assert changed.reason_codes == ("target_mismatch",)
    unverified = PrivacyPolicy(config(context_window_tokens=10000, validated_utf8_token_bound=False))
    assert unverified.inspect_json(body("clean"), phase="before_send", target_profile="test").reason_codes == (
        "token_bound_unverified",
    )


def sse(content: tuple[str, ...], *, field: str = "content", complete: bool = True) -> bytes:
    events: list[dict[str, Any]] = [
        {
            "id": "test",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "test-model",
            "choices": [{"index": 0, "delta": {field: piece}, "finish_reason": None}],
        }
        for piece in content
    ]
    if complete:
        events.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
    return (
        "".join(f"data: {json.dumps(event)}\n\n" for event in events) + ("data: [DONE]\n\n" if complete else "")
    ).encode()


@pytest.mark.parametrize("field", ["content", "reasoning_content", "reasoning"])
def test_strict_sse_scans_content_split_across_provider_events(field: str) -> None:
    policy = PrivacyPolicy(config(allow_streaming=True))
    assert inspect_sse(policy, sse(("synthetic-forbidden-", "secret"), field=field), "test").decision == "block"
    assert inspect_sse(policy, sse(("clean",), field=field), "test").decision == "allow"
    assert inspect_sse(policy, sse(("clean",), field=field, complete=False), "test").decision == "withhold"


class _Server(ThreadingHTTPServer):
    def __init__(self, responses: list[tuple[int, str, bytes]]) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.responses = responses
        self.requests: list[bytes] = []


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def do_POST(self) -> None:
        self.server.requests.append(self.rfile.read(int(self.headers["Content-Length"])))
        status, content_type, content = self.server.responses[
            min(len(self.server.requests) - 1, len(self.server.responses) - 1)
        ]
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass


@contextmanager
def server(responses: list[tuple[int, str, bytes]]) -> Iterator[tuple[_Server, str]]:
    instance = _Server(responses)
    thread = Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance, f"http://127.0.0.1:{instance.server_port}/v1/chat/completions"
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join()


def response(content: str = '{"value":"ok"}') -> bytes:
    return json.dumps({
        "id": "test",
        "object": "chat.completion",
        "created": 0,
        "model": "test-model",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {
            "prompt_tokens": 12,
            "completion_tokens": 7,
            "total_tokens": 19,
            "prompt_tokens_details": {"cached_tokens": 8},
        },
    }).encode()


def test_native_responses_sdk_requires_non_storing_expanded_input_before_real_send() -> None:
    async def run(endpoint: str) -> None:
        endpoint = endpoint.replace("/chat/completions", "/responses")
        protection = config(endpoint)
        protection = protection.model_copy(
            update={"targets": {"test": protection.targets["test"].model_copy(update={"protocol": "openai-responses"})}}
        )
        async with (
            httpx.AsyncClient(transport=ProtectedAsyncTransport(protection)) as http,
            AsyncOpenAI(
                base_url=endpoint.removesuffix("/responses"), api_key="test", max_retries=0, http_client=http
            ) as sdk,
        ):
            cases: tuple[tuple[dict[str, Any], str], ...] = (
                ({"input": "clean"}, "provider_state_unsupported"),
                (
                    {"input": [{"type": "item_reference", "id": "msg_prior_private"}], "store": False},
                    "content_unsupported",
                ),
                ({"input": [{"id": "msg_prior_private"}], "store": False}, "content_unsupported"),
                ({"input": [{"id": "msg_prior_private", "type": None}], "store": False}, "content_unsupported"),
            )
            for extra, reason in cases:
                with pytest.raises(APIConnectionError) as caught:
                    await sdk.responses.create(model="test-model", max_output_tokens=10, **extra)
                refusal = find_privacy_error(caught.value)
                assert refusal is not None and refusal.phase == "before_send"
                assert refusal.reason_codes == (reason,)
            result = await sdk.responses.create(model="test-model", input="clean", store=False, max_output_tokens=10)
            assert result.status == "completed"

    payload = json.dumps({"id": "resp_test", "model": "test-model", "status": "completed", "output": []}).encode()
    with server([(200, "application/json", payload)]) as (upstream, endpoint):
        asyncio.run(run(endpoint))
        assert len(upstream.requests) == 1
        assert json.loads(upstream.requests[0])["store"] is False


def test_native_anthropic_sdk_withholds_opaque_documents_before_real_send() -> None:
    async def run(endpoint: str) -> None:
        endpoint = endpoint.replace("/chat/completions", "/messages")
        protection = config(endpoint)
        protection = protection.model_copy(
            update={
                "targets": {"test": protection.targets["test"].model_copy(update={"protocol": "anthropic-messages"})}
            }
        )
        async with (
            httpx.AsyncClient(transport=ProtectedAsyncTransport(protection)) as http,
            AsyncAnthropic(
                base_url=endpoint.removesuffix("/v1/messages"), api_key="test", max_retries=0, http_client=http
            ) as sdk,
        ):
            sources = [
                {"type": "base64", "media_type": "application/pdf", "data": "JVBERi0xLjQK"},
                {"type": "url", "url": "https://example.invalid/private.pdf"},
                {"type": "file", "file_id": "file_private"},
                {
                    "type": "content",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AA=="}}
                    ],
                },
            ]
            for source in sources:
                request: dict[str, Any] = {
                    "model": "test-model",
                    "max_tokens": 10,
                    "messages": [{"role": "user", "content": [{"type": "document", "source": source}]}],
                }
                with pytest.raises(AnthropicConnectionError) as caught:
                    await sdk.messages.create(**request)
                refusal = find_privacy_error(caught.value)
                assert refusal is not None and refusal.phase == "before_send"
                assert refusal.reason_codes == ("content_unsupported",)
            accepted = await sdk.messages.create(
                model="test-model", max_tokens=10, messages=[{"role": "user", "content": "clean"}]
            )
            assert accepted.stop_reason == "end_turn"

    payload = json.dumps({
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "test-model",
        "content": [{"type": "text", "text": "safe"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }).encode()
    with server([(200, "application/json", payload)]) as (upstream, endpoint):
        asyncio.run(run(endpoint))
        assert len(upstream.requests) == 1
        assert json.loads(upstream.requests[0])["messages"] == [{"role": "user", "content": "clean"}]


class Value(BaseModel):
    value: str


async def generate(
    endpoint: str,
    *,
    instructions: str = "Return a value.",
    requests: int = 2,
    configuration: InferenceProtectionConfig | None = None,
    model_settings: Any = None,
    usages: list[InferenceUsage] | None = None,
) -> Value:
    async with (
        httpx.AsyncClient(transport=ProtectedAsyncTransport(configuration or config(endpoint))) as http,
        AsyncOpenAI(
            base_url=endpoint.removesuffix("/chat/completions"), api_key="test", max_retries=0, http_client=http
        ) as sdk,
    ):
        model = OpenAIChatModel("test-model", provider=OpenAIProvider(openai_client=sdk))
        generator = PydanticAIStructuredGenerator(
            model=model,
            instructions=instructions,
            input_type=Value,
            output_type=Value,
            limits=InferenceLimits(max_requests=requests),
            model_settings={"max_tokens": 256, **(model_settings or {})},
        )
        result = await generator.generate(Value(value="clean"))
        if usages is not None:
            usages.append(result.usage)
        return result.output


def test_final_sdk_rendered_instructions_are_blocked_before_real_http_send() -> None:
    with server([(200, "application/json", response())]) as (upstream, endpoint):
        with pytest.raises(InferencePrivacyError) as caught:
            asyncio.run(generate(endpoint, instructions=f"Return {SECRET}."))
        assert caught.value.phase == "before_send"
        assert upstream.requests == []


@pytest.mark.parametrize("status", [200, 429, 500])
def test_raw_response_and_error_are_blocked_before_sdk_or_host_can_receive_them(status: int) -> None:
    payload = (
        response(f'{{"value":"{SECRET}"}}') if status == 200 else json.dumps({"error": {"message": SECRET}}).encode()
    )
    with server([(status, "application/json", payload)]) as (upstream, endpoint):
        with pytest.raises(InferencePrivacyError) as caught:
            asyncio.run(generate(endpoint))
        assert caught.value.phase == "before_release"
        assert len(upstream.requests) == 1
        assert SECRET not in str(caught.value)
        assert caught.value.usage is not None
        assert caught.value.usage.input_tokens == (12 if status == 200 else None)
        assert caught.value.usage.cache_read_tokens == (8 if status == 200 else None)


def test_each_schema_retry_is_checked_at_final_provider_boundary() -> None:
    responses = [(200, "application/json", response("not valid JSON")), (200, "application/json", response())]
    with server(responses) as (upstream, endpoint):
        assert asyncio.run(generate(endpoint)).value == "ok"
        assert len(upstream.requests) == 2
        second = json.loads(upstream.requests[1])
        assert len(second["messages"]) > len(json.loads(upstream.requests[0])["messages"])


def test_shadow_reports_input_rejection_without_blocking_real_request() -> None:
    async def run(endpoint: str) -> None:
        observed = []
        shadow = config(endpoint).model_copy(update={"mode": "shadow"})
        async with httpx.AsyncClient(transport=ProtectedAsyncTransport(shadow, observer=observed.append)) as client:
            received = await client.post(endpoint, content=body(SECRET), headers={"content-type": "application/json"})
        assert received.status_code == 200
        assert observed[0].decision == "block"

    with server([(200, "application/json", response())]) as (upstream, endpoint):
        asyncio.run(run(endpoint))
        assert len(upstream.requests) == 1


def test_strict_response_buffer_limit_releases_no_raw_content() -> None:
    async def run(endpoint: str) -> None:
        bounded = config(endpoint).model_copy(update={"max_response_bytes": 256})
        async with httpx.AsyncClient(transport=ProtectedAsyncTransport(bounded)) as client:
            with pytest.raises(InferencePrivacyError, match="before_release") as caught:
                await client.post(endpoint, content=body("clean"), headers={"content-type": "application/json"})
            assert caught.value.reason_codes == ("payload_too_large",)

    with server([(200, "application/json", response("a" * 1000))]) as (upstream, endpoint):
        asyncio.run(run(endpoint))
        assert len(upstream.requests) == 1


def test_native_cache_settings_are_checked_after_render_without_reordering_messages() -> None:
    with server([(200, "application/json", response())]) as (upstream, endpoint):
        asyncio.run(generate(endpoint))
        approved = config(endpoint, allowed_cache_retention=("in_memory",))
        settings = {"openai_prompt_cache_key": "partition-key", "openai_prompt_cache_retention": "in_memory"}
        asyncio.run(generate(endpoint, configuration=approved, model_settings=settings))
        original, cached = (json.loads(value) for value in upstream.requests)
        assert original["messages"] == cached["messages"]
        assert cached["prompt_cache_key"] == "partition-key"
        assert cached["prompt_cache_retention"] == "in_memory"
        with pytest.raises(InferencePrivacyError) as caught:
            asyncio.run(generate(endpoint, model_settings=settings))
        assert caught.value.reason_codes == ("cache_retention_unapproved",)
        assert len(upstream.requests) == 2


def test_unconfigured_or_unverified_generation_budget_cannot_claim_complete_admission() -> None:
    cases: tuple[tuple[dict[str, Any], str], ...] = (
        ({"context_window_tokens": None}, "budget_unconfigured"),
        ({"max_output_tokens": None}, "budget_unconfigured"),
        ({"validated_utf8_token_bound": False}, "token_bound_unverified"),
    )
    for values, reason in cases:
        decision = PrivacyPolicy(config(**values)).inspect_json(
            body("clean"), phase="before_send", target_profile="test"
        )
        assert decision.decision == "withhold"
        assert decision.reason_codes == (reason,)
        assert decision.coverage.complete is False


def test_blocked_schema_retry_preserves_usage_of_the_prior_attempt() -> None:
    responses = [
        (200, "application/json", response("not valid JSON")),
        (200, "application/json", response(f'{{"value":"{SECRET}"}}')),
    ]
    with server(responses) as (upstream, endpoint):
        with pytest.raises(InferencePrivacyError) as caught:
            asyncio.run(generate(endpoint))
        assert len(upstream.requests) == 2
        assert caught.value.usage is not None
        assert caught.value.usage.requests == 2
        assert caught.value.usage.input_tokens == 24
        assert caught.value.usage.output_tokens == 14


def test_numeric_canonical_value_is_scanned_alongside_original_lexical_value() -> None:
    policy = PrivacyPolicy(config().model_copy(update={"forbidden_values": (SecretStr("123"),)}))
    result = policy.inspect_json(body("clean", temperature=1.23e2), phase="before_send", target_profile="test")
    assert result.decision == "block"
    lexical = body("clean").replace(b'"max_tokens": 10', b'"max_tokens": 1.23e2')
    assert policy.inspect_json(lexical, phase="before_send", target_profile="test").decision == "block"


@pytest.mark.parametrize("field", ["content", "reasoning_content", "reasoning"])
@pytest.mark.parametrize(
    "fragments",
    [("synthetic-forbidden-", "secret"), ('{"value":"synthetic-forbidden-', '\\u0073ecret"}')],
)
def test_sse_strict_transport_releases_no_content_before_joined_inspection(
    field: str, fragments: tuple[str, ...]
) -> None:
    safe_payload = sse(("safe ", "text"), field=field)

    async def run(endpoint: str) -> None:
        released: list[bytes] = []
        async with httpx.AsyncClient(
            transport=ProtectedAsyncTransport(config(endpoint, allow_streaming=True))
        ) as client:
            with pytest.raises(InferencePrivacyError) as caught:
                async with client.stream(
                    "POST", endpoint, content=body("clean", stream=True), headers={"content-type": "application/json"}
                ) as result:
                    released.extend([piece async for piece in result.aiter_bytes()])
            assert caught.value.phase == "before_release"
            assert caught.value.reason_codes == ("forbidden_content",)
            assert released == []
            async with client.stream(
                "POST", endpoint, content=body("clean", stream=True), headers={"content-type": "application/json"}
            ) as result:
                assert await result.aread() == safe_payload

    with server([
        (200, "text/event-stream", sse(fragments, field=field)),
        (200, "text/event-stream", safe_payload),
    ]) as (upstream, endpoint):
        asyncio.run(run(endpoint))
        assert len(upstream.requests) == 2


def test_embedding_transport_preserves_inputs_and_blocks_before_real_send() -> None:
    from pydantic_ai import Embedder
    from pydantic_ai.embeddings.openai import OpenAIEmbeddingModel

    from powercontext.builtin.artifacts.memory.models import EmbeddingProfile
    from powercontext.builtin.inference.pydantic_ai import PydanticAIEmbeddingModel

    async def run(endpoint: str) -> None:
        configuration = InferenceProtectionConfig(
            mode="enforcing",
            forbidden_values=(SecretStr(SECRET),),
            targets={"embed": UpstreamProfile(endpoint=endpoint, model="test-model", protocol="openai-embeddings")},
        )
        async with (
            httpx.AsyncClient(transport=ProtectedAsyncTransport(configuration)) as http,
            AsyncOpenAI(
                base_url=endpoint.removesuffix("/embeddings"), api_key="test", max_retries=0, http_client=http
            ) as sdk,
        ):
            model = PydanticAIEmbeddingModel(
                embedder=Embedder(OpenAIEmbeddingModel("test-model", provider=OpenAIProvider(openai_client=sdk))),
                profile=EmbeddingProfile(
                    profile_id="test", model="test-model", dimension=3, distance="l2", normalization="none"
                ),
            )
            with pytest.raises(InferencePrivacyError) as caught:
                await model.embed((SECRET,))
            assert caught.value.phase == "before_send"
            result = await model.embed_query(("first", "second"))
            assert result.vectors == ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))

    payload = json.dumps({
        "object": "list",
        "model": "test-model",
        "usage": {"prompt_tokens": 4, "total_tokens": 4},
        "data": [
            {"object": "embedding", "index": 0, "embedding": [1, 0, 0]},
            {"object": "embedding", "index": 1, "embedding": [0, 1, 0]},
        ],
    }).encode()
    with server([(200, "application/json", payload)]) as (upstream, endpoint):
        asyncio.run(run(endpoint.replace("/chat/completions", "/embeddings")))
        assert len(upstream.requests) == 1
        assert json.loads(upstream.requests[0])["input"] == ["first", "second"]


@pytest.mark.parametrize("cached_tokens", [None, 0, 8])
def test_successful_generation_preserves_known_zero_and_missing_cache_usage(cached_tokens: int | None) -> None:
    payload = json.loads(response())
    details = {} if cached_tokens is None else {"cached_tokens": cached_tokens}
    payload["usage"]["prompt_tokens_details"] = details
    usages: list[InferenceUsage] = []
    with server([(200, "application/json", json.dumps(payload).encode())]) as (_upstream, endpoint):
        assert asyncio.run(generate(endpoint, usages=usages)).value == "ok"
    assert usages[0].cache_read_tokens == cached_tokens
    assert usages[0].target_model == "test-model"
    assert usages[0].input_tokens == 12


@pytest.mark.parametrize("arguments", ['{"amount":', '{"amount":1,"amount":2}', "[]"])
def test_incomplete_or_ambiguous_tool_arguments_are_withheld_before_release(arguments: str) -> None:
    payload = json.loads(response())
    payload["choices"][0] = {
        "index": 0,
        "message": {
            "role": "assistant",
            "tool_calls": [{"id": "call", "type": "function", "function": {"name": "pay", "arguments": arguments}}],
        },
        "finish_reason": "tool_calls",
    }
    decision = PrivacyPolicy(config()).inspect_json(
        json.dumps(payload).encode(), phase="before_release", target_profile="test"
    )
    assert decision.decision == "withhold"
    assert decision.coverage.complete is False


def test_sse_rejects_incomplete_tools_extra_choices_and_data_after_finish() -> None:
    policy = PrivacyPolicy(config(allow_streaming=True))
    choices = [
        {"index": 0, "delta": {}, "finish_reason": "stop"},
        {"index": 1, "delta": {"content": "unfinished"}, "finish_reason": None},
    ]
    payload = f"data: {json.dumps({'choices': choices})}\n\ndata: [DONE]\n\n".encode()
    assert inspect_sse(policy, payload, "test").reason_codes == ("multiple_choices_unsupported",)
    payload = sse(("clean",)).replace(
        b"data: [DONE]", b'data: {"choices":[{"index":0,"delta":{"content":"more"}}]}\n\ndata: [DONE]'
    )
    assert inspect_sse(policy, payload, "test").reason_codes == ("sse_data_after_finish",)
    events = [
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"amount":'}}]},
                    "finish_reason": None,
                }
            ]
        },
        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
    ]
    payload = ("".join(f"data: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n").encode()
    assert inspect_sse(policy, payload, "test").reason_codes == ("tool_arguments_incomplete",)


def test_unbounded_request_stream_stops_before_send_at_the_byte_budget() -> None:
    reads = 0
    sent = False

    async def chunks():
        nonlocal reads
        while True:
            reads += 1
            yield b"x" * 128

    async def upstream(_request: httpx.Request) -> httpx.Response:
        nonlocal sent
        sent = True
        return httpx.Response(200, json={})

    async def run() -> None:
        protection = config().model_copy(update={"max_payload_bytes": 256})
        async with httpx.AsyncClient(
            transport=ProtectedAsyncTransport(protection, transport=httpx.MockTransport(upstream))
        ) as client:
            with pytest.raises(InferencePrivacyError) as caught:
                await client.post(ENDPOINT, content=chunks())
            assert caught.value.reason_codes == ("payload_too_large",)

    asyncio.run(run())
    assert reads == 3
    assert sent is False


def test_failed_schema_validation_keeps_the_provider_reported_bill() -> None:
    from powercontext.builtin.inference.errors import InvalidInferenceOutputError

    with server([(200, "application/json", response("not valid JSON"))]) as (upstream, endpoint):
        with pytest.raises(InvalidInferenceOutputError) as caught:
            asyncio.run(generate(endpoint, requests=1))
        assert len(upstream.requests) == 1
        assert caught.value.usage is not None
        assert caught.value.usage.input_tokens == 12
        assert caught.value.usage.output_tokens == 7
        assert caught.value.usage.cache_read_tokens == 8


def test_rejected_sse_preserves_its_cumulative_provider_usage() -> None:
    async def run(endpoint: str) -> None:
        async with httpx.AsyncClient(
            transport=ProtectedAsyncTransport(config(endpoint, allow_streaming=True))
        ) as client:
            with pytest.raises(InferencePrivacyError) as caught:
                await client.post(
                    endpoint, content=body("clean", stream=True), headers={"content-type": "application/json"}
                )
            assert caught.value.usage is not None
            assert caught.value.usage.input_tokens == 12
            assert caught.value.usage.output_tokens == 7
            assert caught.value.usage.cache_read_tokens == 8

    usage = json.loads(response())["usage"]
    payload = sse(("synthetic-forbidden-", "secret")).replace(
        b"data: [DONE]", f"data: {json.dumps({'choices': [], 'usage': usage})}\n\ndata: [DONE]".encode()
    )
    with server([(200, "text/event-stream", payload)]) as (_upstream, endpoint):
        asyncio.run(run(endpoint))


def test_shadow_leaves_request_stream_unread_until_the_actual_transport() -> None:
    observations = []

    async def chunks():
        yield b"first"
        yield b"second"

    async def upstream(request: httpx.Request) -> httpx.Response:
        assert await request.aread() == b"firstsecond"
        return httpx.Response(200, content=b"unchanged")

    async def run() -> None:
        transport = ProtectedAsyncTransport(
            config().model_copy(update={"mode": "shadow"}),
            transport=httpx.MockTransport(upstream),
            observer=observations.append,
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await client.post(ENDPOINT, content=chunks())
            assert result.content == b"unchanged"

    asyncio.run(run())
    assert observations[0].reason_codes == ("request_stream_uninspected",)
    assert observations[0].coverage.complete is False


@pytest.mark.parametrize("index", ["-0", "0.0", "0e0", '"0"', "null", "true"])
def test_native_stream_withholds_ambiguous_tool_indices(index: str) -> None:
    first = {
        "id": "test",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "test-model",
        "choices": [
            {
                "index": 0,
                "delta": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_test",
                            "type": "function",
                            "function": {"name": "synthetic-forbidden-", "arguments": "{}"},
                        }
                    ],
                },
                "finish_reason": None,
            }
        ],
    }
    second = (
        '{"choices":[{"index":0,"delta":{"tool_calls":[{"index":'
        + index
        + ',"function":{"name":"secret"}}]},"finish_reason":null}]}'
    )
    payload = (
        f"data: {json.dumps(first)}\n\ndata: {second}\n\n"
        'data: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n\n'
        "data: [DONE]\n\n"
    ).encode()

    async def run(endpoint: str) -> None:
        async with (
            httpx.AsyncClient(transport=ProtectedAsyncTransport(config(endpoint, allow_streaming=True))) as http,
            AsyncOpenAI(
                base_url=endpoint.removesuffix("/chat/completions"), api_key="test", max_retries=0, http_client=http
            ) as sdk,
        ):
            with pytest.raises(APIConnectionError) as caught:
                async with sdk.chat.completions.stream(
                    model="test-model",
                    messages=[{"role": "user", "content": "clean"}],
                    max_tokens=10,
                ) as stream:
                    await stream.get_final_completion()
            refusal = find_privacy_error(caught.value)
            assert refusal is not None and refusal.phase == "before_release"
            assert refusal.reason_codes == ("tool_identity_unsupported",)

    with server([(200, "text/event-stream", payload)]) as (upstream, endpoint):
        asyncio.run(run(endpoint))
        assert len(upstream.requests) == 1


def test_native_responses_sdk_withholds_image_generation_input_and_output() -> None:
    item = {"type": "image_generation_call", "id": "ig_test", "status": "completed", "result": "iVBORw0KGgo="}
    payload = json.dumps({"id": "resp_test", "model": "test-model", "status": "completed", "output": [item]}).encode()

    async def run(endpoint: str) -> None:
        endpoint = endpoint.replace("/chat/completions", "/responses")
        protection = config(endpoint)
        protection = protection.model_copy(
            update={"targets": {"test": protection.targets["test"].model_copy(update={"protocol": "openai-responses"})}}
        )
        async with (
            httpx.AsyncClient(transport=ProtectedAsyncTransport(protection)) as http,
            AsyncOpenAI(
                base_url=endpoint.removesuffix("/responses"), api_key="test", max_retries=0, http_client=http
            ) as sdk,
        ):
            cases: tuple[dict[str, Any], ...] = (
                {"input": "clean", "tools": [{"type": "image_generation"}]},
                {"input": [item]},
            )
            for extra in cases:
                with pytest.raises(APIConnectionError) as caught:
                    await sdk.responses.create(model="test-model", max_output_tokens=10, store=False, **extra)
                refusal = find_privacy_error(caught.value)
                assert refusal is not None and refusal.phase == "before_send"
                assert refusal.reason_codes == ("content_unsupported",)
            with pytest.raises(APIConnectionError) as caught:
                await sdk.responses.create(model="test-model", input="clean", max_output_tokens=10, store=False)
            refusal = find_privacy_error(caught.value)
            assert refusal is not None and refusal.phase == "before_release"
            assert refusal.reason_codes == ("content_unsupported",)

    with server([(200, "application/json", payload)]) as (upstream, endpoint):
        asyncio.run(run(endpoint))
        assert len(upstream.requests) == 1


def test_sse_keeps_distinct_tool_names_separate_but_joins_same_tool() -> None:
    events: list[dict[str, Any]] = [
        {
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": index,
                                "function": {"name": name, "arguments": "{}"},
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ]
        }
        for index, name in enumerate(("synthetic-forbidden-", "secret"))
    ]
    events.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})
    payload = ("".join(f"data: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n").encode()
    policy = PrivacyPolicy(config(allow_streaming=True))
    assert inspect_sse(policy, payload, "test").decision == "allow"
    assert inspect_sse(policy, payload.replace(b'"index": 1', b'"index": 0'), "test").decision == "block"
