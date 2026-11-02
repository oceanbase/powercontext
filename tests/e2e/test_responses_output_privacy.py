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

"""Inspect the text the native Responses SDK assembles from real HTTP replies."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from openai import APIConnectionError, AsyncOpenAI
from openai.types.responses.response_create_params import ResponseCreateParamsNonStreaming
from pydantic import SecretStr

from powercontext.builtin.inference.errors import find_privacy_error
from powercontext.builtin.inference.privacy import InferenceProtectionConfig, UpstreamProfile
from powercontext.builtin.inference.protected_transport import ProtectedAsyncTransport
from tests.e2e.test_rfc2086_transport_acceptance import RecordingServer, upstream

_SECRET = "synthetic-forbidden-secret"  # noqa: S105 - synthetic forbidden-content fixture


@pytest.mark.parametrize(
    "case", ["split", "intervening_tool", "intervening_refusal", "nested_json", "safe", "separate_refusal"]
)
def test_responses_sdk_assembled_output_is_inspected_before_release(case):
    def message(index, text, *, refusal=False):
        content = (
            {"type": "refusal", "refusal": text}
            if refusal
            else {
                "type": "output_text",
                "text": text,
                "annotations": [],
            }
        )
        return {
            "id": f"msg_{index}",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [content],
        }

    prefix = "ordinary-" if case == "safe" else "synthetic-forbidden-"
    suffix = "secret"
    if case == "nested_json":
        prefix, suffix = '{"value":"synthetic-forbidden-\\u0073', 'ecret"}'
    output = [message(0, prefix, refusal=case == "separate_refusal"), message(1, suffix)]
    if case == "intervening_tool":
        output.insert(
            1, {"id": "call_0", "type": "function_call", "call_id": "call_0", "name": "inspect", "arguments": "{}"}
        )
    elif case == "intervening_refusal":
        output.insert(1, message(2, "Unavailable", refusal=True))
    raw = json.dumps({
        "id": "resp_0",
        "object": "response",
        "model": "test-model",
        "status": "completed",
        "output": output,
    }).encode()
    assert _SECRET.encode() not in raw
    expected = suffix if case == "separate_refusal" else prefix + suffix
    blocked = case not in {"safe", "separate_refusal"}

    async def scenario(server: RecordingServer):
        base_url = f"http://127.0.0.1:{server.server_port}/v1"
        config = InferenceProtectionConfig(
            mode="enforcing",
            forbidden_values=(SecretStr(_SECRET),),
            targets={
                "test": UpstreamProfile(
                    endpoint=f"{base_url}/responses",
                    model="test-model",
                    protocol="openai-responses",
                    context_window_tokens=32768,
                    max_output_tokens=256,
                    validated_utf8_token_bound=True,
                )
            },
        )
        request: ResponseCreateParamsNonStreaming = {
            "model": "test-model",
            "input": "Return an answer.",
            "store": False,
            "max_output_tokens": 64,
        }
        async with AsyncOpenAI(api_key="synthetic", base_url=base_url, max_retries=0) as unprotected:
            response = await unprotected.responses.create(**request)
            assert response.output_text == expected
            if case == "nested_json":
                assert json.loads(response.output_text)["value"] == _SECRET
        decisions = []
        transport = ProtectedAsyncTransport(config, observer=decisions.append)
        async with AsyncOpenAI(
            api_key="synthetic",
            base_url=base_url,
            max_retries=0,
            http_client=httpx.AsyncClient(transport=transport),
        ) as protected:
            if blocked:
                with pytest.raises(APIConnectionError) as rejected:
                    await protected.responses.create(**request)
                privacy = find_privacy_error(rejected.value)
                assert privacy is not None and privacy.phase == "before_release"
                assert privacy.reason_codes == ("forbidden_content",)
                assert _SECRET not in str(privacy)
            else:
                response = await protected.responses.create(**request)
                assert response.output_text == expected
        assert len(server.received) == 2
        assert decisions[-1].phase == "before_release" and decisions[-1].coverage.complete
        assert decisions[-1].decision == ("block" if blocked else "allow")

    with upstream(raw) as server:
        asyncio.run(scenario(server))
