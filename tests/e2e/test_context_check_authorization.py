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

"""Public prepare/check never turn an encrypted binding into lasting authority."""

import asyncio
import json
from typing import Any

import pytest
from pydantic import SecretStr

from powercontext.builtin.inference.privacy import InferenceProtectionConfig, UpstreamProfile
from powercontext.builtin.runtime import InferenceConfig
from tests.e2e.test_access_control_regressions import _grant, _scope, _server
from tests.e2e.test_atomic_memory_prepare_lifecycle import _change, _remember, _revoke


def _inference():
    return InferenceConfig(
        protection=InferenceProtectionConfig(
            mode="enforcing",
            forbidden_values=(SecretStr("synthetic-forbidden-value"),),
            targets={
                "model": UpstreamProfile(
                    endpoint="https://model.example/v1/chat/completions",
                    model="test",
                    protocol="openai-chat",
                    context_window_tokens=65536,
                    max_output_tokens=64,
                    validated_utf8_token_bound=True,
                )
            },
        )
    )


def _send(scope_id, content, binding=None):
    return {
        "scope_id": scope_id,
        "phase": "before_send",
        "target_profile": "model",
        "payload": {
            "schema": "powercontext.model-json-payload.v1",
            "headers": {"content-type": "application/json"},
            "body": json.dumps({"model": "test", "max_tokens": 64, "messages": [{"role": "user", "content": content}]}),
        },
        "prepared_contexts": []
        if binding is None
        else [
            {
                "binding": binding,
                "location": {
                    "schema": "powercontext.model-json-text-location.v1",
                    "pointer": "/messages/0/content",
                    "start_byte": 0,
                    "end_byte": len(content.encode()),
                },
            }
        ],
    }


def test_public_responses_check_requires_explicit_non_storing_input(tmp_path):
    async def scenario():
        inference = _inference()
        target = inference.protection.targets["model"].model_copy(update={"protocol": "openai-responses"})
        inference = inference.model_copy(
            update={"protection": inference.protection.model_copy(update={"targets": {"model": target}})}
        )
        async with _server(tmp_path, inference=inference) as (_, client, _):
            scope_id = await _scope(client)
            request = _send(scope_id, "safe")
            body = {"model": "test", "max_output_tokens": 64, "input": "safe"}
            for storage in ({}, {"store": True}, {"store": None}, {"store": 0}, {"store": "false"}, {"store": False}):
                request["payload"]["body"] = json.dumps(body | storage)
                response = await client.post("/v1/context/check", json=request)
                assert response.status_code == 200, response.text
                result = response.json()
                if storage.get("store") is False:
                    assert result["decision"] == "allow" and result["coverage"]["complete"]
                    assert result["binding"]
                else:
                    assert result["decision"] == "withhold" and not result["coverage"]["complete"]
                    assert result["reason_codes"] == ["provider_state_unsupported"]
                    assert "binding" not in result

            for discriminator in ({"type": "item_reference"}, {}, {"type": None}):
                request["payload"]["body"] = json.dumps(
                    body | {"store": False, "input": [{"id": "msg_prior_private", **discriminator}]}
                )
                response = await client.post("/v1/context/check", json=request)
                assert response.status_code == 200, response.text
                result = response.json()
                assert result["decision"] == "withhold" and not result["coverage"]["complete"]
                assert result["reason_codes"] == ["content_unsupported"]
                assert "binding" not in result

            for expanded in (
                [{"role": "user", "content": "safe"}],
                [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "safe"}]}],
                [
                    {
                        "type": "message",
                        "id": "msg_expanded",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "safe", "annotations": []}],
                        "status": "completed",
                    },
                    {"type": "function_call", "call_id": "call", "name": "test", "arguments": '{"id":"user-data"}'},
                    {"type": "function_call_output", "call_id": "call", "output": "safe"},
                ],
            ):
                request["payload"]["body"] = json.dumps(body | {"store": False, "input": expanded})
                response = await client.post("/v1/context/check", json=request)
                assert response.status_code == 200, response.text
                result = response.json()
                assert result["decision"] == "allow" and result["coverage"]["complete"] and result["binding"]

    asyncio.run(scenario())


