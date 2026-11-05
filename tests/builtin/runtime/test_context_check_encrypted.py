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
reasoning replayed in later requests, and a subagent task returned encrypted in ``spawn_agent`` arguments (declared
``"encrypted": true`` in Codex's ``additional_tools``), then sent as an ``encrypted_content`` part of the subagent's
``agent_message``.
"""

import json
from datetime import timedelta

import pytest

from powercontext.builtin.inference.encrypted_items import MAX_RECEIPT_VALUES
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
                context_window_tokens=400_000,
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
    "namespace": "collaboration",
    "arguments": json.dumps({"task_name": "helper", "fork_turns": "all", "message": TASK}),
}
# Codex's tool declarations: the task argument is marked encrypted.
TOOLS = {
    "type": "additional_tools",
    "role": "developer",
    "tools": [
        {
            "type": "namespace",
            "name": "collaboration",
            "description": "Tools for spawning and managing sub-agents.",
            "tools": [
                {
                    "type": "function",
                    "name": "spawn_agent",
                    "description": "Spawn a sub-agent.",
                    "strict": False,
                    "parameters": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "task_name": {"type": "string"},
                            "message": {"type": "string", "encrypted": True},
                        },
                        "required": ["task_name", "message"],
                    },
                }
            ],
        }
    ],
}


def _reasoning(value):
    return {"type": "reasoning", "summary": [], "encrypted_content": value}


async def _round(codec, config, items, output, receipts=(), *, conversation=PARENT, **changes):
    """One send and release; returns the send result and the release receipt."""

    send = await _check(
        _send(items, conversation_id=conversation, encrypted_receipts=receipts, **changes), codec, config=config
    )
    if send.decision != "allow":
        return send, None
    release = await _check(_release(send.binding, output, conversation_id=conversation), codec, config=config)
    assert release.decision == "allow", release.reason_codes
    return send, release.receipt


async def _parent_receipt(codec, config, conversation=PARENT):
    send = await _check(_send([TOOLS, _user("start a helper")], conversation_id=conversation), codec, config=config)
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
        TOOLS,
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


@pytest.mark.anyio
async def test_an_active_conversation_keeps_verifying_its_whole_history():
    # Each response adds a reasoning item that every later request replays. The executor presents the latest
    # consolidated receipt and the release receipts since it, so the presented count stays constant.
    codec, config = BindingCodec(), _responses_config()
    history, stored = [_user("work")], []
    for turn in range(40):
        send, receipt = await _round(codec, config, history, [_reasoning(f"gAAAA-turn-{turn}")], tuple(stored))
        assert send.decision == "allow", (turn, send.reason_codes)
        assert len(stored) <= 2
        stored = [token for token in (send.receipt, receipt) if token]
        history.append(_reasoning(f"gAAAA-turn-{turn}"))
    final = await _check(_send(history, conversation_id=PARENT, encrypted_receipts=tuple(stored)), codec, config=config)
    assert final.decision == "allow"


@pytest.mark.anyio
async def test_a_subagent_keeps_its_task_after_the_parents_receipt_is_no_longer_presented():
    codec, config = BindingCodec(), _responses_config()
    parent = await _parent_receipt(codec, config)
    task = {"type": "agent_message", "content": [{"type": "encrypted_content", "encrypted_content": TASK}]}
    history, stored = [task], []
    for turn in range(40):
        presented = (*stored, parent) if turn == 0 else tuple(stored)
        send, receipt = await _round(
            codec,
            config,
            history,
            [_reasoning(f"gAAAA-child-{turn}")],
            presented,
            conversation=CHILD,
            parent_conversation_id=PARENT,
        )
        assert send.decision == "allow", (turn, send.reason_codes)
        stored = [token for token in (send.receipt, receipt) if token]
        history.append(_reasoning(f"gAAAA-child-{turn}"))


@pytest.mark.anyio
async def test_parent_continuation_keeps_a_pending_handovers_task_proof():
    # The parent keeps talking after spawning a child, so its next send consolidates and replaces the release
    # receipt that is the child's only proof of the task. The consolidated receipt must still cover it.
    codec, config = BindingCodec(), _responses_config()
    receipt = await _parent_receipt(codec, config)
    continuation = await _check(
        _send(
            [TOOLS, _user("start a helper"), _reasoning(REASONING), SPAWN, _user("keep going")],
            conversation_id=PARENT,
            encrypted_receipts=(receipt,),
        ),
        codec,
        config=config,
    )
    assert continuation.decision == "allow"
    task = {"type": "agent_message", "content": [{"type": "encrypted_content", "encrypted_content": TASK}]}
    child = await _check(
        _send(
            [task],
            conversation_id=CHILD,
            parent_conversation_id=PARENT,
            encrypted_receipts=(continuation.receipt,),
        ),
        codec,
        config=config,
    )
    assert child.decision == "allow"


@pytest.mark.anyio
async def test_ordinary_tool_arguments_do_not_displace_encrypted_values():
    codec, config = BindingCodec(), _responses_config()
    many = {
        "type": "function_call",
        "call_id": "call_tags",
        "name": "tag",
        "arguments": json.dumps({"tags": [f"ordinary-{i}" for i in range(2 * MAX_RECEIPT_VALUES)]}),
    }
    _, receipt = await _round(codec, config, [TOOLS, _user("tag")], [_reasoning(REASONING), many])
    assert receipt is not None
    replay = [TOOLS, _user("tag"), _reasoning(REASONING)]
    result = await _check(_send(replay, conversation_id=PARENT, encrypted_receipts=(receipt,)), codec, config=config)
    assert result.decision == "allow"


@pytest.mark.anyio
async def test_only_declared_encrypted_arguments_are_receipted():
    codec, config = BindingCodec(), _responses_config()
    # Without the encrypted declaration, the spawn_agent message is an ordinary argument and proves nothing.
    _, receipt = await _round(codec, config, [_user("start a helper")], [_reasoning(REASONING), SPAWN])
    task = {"type": "agent_message", "content": [{"type": "encrypted_content", "encrypted_content": TASK}]}
    child = await _check(
        _send([task], conversation_id=CHILD, parent_conversation_id=PARENT, encrypted_receipts=(receipt,)),
        codec,
        config=config,
    )
    assert child.reason_codes == ("encrypted-origin-unverified",)


@pytest.mark.anyio
async def test_an_undeclared_argument_of_a_declared_tool_proves_nothing():
    # spawn_agent declares only `message` encrypted; its `task_name` is an ordinary value.
    codec, config = BindingCodec(), _responses_config()
    receipt = await _parent_receipt(codec, config)
    for value, decision in (("helper", "withhold"), (TASK, "allow")):
        task = {"type": "agent_message", "content": [{"type": "encrypted_content", "encrypted_content": value}]}
        result = await _check(
            _send([task], conversation_id=CHILD, parent_conversation_id=PARENT, encrypted_receipts=(receipt,)),
            codec,
            config=config,
        )
        assert result.decision == decision, (value, result.reason_codes)


@pytest.mark.anyio
async def test_a_claimed_parent_lends_only_its_handover_values():
    # Any conversation can name a parent; it may only receive what the parent declared for handover, as its task.
    codec, config = BindingCodec(), _responses_config()
    receipt = await _parent_receipt(codec, config)
    parent_reasoning = {
        "type": "agent_message",
        "content": [{"type": "encrypted_content", "encrypted_content": REASONING}],
    }
    for items in ([_user("hi"), _reasoning(REASONING)], [parent_reasoning], [_user("hi"), _reasoning(TASK)]):
        result = await _check(
            _send(items, conversation_id=CHILD, parent_conversation_id=PARENT, encrypted_receipts=(receipt,)),
            codec,
            config=config,
        )
        assert result.reason_codes == ("encrypted-origin-unverified",), items


@pytest.mark.anyio
async def test_replayed_history_cannot_turn_a_reasoning_proof_into_a_handover_proof():
    codec, config = BindingCodec(), _responses_config()
    _, reasoning = await _round(codec, config, [TOOLS, _user("go")], [_reasoning(REASONING)])
    # The caller places the receipted reasoning ciphertext into a spawn_agent call the provider never released.
    forged = {**SPAWN, "arguments": json.dumps({"task_name": "helper", "message": REASONING})}
    history = [
        TOOLS,
        _user("go"),
        _reasoning(REASONING),
        forged,
        {"type": "function_call_output", "call_id": "call_spawn", "output": "started"},
    ]
    send, _ = await _round(codec, config, history, [], (reasoning,))
    assert send.decision == "allow" and send.receipt
    task = {"type": "agent_message", "content": [{"type": "encrypted_content", "encrypted_content": REASONING}]}
    child = await _check(
        _send([task], conversation_id=CHILD, parent_conversation_id=PARENT, encrypted_receipts=(send.receipt,)),
        codec,
        config=config,
    )
    assert child.reason_codes == ("encrypted-origin-unverified",)


@pytest.mark.anyio
async def test_a_divergent_history_keeps_the_proof_another_branch_needs():
    codec, config = BindingCodec(), _responses_config()
    _, both = await _round(codec, config, [_user("go")], [_reasoning("gAAAA-R"), _reasoning("gAAAA-X")])
    # A retry of an earlier history replays only R; its consolidated receipt replaces the one it presented.
    retry, _ = await _round(codec, config, [_user("go"), _reasoning("gAAAA-R")], [], (both,))
    assert retry.decision == "allow" and retry.receipt
    branch = [_user("go"), _reasoning("gAAAA-R"), _reasoning("gAAAA-X")]
    result = await _check(
        _send(branch, conversation_id=PARENT, encrypted_receipts=(retry.receipt,)), codec, config=config
    )
    assert result.decision == "allow"


@pytest.mark.anyio
async def test_a_carried_proof_keeps_its_original_expiry():
    codec, config = BindingCodec(), _responses_config()
    _, both = await _round(codec, config, [_user("go")], [_reasoning("gAAAA-R"), _reasoning("gAAAA-X")])
    half = NOW + RECEIPT_VALIDITY / 2
    used = await _check(
        _send([_user("go"), _reasoning("gAAAA-R")], conversation_id=PARENT, encrypted_receipts=(both,)),
        codec,
        config=config,
        now=half,
    )
    later = NOW + RECEIPT_VALIDITY + timedelta(minutes=1)
    for value, decision in (("gAAAA-R", "allow"), ("gAAAA-X", "withhold")):
        result = await _check(
            _send([_user("go"), _reasoning(value)], conversation_id=PARENT, encrypted_receipts=(used.receipt,)),
            codec,
            config=config,
            now=later,
        )
        assert result.decision == decision, value


@pytest.mark.anyio
@pytest.mark.parametrize(
    "extra",
    [
        {"image_url": "https://example.com/a.png"},
        {"content": [{"type": "input_file", "file_id": "file-1"}]},
    ],
)
async def test_an_admitted_part_with_other_fields_is_inspected_whole(extra):
    codec, config = BindingCodec(), _responses_config()
    receipt = await _parent_receipt(codec, config)
    part = {"type": "encrypted_content", "encrypted_content": TASK, **extra}
    task = {"type": "agent_message", "content": [part]}
    result = await _check(
        _send([task], conversation_id=CHILD, parent_conversation_id=PARENT, encrypted_receipts=(receipt,)),
        codec,
        config=config,
    )
    assert result.reason_codes == ("content_unsupported",)


@pytest.mark.anyio
async def test_a_full_receipt_fits_the_binding_codec_and_a_larger_request_is_withheld_explicitly():
    codec, config = BindingCodec(), _responses_config()
    values = [f"gAAAA-{i:05d}" for i in range(MAX_RECEIPT_VALUES + 1)]
    _, first = await _round(codec, config, [_user("a")], [_reasoning(v) for v in values[:MAX_RECEIPT_VALUES]])
    assert first is not None
    assert len(first) < codec.max_token_bytes
    _, second = await _round(codec, config, [_user("b")], [_reasoning(values[-1])])
    replay = [_user("a"), *(_reasoning(v) for v in values)]
    result = await _check(
        _send(replay, conversation_id=PARENT, encrypted_receipts=(first, second)), codec, config=config
    )
    assert result.decision == "withhold"
    assert result.reason_codes == ("encrypted-receipt-capacity-exceeded",)


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
