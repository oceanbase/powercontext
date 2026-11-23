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

"""Native Code Assist release checks cover visible text and thoughts separately."""

import json
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr

from powercontext.builtin.inference.privacy import InferenceProtectionConfig, UpstreamProfile
from powercontext.builtin.runtime.context_bindings import BindingCodec
from powercontext.builtin.runtime.context_check import (
    CheckContextBeforeRelease,
    CheckContextBeforeSend,
    ModelJsonPayload,
    check_context,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _payload(value):
    return ModelJsonPayload(headers={"content-type": "application/json"}, body=json.dumps(value))


async def _no_reuse(_binding):
    pytest.fail("a request without prepared context must not validate context reuse")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        pytest.param(
            [{"text": "sec"}, {"text": "padding", "thought": True}, {"text": "ret", "thought": False}],
            "block",
            id="visible-text-around-thought",
        ),
        pytest.param(
            [{"text": "sec", "thought": True}, {"text": "padding"}, {"text": "ret", "thought": True}],
            "block",
            id="thought-around-visible-text",
        ),
        pytest.param(
            [{"text": "pub"}, {"text": "reasoning", "thought": True}, {"text": "lic", "thought": False}],
            "allow",
            id="safe-interleaving",
        ),
        pytest.param(
            [{"text": "sec"}, {"text": "ret", "thought": True}],
            "allow",
            id="separate-channels-do-not-form-a-forbidden-value",
        ),
        pytest.param(
            [{"text": "sec"}, {"text": "padding", "thought": None}, {"text": "ret"}],
            "allow",
            id="null-thought-remains-visible",
        ),
        pytest.param(
            [{"text": "sec"}, {"text": "padding", "thought": "true"}, {"text": "ret"}],
            "withhold",
            id="string-thought-is-not-a-boolean",
        ),
        pytest.param(
            [{"text": "sec"}, {"text": "padding", "thought": 1}, {"text": "ret"}],
            "withhold",
            id="numeric-thought-is-not-a-boolean",
        ),
    ],
)
async def test_native_codeassist_release_checks_each_text_channel(parts, expected):
    config = InferenceProtectionConfig(
        mode="enforcing",
        forbidden_values=(SecretStr("secret"),),
        targets={
            "test": UpstreamProfile(
                endpoint="https://cloudcode-pa.googleapis.com/v1internal:generateContent",
                model="test",
                protocol="google-codeassist",
                context_window_tokens=65536,
                max_output_tokens=64,
                validated_utf8_token_bound=True,
            )
        },
    )
    codec = BindingCodec()
    now = datetime(2026, 11, 22, tzinfo=UTC)
    admitted = await check_context(
        CheckContextBeforeSend(
            phase="before_send",
            target_profile="test",
            payload=_payload({
                "model": "test",
                "project": "synthetic-project",
                "request": {
                    "contents": [{"role": "user", "parts": [{"text": "safe"}]}],
                    "generationConfig": {"maxOutputTokens": 64},
                },
            }),
            prepared_contexts=(),
        ),
        scope_id="test:scope",
        config=config,
        codec=codec,
        binding_identity="user:alice",
        now=now,
        validate_reuse=_no_reuse,
    )
    assert admitted.decision == "allow" and admitted.binding
    released = await check_context(
        CheckContextBeforeRelease(
            phase="before_release",
            target_profile="test",
            request_binding=admitted.binding,
            completion="complete",
            payload=_payload({
                "response": {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP"}]}
            }),
        ),
        scope_id="test:scope",
        config=config,
        codec=codec,
        binding_identity="user:alice",
        now=now,
        validate_reuse=_no_reuse,
    )
    assert released.decision == expected
    assert released.coverage.complete is (expected != "withhold")
    if expected == "block":
        assert released.reason_codes == ("forbidden_content",)
        assert released.binding is None
        assert "secret" not in released.model_dump_json()
    elif expected == "withhold":
        assert released.reason_codes == ("content_unsupported",)
        assert released.binding is None
    else:
        assert released.binding