def test_public_anthropic_check_only_binds_inspectable_document_sources(tmp_path):
    async def scenario():
        inference = _inference()
        target = inference.protection.targets["model"].model_copy(update={"protocol": "anthropic-messages"})
        inference = inference.model_copy(
            update={"protection": inference.protection.model_copy(update={"targets": {"model": target}})}
        )
        sources = [
            ({"type": "base64", "media_type": "application/pdf", "data": "JVBERi0xLjQK"}, "withhold"),
            ({"type": "url", "url": "https://example.invalid/private.pdf"}, "withhold"),
            ({"type": "file", "file_id": "file_private"}, "withhold"),
            ({"type": "future-source"}, "withhold"),
            (None, "withhold"),
            ({"type": "text", "media_type": "text/plain"}, "withhold"),
            (
                {"type": "content", "content": [{"type": "image", "source": {"type": "url", "url": "image"}}]},
                "withhold",
            ),
            ({"type": "text", "media_type": "text/plain", "data": "safe"}, "allow"),
            ({"type": "content", "content": "safe"}, "allow"),
            ({"type": "content", "content": [{"type": "text", "text": "safe"}]}, "allow"),
            ({"type": "text", "media_type": "text/plain", "data": "synthetic-forbidden-value"}, "block"),
            (
                {
                    "type": "content",
                    "content": [{"type": "text", "text": "synthetic-"}, {"type": "text", "text": "forbidden-value"}],
                },
                "block",
            ),
        ]
        async with _server(tmp_path, inference=inference) as (_, client, _):
            scope_id = await _scope(client)
            for source, decision in sources:
                document = {"type": "document", "source": source}
                for content in ([document], [{"type": "tool_result", "tool_use_id": "tool", "content": [document]}]):
                    response = await client.post("/v1/context/check", json=_send(scope_id, content))
                    assert response.status_code == 200, response.text
                    result = response.json()
                    assert result["decision"] == decision
                    assert result["coverage"]["complete"] is (decision != "withhold")
                    if decision == "allow":
                        assert result["binding"]
                    else:
                        assert "binding" not in result
                        reason = "content_unsupported" if decision == "withhold" else "forbidden_content"
                        assert result["reason_codes"] == [reason]

    asyncio.run(scenario())


async def _prepare(client, scope_id, *, headers=None, reuse=True):
    return await client.post(
        "/v1/context/prepare",
        headers=headers,
        json={
            "scope_id": scope_id,
            "query": "alpha",
            "assembly": {"sections": [{"family": "memory", "limit": 8}]},
            "include_reuse": reuse,
        },
    )


def test_public_envelope_send_release_and_exact_revision_invalidation(tmp_path):
    async def scenario():
        async with _server(tmp_path, inference=_inference()) as (_, client, _):
            scope_id = await _scope(client)
            record = await _remember(client, scope_id, "Alpha deployment uses port 8123.")
            ordinary = await _prepare(client, scope_id, reuse=False)
            assert ordinary.status_code == 200, ordinary.text
            assert set(ordinary.json()) == {"schema", "status", "content", "content_bytes"}
            prepared = await _prepare(client, scope_id)
            assert prepared.status_code == 200, prepared.text
            envelope = prepared.json()
            assert envelope["schema"] == "powercontext.prepared-context-with-reuse.v1"
            request = _send(scope_id, envelope["context"]["content"], envelope["reuse"]["binding"])
            sent = await client.post("/v1/context/check", json=request)
            assert sent.status_code == 200, sent.text
            assert sent.json()["decision"] == "allow"
            assert sent.json()["coverage"]["complete"] is True
            release = {
                "scope_id": scope_id,
                "phase": "before_release",
                "target_profile": "model",
                "request_binding": sent.json()["binding"],
                "completion": "complete",
                "payload": {
                    "schema": "powercontext.model-json-payload.v1",
                    "headers": {"content-type": "application/json"},
                    "body": json.dumps({
                        "model": "test",
                        "choices": [
                            {
                                "message": {"role": "assistant", "content": "Use port 8123."},
                                "finish_reason": "stop",
                            }
                        ],
                    }),
                },
            }
            response = await client.post("/v1/context/check", json=release)
            assert response.status_code == 200, response.text
            assert response.json()["decision"] == "allow"
            assert "correlate_until" not in response.json()
            await _change(client, scope_id, record, "revise")
            stale = await client.post("/v1/context/check", json=request)
            assert stale.status_code == 200, stale.text
            assert stale.json()["decision"] == "withhold" and "binding" not in stale.json()
            stale_output = await client.post("/v1/context/check", json=release)
            assert stale_output.status_code == 200, stale_output.text
            assert stale_output.json()["decision"] == "withhold"

    asyncio.run(scenario())


def test_current_authorization_and_identity_are_required_for_each_check(tmp_path):
    async def scenario():
        async with _server(tmp_path, inference=_inference()) as (_, client, _):
            scope_id = await _scope(client)
            await _remember(client, scope_id, "Alpha uses isolated deployment.")
            grant = await _grant(client, scope_id, "alice", "scope.viewer")
            await _grant(client, scope_id, "bob", "scope.viewer")
            alice, bob = {"Authorization": "Bearer alice"}, {"Authorization": "Bearer bob"}
            prepared = await _prepare(client, scope_id, headers=alice)
            assert prepared.status_code == 200, prepared.text
            envelope = prepared.json()
            request = _send(scope_id, envelope["context"]["content"], envelope["reuse"]["binding"])
            wrong_identity = await client.post("/v1/context/check", headers=bob, json=request)
            assert wrong_identity.status_code == 200, wrong_identity.text
            assert wrong_identity.json()["decision"] == "withhold"
            good = await client.post("/v1/context/check", headers=alice, json=request)
            assert good.status_code == 200 and good.json()["decision"] == "allow", good.text
            await _revoke(client, grant)
            revoked = await client.post("/v1/context/check", headers=alice, json=request)
            assert revoked.status_code == 403, revoked.text

    asyncio.run(scenario())


