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

"""Admission is exact, phase-bound, and cannot promote historical text to authority."""

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

import pytest
from pydantic import SecretStr, ValidationError

from powercontext.builtin.inference.privacy import InferenceProtectionConfig, UpstreamProfile
from powercontext.builtin.runtime.context_bindings import BindingCodec
from powercontext.builtin.runtime.context_check import (
    CHECK_CONTEXT_REQUEST,
    CheckContextBeforeRelease,
    CheckContextBeforeSend,
    ContextCheckTooLargeError,
    ModelJsonPayload,
    check_context,
)
from powercontext.builtin.runtime.context_reuse import ContextReuseError, ValidatedContextReuse
from powercontext.builtin.runtime.errors import InvalidRuntimeRequestError

NOW = datetime(2026, 10, 26, tzinfo=UTC)


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _config(**changes):
    value = {
        "mode": "enforcing",
        "forbidden_values": (SecretStr("synthetic-forbidden-value"),),
        "targets": {
            "test": UpstreamProfile(
                endpoint="https://model.example/v1/chat/completions",
                model="test",
                protocol="openai-chat",
                context_window_tokens=65536,
                max_output_tokens=64,
                validated_utf8_token_bound=True,
            )
        },
    }
    value.update(changes)
    return InferenceProtectionConfig.model_validate(value)


def _payload(value):
    return ModelJsonPayload(headers={"content-type": "application/json"}, body=json.dumps(value))


def _send(text="safe", **changes):
    value = {
        "phase": "before_send",
        "target_profile": "test",
        "payload": _payload({"model": "test", "max_tokens": 64, "messages": [{"role": "user", "content": text}]}),
        "prepared_contexts": (),
    }
    value.update(changes)
    return CheckContextBeforeSend.model_validate(value)


def _release(binding, text="safe"):
    return CheckContextBeforeRelease(
        phase="before_release",
        target_profile="test",
        request_binding=binding,
        completion="complete",
        payload=_payload({
            "model": "test",
            "choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        }),
    )


async def _no_reuse(_binding):
    pytest.fail("pure checking must not retrieve or validate an unrelated prepared context")


async def _check(request, codec, **changes):
    arguments = {
        "scope_id": "test:scope",
        "config": _config(),
        "codec": codec,
        "binding_identity": "user:alice",
        "now": NOW,
        "validate_reuse": _no_reuse,
    }
    arguments.update(changes)
    return await check_context(request, **arguments)


@pytest.mark.anyio
@pytest.mark.parametrize("protocol", ["openai-chat", "openai-responses", "anthropic-messages"])
async def test_null_error_does_not_authorize_incomplete_output(protocol):
    config = _config()
    config = config.model_copy(
        update={"targets": {"test": config.targets["test"].model_copy(update={"protocol": protocol})}}
    )
    codec = BindingCodec()
    request = _send()
    if protocol == "openai-responses":
        request = request.model_copy(
            update={"payload": _payload({"model": "test", "max_output_tokens": 64, "input": "safe", "store": False})}
        )
    send = await _check(request, codec, config=config)
    assert send.decision == "allow" and send.binding
    payloads = {
        "openai-chat": {"choices": [{"message": {"content": "partial"}, "finish_reason": "length"}]},
        "openai-responses": {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}},
        "anthropic-messages": {"stop_reason": "max_tokens", "content": [{"type": "text", "text": "partial"}]},
    }
    release = _release(send.binding).model_copy(
        update={"payload": _payload({"model": "test", "error": None, **payloads[protocol]})}
    )
    result = await _check(release, codec, config=config)
    assert result.decision == "withhold" and result.binding is None
    assert result.reason_codes == ("response_incomplete",)
    assert not result.coverage.complete


@pytest.mark.anyio
async def test_separate_send_deadline_and_response_correlation():
    codec = BindingCodec()
    send = await _check(_send(), codec)
    assert send.decision == "allow" and send.binding
    assert send.valid_until == NOW + timedelta(seconds=60)
    assert send.correlate_until == NOW + timedelta(seconds=300)
    release = await _check(_release(send.binding), codec, now=NOW + timedelta(seconds=90))
    assert release.decision == "allow" and release.binding
    assert release.correlate_until is None
    wrong_phase = await _check(_release(release.binding), codec)
    assert wrong_phase.decision == "withhold"
    expired = await _check(_release(send.binding), codec, now=NOW + timedelta(seconds=301))
    assert expired.decision == "withhold"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "changed",
    [
        {"binding_identity": "user:bob"},
        {"scope_id": "other:scope"},
        {"config": _config(forbidden_values=(SecretStr("a different rule under the same version"),))},
    ],
)
async def test_request_binding_cannot_cross_identity_scope_or_current_policy(changed):
    codec = BindingCodec()
    send = await _check(_send(), codec)
    assert send.binding
    result = await _check(_release(send.binding), codec, **changed)
    assert result.decision == "withhold" and result.binding is None


@pytest.mark.anyio
async def test_raw_request_and_response_block_without_echoing_content():
    codec = BindingCodec()
    blocked = await _check(_send("synthetic-forbidden-value"), codec)
    assert blocked.decision == "block" and blocked.binding is None
    assert "synthetic-forbidden-value" not in blocked.model_dump_json()
    allowed = await _check(_send(), codec)
    assert allowed.binding
    blocked = await _check(_release(allowed.binding, "synthetic-forbidden-value"), codec)
    assert blocked.decision == "block" and blocked.binding is None


