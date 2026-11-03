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

"""Provider-encrypted items are admitted only with receipts from earlier releases.

The request and response shapes follow a recorded Codex 0.145 run against the OpenAI Responses API: encrypted
reasoning replayed in later requests, and a subagent task returned encrypted in ``spawn_agent`` arguments, then sent
as an ``encrypted_content`` part of the subagent's ``agent_message``.
"""

import json

import pytest

from powercontext.builtin.inference.privacy import UpstreamProfile
from powercontext.builtin.runtime.context_bindings import BindingCodec
from powercontext.builtin.runtime.context_check import (
    CHECK_CONTEXT_REQUEST,
    RECEIPT_VALIDITY,
    CheckContextBeforeRelease,
    CheckContextBeforeSend,
    ModelJsonPayload,
)
from tests.builtin.runtime.test_context_check import NOW, _check, _config

PARENT = "codex-thread:parent"
CHILD = "codex-thread:child"
REASONING = "gAAAAABq-reasoning-ciphertext"
TASK = "gAAAAABq-subagent-task-ciphertext"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _responses_config(admit=("reasoning", "agent_message")):
    return _config(
        targets={
            "test": UpstreamProfile(
                endpoint="https://model.example/v1/responses",
                model="test",
                protocol="openai-responses",
                context_window_tokens=65536,
                max_output_tokens=64,
                validated_utf8_token_bound=True,
                admit_encrypted_items=admit,
            )
        }
    )


def _payload(value):
    return ModelJsonPayload(headers={"content-type": "application/json"}, body=json.dumps(value))


def _user(text):
    return {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}


def _send(items, **changes):
    value = {
        "phase": "before_send",
        "target_profile": "test",
        "payload": _payload({"model": "test", "max_output_tokens": 64, "store": False, "input": items}),
        "prepared_contexts": (),
    }
    value.update(changes)
    return CheckContextBeforeSend.model_validate(value)


def _release(binding, output, **changes):
    value = {
        "phase": "before_release",
        "target_profile": "test",
        "payload": _payload({"model": "test", "status": "completed", "output": output}),
        "request_binding": binding,
        "completion": "complete",
    }
    value.update(changes)
    return CheckContextBeforeRelease.model_validate(value)


SPAWN = {
    "type": "function_call",
    "call_id": "call_spawn",
    "name": "spawn_agent",
    "arguments": json.dumps({"task_name": "helper", "fork_turns": "all", "message": TASK}),
}


async def _parent_receipt(codec, config, conversation=PARENT):
    send = await _check(_send([_user("start a helper")], conversation_id=conversation), codec, config=config)
    assert send.decision == "allow" and send.binding
    release = await _check(
        _release(
            send.binding,
            [{"type": "reasoning", "summary": [], "encrypted_content": REASONING}, SPAWN],
            conversation_id=conversation,
        ),
        codec,
        config=config,
    )
    assert release.decision == "allow"
    return release.receipt


@pytest.mark.anyio
async def test_a_release_receipts_its_encrypted_values_for_the_conversation():
    codec, config = BindingCodec(), _responses_config()
    receipt = await _parent_receipt(codec, config)
    assert receipt is not None

    continuation = [
        _user("start a helper"),
        {"type": "reasoning", "summary": [], "encrypted_content": REASONING},
        SPAWN,
        {"type": "function_call_output", "call_id": "call_spawn", "output": "started"},
    ]
    admitted = await _check(
        _send(continuation, conversation_id=PARENT, encrypted_receipts=(receipt,)), codec, config=config
    )
    assert admitted.decision == "allow"

    for changes in ({"conversation_id": PARENT}, {"encrypted_receipts": (receipt,)}):
        withheld = await _check(_send(continuation, **changes), codec, config=config)
        assert withheld.decision == "withhold"
        assert withheld.reason_codes == ("encrypted-origin-unverified",)


@pytest.mark.anyio
async def test_a_value_that_no_release_returned_is_withheld():
    codec, config = BindingCodec(), _responses_config()
    receipt = await _parent_receipt(codec, config)
    forged = [_user("hi"), {"type": "reasoning", "summary": [], "encrypted_content": "gAAAAABq-forged"}]
    result = await _check(_send(forged, conversation_id=PARENT, encrypted_receipts=(receipt,)), codec, config=config)
    assert result.reason_codes == ("encrypted-origin-unverified",)