def test_public_check_rejects_missing_bindings_and_blocks_without_reflecting_secrets(tmp_path):
    async def scenario():
        async with _server(tmp_path, inference=_inference()) as (_, client, _):
            scope_id = await _scope(client)
            await _remember(client, scope_id, "Alpha deployment.")
            prepared = await _prepare(client, scope_id)
            assert prepared.status_code == 200, prepared.text
            missing = await client.post(
                "/v1/context/check", json=_send(scope_id, prepared.json()["context"]["content"])
            )
            assert missing.status_code == 422, missing.text
            blocked = await client.post("/v1/context/check", json=_send(scope_id, "synthetic-forbidden-value"))
            assert blocked.status_code == 200, blocked.text
            assert blocked.json()["decision"] == "block" and "binding" not in blocked.json()
            assert "synthetic-forbidden-value" not in blocked.text
            invalid = _send(scope_id, "safe")
            invalid["request_binding"] = "wrong-phase"
            rejected = await client.post("/v1/context/check", json=invalid)
            assert rejected.status_code == 422, rejected.text
            for invalid in (
                _send(scope_id, "safe") | {"phase": "synthetic-sensitive-phase"},
                _send(scope_id, "safe") | {"synthetic-sensitive-field-name": "safe"},
            ):
                rejected = await client.post("/v1/context/check", json=invalid)
                assert rejected.status_code == 422
                assert "synthetic-sensitive" not in rejected.text

    asyncio.run(scenario())


@pytest.mark.parametrize("protocol", ["openai-chat", "openai-responses", "anthropic-messages"])
@pytest.mark.parametrize("assembled", [False, True], ids=["context-marker", "text-marker"])
def test_public_check_rejects_stale_context_markers_split_between_native_blocks(tmp_path, protocol, assembled):
    async def scenario():
        inference = _inference()
        target = inference.protection.targets["model"].model_copy(
            update={"protocol": protocol, "require_prepared_contexts": True}
        )
        inference = inference.model_copy(
            update={"protection": inference.protection.model_copy(update={"targets": {"model": target}})}
        )
        async with _server(tmp_path, inference=inference) as (_, client, _):
            scope_id = await _scope(client)
            record = await _remember(client, scope_id, "Alpha deployment uses port 8123.")
            prepare: dict[str, Any] = {"scope_id": scope_id, "query": "alpha", "include_reuse": True}
            if assembled:
                prepare["assembly"] = {"sections": [{"family": "memory", "limit": 8}]}
            old = await client.post("/v1/context/prepare", json=prepare)
            assert old.status_code == 200, old.text
            retained = old.json()
            await _change(client, scope_id, record, "revise")
            fresh = await client.post("/v1/context/prepare", json=prepare)
            assert fresh.status_code == 200, fresh.text
            current = fresh.json()
            collection = "input" if protocol == "openai-responses" else "messages"
            text_type = "input_text" if protocol == "openai-responses" else "text"

            async def check(envelope, extra=()):
                content = envelope["context"]["content"]
                body: dict[str, Any] = {
                    "model": "test",
                    collection: [
                        {
                            "role": "user",
                            "content": [
                                {"type": text_type, "text": "Continue using this history:\n"},
                                {"type": text_type, "text": content},
                                {"type": text_type, "text": "\nAnswer the current task."},
                            ],
                        }
                    ],
                }
                if protocol == "openai-responses":
                    body.update(max_output_tokens=64, store=False)
                else:
                    body["max_tokens"] = 64
                if extra:
                    if protocol == "anthropic-messages":
                        body["system"] = extra
                    else:
                        body[collection].append({"role": "system", "content": extra})
                request = _send(scope_id, content, envelope["reuse"]["binding"])
                request["payload"]["body"] = json.dumps(body)
                request["prepared_contexts"][0]["location"]["pointer"] = f"/{collection}/0/content/1/text"
                return await client.post("/v1/context/check", json=request)

            stale = await check(retained)
            assert stale.status_code == 200 and stale.json()["decision"] == "withhold", stale.text
            assert stale.json()["reason_codes"] == ["context-refresh-required"]
            allowed = await check(current)
            assert allowed.status_code == 200 and allowed.json()["decision"] == "allow", allowed.text
            text = retained["context"]["content"]
            whole = await check(current, [{"type": text_type, "text": text}])
            assert whole.status_code == 422, whole.text
            boundary = text.index("BEGIN_POWERCONTEXT_") + len("BEGIN_PO")
            for chunks in (
                (text[:boundary], text[boundary:]),
                (text[:boundary], "", text[boundary : boundary + 1], text[boundary + 1 :]),
            ):
                split = await check(current, [{"type": text_type, "text": chunk} for chunk in chunks])
                assert split.status_code == 422, split.json().get("decision")
                assert split.json()["error"]["code"] == "invalid_request"

    asyncio.run(scenario())