@pytest.mark.anyio
async def test_shadow_never_issues_an_execution_binding():
    result = await _check(_send(), BindingCodec(), config=_config(mode="shadow"))
    assert result.decision == "withhold" and not result.coverage.complete and result.binding is None


@pytest.mark.anyio
async def test_utf8_exact_block_requires_user_position_and_complete_binding():
    block = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n历史资料\nEND_POWERCONTEXT_PREPARED_CONTEXT_V1"
    prefix = "当前任务: \n"

    async def validate(binding):
        assert binding == "opaque"
        return ValidatedContextReuse(
            content_digest=sha256(block.encode()).hexdigest(),
            content_bytes=len(block.encode()),
            expires_at=NOW + timedelta(minutes=5),
            ordered_roots=(),
        )

    location = {
        "binding": "opaque",
        "location": {
            "pointer": "/messages/0/content",
            "start_byte": len(prefix.encode()),
            "end_byte": len((prefix + block).encode()),
        },
    }
    request = _send(prefix + block, prepared_contexts=(location,))
    result = await _check(request, BindingCodec(), validate_reuse=validate)
    assert result.decision == "allow"
    with pytest.raises(InvalidRuntimeRequestError, match="binding-required"):
        await _check(_send(prefix + block), BindingCodec())
    promoted = _send(
        payload=_payload({
            "model": "test",
            "max_tokens": 64,
            "messages": [{"role": "system", "content": prefix + block}],
        }),
        prepared_contexts=(location,),
    )
    with pytest.raises(InvalidRuntimeRequestError, match="location-trust"):
        await _check(promoted, BindingCodec(), validate_reuse=validate)
    altered = _send(prefix + block.replace("资料", "伪造"), prepared_contexts=(location,))
    with pytest.raises(InvalidRuntimeRequestError, match="location-content"):
        await _check(altered, BindingCodec(), validate_reuse=validate)


@pytest.mark.anyio
async def test_current_dependency_failure_withholds_instead_of_refreshing():
    async def revoked(_binding):
        raise ContextReuseError("context-reuse-source-changed")

    item = {"binding": "opaque", "location": {"pointer": "/messages/0/content", "start_byte": 0, "end_byte": 4}}
    result = await _check(_send(prepared_contexts=(item,)), BindingCodec(), validate_reuse=revoked)
    assert result.decision == "withhold" and result.reason_codes == ("context-refresh-required",)


@pytest.mark.anyio
async def test_multiple_retained_blocks_require_every_exact_ordered_utf8_location():
    blocks = [
        f"BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n历史资料{index}\nEND_POWERCONTEXT_PREPARED_CONTEXT_V1"
        for index in range(2)
    ]
    prefix, separator = "任务:\n", "\n下一份:\n"
    text = prefix + separator.join(blocks)
    starts = [len(prefix.encode()), len((prefix + blocks[0] + separator).encode())]
    items: list[dict[str, Any]] = [
        {
            "binding": str(index),
            "location": {
                "pointer": "/messages/0/content",
                "start_byte": start,
                "end_byte": start + len(block.encode()),
            },
        }
        for index, (start, block) in enumerate(zip(starts, blocks, strict=True))
    ]

    async def validate(binding):
        block = blocks[int(binding)]
        return ValidatedContextReuse(
            content_digest=sha256(block.encode()).hexdigest(),
            content_bytes=len(block.encode()),
            expires_at=NOW + timedelta(minutes=5),
            ordered_roots=(),
        )

    codec = BindingCodec()
    result = await _check(_send(text, prepared_contexts=items), codec, validate_reuse=validate)
    assert result.decision == "allow" and result.binding
    released = await _check(_release(result.binding), codec, validate_reuse=validate)
    assert released.decision == "allow"
    for remaining in ([items[0]], [items[1]]):
        with pytest.raises(InvalidRuntimeRequestError, match="binding-required"):
            await _check(_send(text, prepared_contexts=remaining), codec, validate_reuse=validate)
    with pytest.raises(InvalidRuntimeRequestError, match="location-order"):
        await _check(_send(text, prepared_contexts=list(reversed(items))), codec, validate_reuse=validate)
    overlapping = [items[0], {"binding": "1", "location": dict(items[1]["location"], start_byte=starts[0] + 1)}]
    with pytest.raises(InvalidRuntimeRequestError, match="location-overlap"):
        await _check(_send(text, prepared_contexts=overlapping), codec, validate_reuse=validate)
    split_character = [dict(items[0], location=dict(items[0]["location"], start_byte=1)), items[1]]
    with pytest.raises(InvalidRuntimeRequestError, match="location-character-boundary"):
        await _check(_send(text, prepared_contexts=split_character), codec, validate_reuse=validate)


@pytest.mark.anyio
async def test_total_utf8_budget_is_enforced_before_policy():
    with pytest.raises(ContextCheckTooLargeError):
        await _check(_send("数据" * 90), BindingCodec(), config=_config(max_payload_bytes=256))


def test_phase_dto_and_headers_do_not_accept_cross_phase_or_authority_override():
    value = _send().model_dump(mode="json")
    value["request_binding"] = "a request permit"
    with pytest.raises(ValidationError):
        CHECK_CONTEXT_REQUEST.validate_python(value)
    with pytest.raises(ValidationError):
        ModelJsonPayload(headers={"content-type": "application/json", "authorization": "arbitrary"}, body="{}")