@pytest.mark.anyio
async def test_a_subagent_may_use_its_parents_receipt_for_the_handed_over_task():
    codec, config = BindingCodec(), _responses_config()
    receipt = await _parent_receipt(codec, config)
    task = {
        "type": "agent_message",
        "author": "/root",
        "recipient": "/root/helper",
        "content": [
            {"type": "input_text", "text": "Message Type: NEW_TASK\nTask name: /root/helper\nSender: /root"},
            {"type": "encrypted_content", "encrypted_content": TASK},
        ],
    }
    child = await _check(
        _send([task], conversation_id=CHILD, parent_conversation_id=PARENT, encrypted_receipts=(receipt,)),
        codec,
        config=config,
    )
    assert child.decision == "allow"
    orphan = await _check(_send([task], conversation_id=CHILD, encrypted_receipts=(receipt,)), codec, config=config)
    assert orphan.reason_codes == ("encrypted-origin-unverified",)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "changes",
    [{"binding_identity": "user:mallory"}, {"scope_id": "other:scope"}],
)
async def test_a_receipt_from_another_principal_or_scope_proves_nothing(changes):
    codec, config = BindingCodec(), _responses_config()
    foreign = await _check(_send([_user("start a helper")], conversation_id=PARENT), codec, config=config, **changes)
    release = await _check(
        _release(foreign.binding, [{"type": "reasoning", "encrypted_content": REASONING}], conversation_id=PARENT),
        codec,
        config=config,
        **changes,
    )
    replay = [_user("hi"), {"type": "reasoning", "encrypted_content": REASONING}]
    result = await _check(
        _send(replay, conversation_id=PARENT, encrypted_receipts=(release.receipt,)), codec, config=config
    )
    assert result.reason_codes == ("encrypted-origin-unverified",)


@pytest.mark.anyio
async def test_a_receipt_expires_with_its_validity():
    codec, config = BindingCodec(), _responses_config()
    receipt = await _parent_receipt(codec, config)
    replay = [_user("hi"), {"type": "reasoning", "encrypted_content": REASONING}]
    later = await _check(
        _send(replay, conversation_id=PARENT, encrypted_receipts=(receipt,)),
        codec,
        config=config,
        now=NOW + RECEIPT_VALIDITY,
    )
    assert later.reason_codes == ("encrypted-origin-unverified",)


@pytest.mark.anyio
async def test_without_a_conversation_nothing_is_receipted():
    codec, config = BindingCodec(), _responses_config()
    assert await _parent_receipt(codec, config, conversation=None) is None


@pytest.mark.anyio
async def test_targets_that_do_not_opt_in_and_other_positions_stay_opaque():
    codec = BindingCodec()
    replay = [_user("hi"), {"type": "reasoning", "encrypted_content": REASONING}]
    closed = await _check(_send(replay, conversation_id=PARENT), codec, config=_responses_config(admit=()))
    assert closed.reason_codes == ("content_unsupported",)

    config = _responses_config()
    receipt = await _parent_receipt(codec, config)
    elsewhere = [_user("hi"), {"type": "message", "role": "assistant", "encrypted_content": REASONING}]
    result = await _check(_send(elsewhere, conversation_id=PARENT, encrypted_receipts=(receipt,)), codec, config=config)
    assert result.reason_codes == ("content_unsupported",)


def test_only_responses_targets_can_opt_in():
    with pytest.raises(ValueError, match="openai-responses"):
        UpstreamProfile(
            endpoint="https://model.example/v1/chat/completions",
            model="test",
            protocol="openai-chat",
            admit_encrypted_items=("reasoning",),
        )


def test_requests_without_the_new_fields_still_validate():
    # The HTTP route re-serializes the transport model, which writes omitted optional fields as null.
    request = CHECK_CONTEXT_REQUEST.validate_json(
        json.dumps({
            "phase": "before_send",
            "target_profile": "test",
            "payload": {
                "schema": "powercontext.model-json-payload.v1",
                "headers": {"content-type": "application/json"},
                "body": "{}",
            },
            "prepared_contexts": [],
            "conversation_id": None,
            "parent_conversation_id": None,
            "encrypted_receipts": None,
        })
    )
    assert request.encrypted_receipts == ()
