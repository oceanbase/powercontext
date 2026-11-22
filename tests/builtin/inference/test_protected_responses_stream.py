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
from collections.abc import Sequence
from typing import Any

import httpx2
import pytest
from openai import AsyncOpenAI
from pydantic import SecretStr
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings
from pydantic_ai.providers.openai import OpenAIProvider

from powercontext.builtin.inference.errors import InferencePrivacyError
from powercontext.builtin.inference.privacy import InferenceProtectionConfig, UpstreamProfile
from powercontext.builtin.inference.protected_transport import ProtectedAsyncTransport
from powercontext.builtin.inference.usage import collect_approved_usage

SECRET = "synthetic-forbidden-secret"  # noqa: S105 - synthetic forbidden-value fixture
ENDPOINT = "https://example.invalid/v1/responses"


def protection(*, forbidden_value: str = SECRET, **limits: Any) -> InferenceProtectionConfig:
    return InferenceProtectionConfig(
        mode="enforcing",
        forbidden_values=(SecretStr(forbidden_value),),
        targets={
            "test": UpstreamProfile(
                endpoint=ENDPOINT,
                model="test-model",
                protocol="openai-responses",
                allow_streaming=True,
                context_window_tokens=100000,
                max_output_tokens=256,
                validated_utf8_token_bound=True,
            )
        },
        **limits,
    )


def message(text: str, *, item_id: str = "msg_test") -> dict[str, Any]:
    return {
        "id": item_id,
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text, "annotations": []}],
    }


def response(output: list[dict[str, Any]], *, status: str = "completed") -> dict[str, Any]:
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 0,
        "model": "test-model",
        "status": status,
        "output": output,
        "usage": {
            "input_tokens": 12,
            "output_tokens": 7,
            "total_tokens": 19,
            "input_tokens_details": {"cached_tokens": 8},
            "output_tokens_details": {"reasoning_tokens": 3},
        },
    }


def events(*pieces: str, tool: bool = False) -> list[dict[str, Any]]:
    final = (
        {
            "type": "function_call",
            "id": "fc_test",
            "call_id": "call_test",
            "name": "lookup",
            "arguments": "".join(pieces),
            "status": "completed",
        }
        if tool
        else message("".join(pieces))
    )
    initial = (
        {**final, "arguments": "", "status": "in_progress"}
        if tool
        else {**final, "content": [], "status": "in_progress"}
    )
    result = [
        {"type": "response.created", "response": response([], status="in_progress")},
        {"type": "response.output_item.added", "output_index": 0, "item": initial},
    ]
    if not tool:
        result.append({
            "type": "response.content_part.added",
            "output_index": 0,
            "content_index": 0,
            "item_id": final["id"],
            "part": {"type": "output_text", "text": "", "annotations": []},
        })
    result.extend(
        {
            "type": "response.function_call_arguments.delta" if tool else "response.output_text.delta",
            "output_index": 0,
            "content_index": 0,
            "item_id": final["id"],
            "delta": piece,
        }
        for piece in pieces
    )
    result.extend([
        {"type": "response.output_item.done", "output_index": 0, "item": final},
        {"type": "response.completed", "response": response([final])},
    ])
    return result


def sse(values: Sequence[dict[str, Any] | str]) -> bytes:
    return "".join(f"data: {value if isinstance(value, str) else json.dumps(value)}\n\n" for value in values).encode()


async def consume(
    raw: bytes,
    *,
    sdk_stream: bool = False,
    pydantic_stream: bool = False,
    calls: list[httpx2.Request] | None = None,
    response_stream: httpx2.AsyncByteStream | None = None,
    snapshots: list[str] | None = None,
    **limits: Any,
):
    calls = [] if calls is None else calls

    def upstream(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)
        return httpx2.Response(
            200,
            content=raw if response_stream is None else None,
            stream=response_stream,
            headers={"content-type": "text/event-stream; charset=utf-8", "x-request-id": "req_fixture"},
        )

    async with (
        httpx2.AsyncClient(
            transport=ProtectedAsyncTransport(protection(**limits), transport=httpx2.MockTransport(upstream))
        ) as http,
        AsyncOpenAI(
            base_url=ENDPOINT.removesuffix("/responses"), api_key="fixture", max_retries=0, http_client=http
        ) as sdk,
    ):
        with collect_approved_usage() as usage:
            if pydantic_stream:
                model = OpenAIResponsesModel("test-model", provider=OpenAIProvider(openai_client=sdk))
                async with model.request_stream(
                    [ModelRequest(parts=[UserPromptPart("clean")])],
                    OpenAIResponsesModelSettings(max_tokens=10, openai_store=False),
                    ModelRequestParameters(),
                ) as model_stream:
                    async for _ in model_stream:
                        pass
                    result = model_stream.get()
            elif sdk_stream:
                async with sdk.responses.stream(
                    model="test-model", input="clean", max_output_tokens=10, store=False
                ) as stream:
                    if snapshots is not None:
                        async for event in stream:
                            if event.type == "response.output_text.delta":
                                snapshots.append(event.snapshot)
                    result = await stream.get_final_response()
            else:
                result = await http.post(
                    ENDPOINT,
                    json={
                        "model": "test-model",
                        "input": "clean",
                        "max_output_tokens": 10,
                        "store": False,
                        "stream": True,
                    },
                )
            return result, usage, calls


@pytest.mark.parametrize("tool", [False, True])
def test_completed_responses_stream_reaches_sdk_with_usage(tool: bool) -> None:
    raw = sse(events('{"value":', '"ok"}', tool=tool))
    result, usage, calls = asyncio.run(consume(raw, sdk_stream=True))
    assert result.status == "completed"
    if tool:
        assert result.output[0].arguments == '{"value":"ok"}'
    else:
        assert result.output_text == '{"value":"ok"}'
    assert len(calls) == len(usage) == 1
    assert usage[0].input_tokens == 12 and usage[0].output_tokens == 7
    assert usage[0].cache_read_tokens == 8 and usage[0].reasoning_tokens == 3
    assert not usage[0].incomplete


def test_approved_stream_preserves_bytes_status_and_headers() -> None:
    raw = sse(events("safe")) + b"data: [DONE]\n\n"
    result, _, calls = asyncio.run(consume(raw))
    assert result.status_code == 200 and result.content == raw
    assert result.headers["content-type"] == "text/event-stream; charset=utf-8"
    assert result.headers["x-request-id"] == "req_fixture" and len(calls) == 1


@pytest.mark.parametrize("tool", [False, True])
@pytest.mark.parametrize(
    "pieces", [("synthetic-forbidden-", "secret"), ('{"value":"synthetic-forbidden-', '\\u0073ecret"}')]
)
def test_fragments_remain_checked_when_terminal_response_omits_them(tool: bool, pieces: tuple[str, str]) -> None:
    values = events(*pieces, tool=tool)
    values[-2]["item"] = message("safe")
    values[-1]["response"] = response([message("safe")])
    with pytest.raises(InferencePrivacyError) as caught:
        asyncio.run(consume(sse(values)))
    assert caught.value.decision == "block" and caught.value.reason_codes == ("forbidden_content",)


@pytest.mark.parametrize("terminal", [None, "response.failed", "response.incomplete", "error"])
def test_unfinished_and_error_streams_are_withheld(terminal: str | None) -> None:
    values = events("safe")[:-1]
    if terminal:
        values.append({"type": terminal, "response": response([], status="failed")})
    with pytest.raises(InferencePrivacyError) as caught:
        asyncio.run(consume(sse(values)))
    assert caught.value.phase == "before_release" and caught.value.decision == "withhold"
    assert caught.value.reason_codes != ("streaming_unsupported",)


@pytest.mark.parametrize("tool", [False, True])
def test_completed_snapshot_can_supply_output_without_deltas(tool: bool) -> None:
    values = events('{"value":"ok"}', tool=tool)
    values = [event for event in values if not event["type"].endswith(".delta")]
    result, _, _ = asyncio.run(consume(sse(values), sdk_stream=True))
    assert result.status == "completed"


def test_response_settings_are_not_misclassified_as_opaque_content() -> None:
    values = events("safe")
    for value in values:
        if "response" in value:
            value["response"]["image"] = {"detail": "auto"}
    result, _, _ = asyncio.run(consume(sse(values), sdk_stream=True))
    assert result.output_text == "safe"


@pytest.mark.parametrize("field", ["content_index", "item_id", "sequence_number"])
def test_tool_argument_join_uses_sdk_output_identity(field: str) -> None:
    values = events('{"value":"synthetic-forbidden-', 'secret"}', tool=True)
    deltas = [event for event in values if event["type"].endswith(".delta")]
    deltas[1][field] = 1 if field != "item_id" else "untrusted-metadata"
    values[-2]["item"]["arguments"] = "{}"
    values[-1]["response"]["output"][0]["arguments"] = "{}"
    with pytest.raises(InferencePrivacyError) as caught:
        asyncio.run(consume(sse(values)))
    assert caught.value.decision == "block"


def test_text_is_joined_across_output_items_before_nested_json_inspection() -> None:
    values = [
        {
            "type": "response.output_text.delta",
            "output_index": 0,
            "content_index": 0,
            "delta": '{"value":"synthetic-forbidden-',
        },
        {"type": "response.output_text.delta", "output_index": 1, "content_index": 0, "delta": '\\u0073ecret"}'},
        {"type": "response.completed", "response": response([message("safe")])},
    ]
    with pytest.raises(InferencePrivacyError) as caught:
        asyncio.run(consume(sse(values)))
    assert caught.value.decision == "block"


def test_initial_text_and_deltas_are_checked_together() -> None:
    values = events("secret")
    values[2]["part"]["text"] = "synthetic-forbidden-"
    values[-2]["item"] = message("safe")
    values[-1]["response"] = response([message("safe")])
    with pytest.raises(InferencePrivacyError) as caught:
        asyncio.run(consume(sse(values)))
    assert caught.value.decision == "block"


@pytest.mark.parametrize(
    ("extra", "reason"),
    [
        (
            '{"type":"response.output_text.delta","output_index":0,"delta":"safe","delta":"also safe"}',
            "json_duplicate_key",
        ),
        (
            {
                "type": "response.content_part.added",
                "output_index": 0,
                "content_index": 0,
                "part": {"type": "output_audio", "audio": "AAAA"},
            },
            "content_unsupported",
        ),
        (
            {
                "type": "response.output_item.added",
                "output_index": 1,
                "item": {"type": "reasoning", "encrypted_content": "AAAA"},
            },
            "content_unsupported",
        ),
        ({"type": "response.output_audio.delta", "output_index": 0, "delta": "AAAA"}, "content_unsupported"),
    ],
)
def test_duplicate_and_opaque_events_are_withheld(extra: dict[str, Any] | str, reason: str) -> None:
    with pytest.raises(InferencePrivacyError) as caught:
        asyncio.run(consume(sse([extra, *events("safe")])))
    assert caught.value.decision == "withhold" and caught.value.reason_codes == (reason,)


@pytest.mark.parametrize("arguments", ['{"value":', '{"value":1,"value":2}'])
def test_incomplete_or_ambiguous_joined_tool_arguments_are_withheld(arguments: str) -> None:
    values = events(arguments, tool=True)
    values[-2]["item"]["arguments"] = "{}"
    values[-1]["response"]["output"][0]["arguments"] = "{}"
    with pytest.raises(InferencePrivacyError) as caught:
        asyncio.run(consume(sse(values)))
    assert caught.value.decision == "withhold"
    assert caught.value.reason_codes in {("tool_arguments_incomplete",), ("json_duplicate_key",)}


@pytest.mark.parametrize(
    "suffix",
    [
        sse([{"type": "response.output_text.delta", "output_index": 0, "content_index": 0, "delta": "later"}]),
        sse(events("second")),
        b"data: [DONE]\n\ndata: [DONE]\n\n",
    ],
)
def test_data_after_completed_response_is_withheld(suffix: bytes) -> None:
    with pytest.raises(InferencePrivacyError) as caught:
        asyncio.run(consume(sse(events("safe")) + suffix))
    assert caught.value.reason_codes == ("sse_data_after_terminal",)


def test_eof_without_final_sse_frame_delimiter_is_withheld() -> None:
    with pytest.raises(InferencePrivacyError) as caught:
        asyncio.run(consume(sse(events("safe"))[:-1]))
    assert caught.value.reason_codes == ("sse_incomplete_or_too_large",)


@pytest.mark.parametrize(
    "changes", [{"model": "unapproved-model"}, {"status": "incomplete"}, {"error": {"message": "failed"}}]
)
def test_terminal_response_requires_final_policy_approval(changes: dict[str, Any]) -> None:
    values = events("safe")
    values[-1]["response"].update(changes)
    with pytest.raises(InferencePrivacyError) as caught:
        asyncio.run(consume(sse(values)))
    assert caught.value.decision == "withhold"
    assert caught.value.usage is not None and caught.value.usage.input_tokens == 12


def test_response_byte_limit_applies_before_release() -> None:
    with pytest.raises(InferencePrivacyError) as caught:
        asyncio.run(consume(sse(events("safe")), max_response_bytes=256))
    assert caught.value.reason_codes == ("payload_too_large",)


def test_response_depth_limit_applies_to_stream_events() -> None:
    values = events("safe")
    values[0]["metadata"] = {"outer": {"inner": {"value": "safe"}}}
    with pytest.raises(InferencePrivacyError) as caught:
        asyncio.run(consume(sse(values), max_json_depth=3))
    assert caught.value.phase == "before_release" and caught.value.decision == "withhold"


def test_unicode_line_separators_remain_literal_content() -> None:
    raw = sse(events("line\u2028separator")).replace(b"\\u2028", "\u2028".encode())
    result, _, _ = asyncio.run(consume(raw, sdk_stream=True))
    assert result.output_text == "line\u2028separator"


def test_output_text_join_ignores_unrelated_summary_index() -> None:
    values = events("synthetic-forbidden-", "secret")
    values[4]["summary_index"] = 1
    values[-2]["item"] = message("safe")
    values[-1]["response"] = response([message("safe")])
    with pytest.raises(InferencePrivacyError) as caught:
        asyncio.run(consume(sse(values)))
    assert caught.value.decision == "block"


def test_output_text_join_follows_sdk_item_order_with_sparse_indices() -> None:
    values = [
        {"type": "response.output_item.added", "output_index": index, "item": message(text, item_id=f"msg_{index}")}
        for index, text in [(5, "synthetic-forbidden-"), (1, "secret")]
    ]
    values.append({"type": "response.completed", "response": response([message("safe")])})
    with pytest.raises(InferencePrivacyError) as caught:
        asyncio.run(consume(sse(values)))
    assert caught.value.decision == "block"


def test_summary_initial_text_and_delta_are_checked_together() -> None:
    values = [
        {
            "type": "response.reasoning_summary_part.added",
            "output_index": 0,
            "summary_index": 0,
            "part": {"type": "summary_text", "text": "synthetic-forbidden-"},
        },
        {"type": "response.reasoning_summary_text.delta", "output_index": 0, "summary_index": 0, "delta": "secret"},
        {"type": "response.completed", "response": response([message("safe")])},
    ]
    with pytest.raises(InferencePrivacyError) as caught:
        asyncio.run(consume(sse(values)))
    assert caught.value.decision == "block"


@pytest.mark.parametrize("terminal", [None, "response.failed"])
def test_sdk_does_not_retry_a_rejected_stream(terminal: str | None) -> None:
    values = events("safe")[:-1]
    if terminal:
        values.append({"type": terminal, "response": response([], status="failed")})
    calls: list[httpx2.Request] = []
    with pytest.raises(InferencePrivacyError):
        asyncio.run(consume(sse(values), sdk_stream=True, calls=calls))
    assert len(calls) == 1


@pytest.mark.parametrize("failure", ["disconnect", "timeout"])
def test_interrupted_stream_is_closed_and_not_retried(failure: str) -> None:
    class Interrupted(httpx2.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield sse(events("safe")[:-1])
            if failure == "disconnect":
                raise httpx2.ReadError("disconnect")
            await asyncio.sleep(1)

        async def aclose(self):
            self.closed = True

    stream = Interrupted()
    calls: list[httpx2.Request] = []
    with pytest.raises(InferencePrivacyError) as caught:
        asyncio.run(consume(b"", sdk_stream=True, calls=calls, response_stream=stream, response_timeout_seconds=0.01))
    assert caught.value.reason_codes == ("send_or_response_uncertain",)
    assert caught.value.usage is not None and caught.value.usage.incomplete
    assert len(calls) == 1 and stream.closed


def test_terminal_without_usage_keeps_counts_unknown() -> None:
    values = events("safe")
    values[-1]["response"].pop("usage")
    _, usage, _ = asyncio.run(consume(sse(values)))
    assert usage[0].input_tokens is None and usage[0].output_tokens is None and usage[0].incomplete


def test_sse_event_limit_is_enforced() -> None:
    raw = sse([{"type": "response.in_progress", "response": response([], status="in_progress")}] * 16385)
    with pytest.raises(InferencePrivacyError) as caught:
        asyncio.run(consume(raw, max_response_bytes=8388608))
    assert caught.value.reason_codes == ("sse_incomplete_or_too_large",)


def test_content_part_added_uses_sdk_append_position_before_release() -> None:
    values = events("secret")
    values[2]["part"]["text"] = "synthetic-forbidden-"
    values[2]["content_index"] = 5
    values[-2]["item"] = message("safe")
    values[-1]["response"] = response([message("safe")])
    for sequence_number, event in enumerate(values):
        event["sequence_number"] = sequence_number
        if event["type"] == "response.output_text.delta":
            event["logprobs"] = []
    snapshots: list[str] = []
    with pytest.raises(InferencePrivacyError) as caught:
        asyncio.run(consume(sse(values), sdk_stream=True, snapshots=snapshots))
    assert caught.value.decision == "block"
    assert not snapshots


@pytest.mark.parametrize("tool", [False, True])
def test_created_response_seeds_initial_content_before_deltas(tool: bool) -> None:
    values = events('secret"}' if tool else "secret", tool=tool)
    seed = (
        {**values[-2]["item"], "arguments": '{"value":"synthetic-forbidden-'}
        if tool
        else message("synthetic-forbidden-")
    )
    values[0]["response"]["output"] = [seed]
    values[-2]["item"] = {**seed, "arguments": "{}"} if tool else message("safe")
    values[-1]["response"]["output"] = [values[-2]["item"]]
    values = [values[0], *values[2 if tool else 3 :]]
    for sequence_number, event in enumerate(values):
        event["sequence_number"] = sequence_number
        if event["type"] == "response.output_text.delta":
            event["logprobs"] = []
    snapshots: list[str] = []
    with pytest.raises(InferencePrivacyError) as caught:
        asyncio.run(consume(sse(values), sdk_stream=True, snapshots=snapshots))
    assert caught.value.decision == "block"
    assert not snapshots


@pytest.mark.parametrize("shared_id", ["msg_a", 7, None], ids=["string_id", "numeric_id", "null_id"])
def test_pydantic_ai_cannot_join_one_item_across_conflicting_output_positions(shared_id: str | int | None) -> None:
    values: list[dict[str, Any]] = [{"type": "response.created", "response": response([], status="in_progress")}]
    values.extend(
        {
            "type": "response.output_text.delta",
            "output_index": index,
            "item_id": item_id,
            "content_index": 0,
            "delta": text,
            "logprobs": [],
        }
        for index, item_id, text in [
            (0, shared_id, "synthetic-forbidden-"),
            (1, "msg_b", "padding"),
            (2, shared_id, "secret"),
        ]
    )
    values.append({"type": "response.completed", "response": response([message("safe")])})
    for sequence_number, event in enumerate(values):
        event["sequence_number"] = sequence_number
    calls: list[httpx2.Request] = []
    with pytest.raises(InferencePrivacyError) as caught:
        asyncio.run(consume(sse(values), pydantic_stream=True, calls=calls))
    assert caught.value.phase == "before_release" and caught.value.decision == "withhold"
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["changed_id", "reused_id"])
def test_output_item_identity_must_remain_consistent(change: str) -> None:
    values = events("safe")
    if change == "changed_id":
        values[3]["item_id"] = "msg_other"
    else:
        values.insert(-1, {"type": "response.output_item.done", "output_index": 1, "item": message("safe")})
    with pytest.raises(InferencePrivacyError) as caught:
        asyncio.run(consume(sse(values)))
    assert caught.value.reason_codes == ("output_identity_unsupported",)


@pytest.mark.parametrize("done_seed", [False, True])
def test_pydantic_ai_global_refusal_text_is_checked_across_items(done_seed: bool) -> None:
    values: list[dict[str, Any]] = [
        {"type": "response.created", "response": response([], status="in_progress")},
        {
            "type": "response.refusal.done" if done_seed else "response.refusal.delta",
            "output_index": 0,
            "content_index": 0,
            "item_id": "msg_a",
            "refusal" if done_seed else "delta": "synthetic-forbidden-",
        },
        {
            "type": "response.refusal.delta",
            "output_index": 1,
            "content_index": 0,
            "item_id": "msg_b",
            "delta": "secret",
        },
        {"type": "response.completed", "response": response([message("safe")])},
    ]
    for sequence_number, event in enumerate(values):
        event["sequence_number"] = sequence_number
    with pytest.raises(InferencePrivacyError) as caught:
        asyncio.run(consume(sse(values), pydantic_stream=True))
    assert caught.value.decision == "block"


@pytest.mark.parametrize("forbidden", [False, True])
def test_pydantic_ai_thinking_aggregate_preserves_summary_separators(forbidden: bool) -> None:
    values: list[dict[str, Any]] = [{"type": "response.created", "response": response([], status="in_progress")}]
    values.extend(
        {
            "type": "response.reasoning_summary_text.delta",
            "output_index": 0,
            "summary_index": index,
            "item_id": "rs_test",
            "delta": text,
        }
        for index, text in enumerate(["synthetic-forbidden-", "secret"])
    )
    values.append({"type": "response.completed", "response": response([message("safe")])})
    for sequence_number, event in enumerate(values):
        event["sequence_number"] = sequence_number
    if forbidden:
        with pytest.raises(InferencePrivacyError) as caught:
            asyncio.run(consume(sse(values), pydantic_stream=True, forbidden_value="synthetic-forbidden-\n\nsecret"))
        assert caught.value.decision == "block"
    else:
        result, _, _ = asyncio.run(consume(sse(values), pydantic_stream=True))
        assert result.thinking == "synthetic-forbidden-\n\nsecret"


@pytest.mark.parametrize("barrier", ["thinking", "tool"])
@pytest.mark.parametrize("forbidden", [False, True])
def test_pydantic_ai_text_aggregate_preserves_nontext_separators(barrier: str, forbidden: bool) -> None:
    values: list[dict[str, Any]] = [
        {"type": "response.created", "response": response([], status="in_progress")},
        {
            "type": "response.output_text.delta",
            "output_index": 0,
            "content_index": 0,
            "item_id": "msg_a",
            "delta": "synthetic-forbidden-",
            "logprobs": [],
        },
    ]
    if barrier == "thinking":
        values.append({
            "type": "response.reasoning_summary_text.delta",
            "output_index": 1,
            "summary_index": 0,
            "item_id": "rs_test",
            "delta": "explanation",
        })
    else:
        values.append({
            "type": "response.output_item.added",
            "output_index": 1,
            "item": {
                "type": "function_call",
                "id": "fc_test",
                "call_id": "call_test",
                "name": "lookup",
                "arguments": "{}",
            },
        })
    values.extend([
        {
            "type": "response.output_text.delta",
            "output_index": 2,
            "content_index": 0,
            "item_id": "msg_b",
            "delta": "secret",
            "logprobs": [],
        },
        {"type": "response.completed", "response": response([message("safe")])},
    ])
    for sequence_number, event in enumerate(values):
        event["sequence_number"] = sequence_number
    if forbidden:
        with pytest.raises(InferencePrivacyError) as caught:
            asyncio.run(consume(sse(values), pydantic_stream=True, forbidden_value="synthetic-forbidden-\n\nsecret"))
        assert caught.value.decision == "block"
    else:
        result, _, _ = asyncio.run(consume(sse(values), pydantic_stream=True, forbidden_value="not-present"))
        assert result.text == "synthetic-forbidden-\n\nsecret"


@pytest.mark.parametrize("pydantic_stream", [False, True])
def test_parallel_tools_with_interleaved_deltas_reach_supported_sdks(pydantic_stream: bool) -> None:
    items = [
        {
            "type": "function_call",
            "id": f"fc_{index}",
            "call_id": f"call_{index}",
            "name": "lookup",
            "arguments": json.dumps({"city": city}),
            "status": "completed",
        }
        for index, city in enumerate(["Paris", "London"])
    ]
    values: list[dict[str, Any]] = [{"type": "response.created", "response": response([], status="in_progress")}]
    values.extend(
        {
            "type": "response.output_item.added",
            "output_index": index,
            "item": {**item, "arguments": "", "status": "in_progress"},
        }
        for index, item in enumerate(items)
    )
    for index, delta in [(0, '{"city":'), (1, '{"city":'), (0, ' "Paris"}'), (1, ' "London"}')]:
        values.append({
            "type": "response.function_call_arguments.delta",
            "output_index": index,
            "item_id": items[index]["id"],
            "delta": delta,
        })
    values.extend(
        {"type": "response.output_item.done", "output_index": index, "item": item} for index, item in enumerate(items)
    )
    values.append({"type": "response.completed", "response": response(items)})
    for sequence_number, event in enumerate(values):
        event["sequence_number"] = sequence_number
    result, usage, calls = asyncio.run(
        consume(sse(values), sdk_stream=not pydantic_stream, pydantic_stream=pydantic_stream)
    )
    arguments = (
        [call.args_as_dict() for call in result.tool_calls]
        if pydantic_stream
        else [json.loads(item.arguments) for item in result.output]
    )
    assert arguments == [{"city": "Paris"}, {"city": "London"}]
    assert len(usage) == len(calls) == 1 and usage[0].input_tokens == 12


def test_normal_refusal_is_preserved_for_pydantic_ai() -> None:
    values = [
        {"type": "response.created", "response": response([], status="in_progress"), "sequence_number": 0},
        {
            "type": "response.refusal.delta",
            "output_index": 0,
            "content_index": 0,
            "item_id": "msg_a",
            "delta": "Cannot help.",
            "sequence_number": 1,
        },
        {
            "type": "response.refusal.done",
            "output_index": 0,
            "content_index": 0,
            "item_id": "msg_a",
            "refusal": "Cannot help.",
            "sequence_number": 2,
        },
        {"type": "response.completed", "response": response([message("safe")]), "sequence_number": 3},
    ]
    result, _, _ = asyncio.run(consume(sse(values), pydantic_stream=True))
    assert result.provider_details["refusal"] == "Cannot help."


def summary_events(second_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = [
        {
            "type": "reasoning",
            "id": "rs_test",
            "summary": [{"type": "summary_text", "text": text} for text in ["padding", "sec"]],
        },
        {"type": "reasoning", "id": second_id, "summary": [{"type": "summary_text", "text": "ret"}]},
    ]
    values: list[dict[str, Any]] = [{"type": "response.created", "response": response([], status="in_progress")}]
    for output_index, item in enumerate(items):
        values.append({
            "type": "response.output_item.added",
            "output_index": output_index,
            "item": {**item, "summary": []},
        })
        for summary_index, part in enumerate(item["summary"]):
            values.append({
                "type": "response.reasoning_summary_part.added",
                "output_index": output_index,
                "item_id": item["id"],
                "summary_index": summary_index,
                "part": {"type": "summary_text", "text": ""},
            })
            values.append({
                "type": "response.reasoning_summary_text.delta",
                "output_index": output_index,
                "item_id": item["id"],
                "summary_index": summary_index,
                "delta": part["text"],
            })
        values.append({"type": "response.output_item.done", "output_index": output_index, "item": item})
    values.append({"type": "response.completed", "response": response(items)})
    for sequence_number, event in enumerate(values):
        event["sequence_number"] = sequence_number
    return values


@pytest.mark.parametrize("second_id", ["rs_test-1", "rs_other", "rs_test-2"])
def test_sdk_derived_summary_identity_collisions_are_withheld(second_id: str) -> None:
    values = summary_events(second_id)
    if second_id == "rs_test-1":
        calls: list[httpx2.Request] = []
        with pytest.raises(InferencePrivacyError) as caught:
            asyncio.run(consume(sse(values), pydantic_stream=True, calls=calls, forbidden_value="secret"))
        assert caught.value.phase == "before_release" and caught.value.decision == "withhold"
        assert len(calls) == 1
    else:
        result, usage, _ = asyncio.run(consume(sse(values), pydantic_stream=True, forbidden_value="secret"))
        assert result.thinking == "padding\n\nsec\n\nret"
        assert len(usage) == 1 and usage[0].input_tokens == 12


@pytest.mark.parametrize("function_id", ["web_test-call", "web_test-function"])
def test_sdk_native_and_function_tool_keys_share_one_namespace(function_id: str) -> None:
    items = [
        {
            "type": "web_search_call",
            "id": "web_test",
            "status": "completed",
            "action": {"type": "search", "query": "safe"},
        },
        {
            "type": "function_call",
            "id": function_id,
            "call_id": "call_test",
            "name": "lookup",
            "arguments": "{}",
            "status": "completed",
        },
    ]
    values: list[dict[str, Any]] = [{"type": "response.created", "response": response([], status="in_progress")}]
    for index, item in enumerate(items):
        values.append({"type": "response.output_item.added", "output_index": index, "item": item})
        values.append({"type": "response.output_item.done", "output_index": index, "item": item})
    values.append({"type": "response.completed", "response": response(items)})
    for sequence_number, event in enumerate(values):
        event["sequence_number"] = sequence_number
    if function_id == "web_test-call":
        with pytest.raises(InferencePrivacyError) as caught:
            asyncio.run(consume(sse(values), pydantic_stream=True))
        assert caught.value.phase == "before_release" and caught.value.decision == "withhold"
    else:
        result, _, _ = asyncio.run(consume(sse(values), pydantic_stream=True))
        assert result.tool_calls[0].args_as_dict() == {}


def test_sdk_text_tuple_does_not_collide_with_reasoning_string_key() -> None:
    values = [event for event in summary_events("rs_other") if event.get("output_index") != 1][:-1]
    text_values = events("ret")
    for event in text_values[1:]:
        if "output_index" in event:
            event["output_index"] = 1
        if "item" in event:
            event["item"]["id"] = "rs_test-1"
        if "item_id" in event:
            event["item_id"] = "rs_test-1"
        if event["type"] == "response.output_text.delta":
            event["logprobs"] = []
        values.append(event)
    for sequence_number, event in enumerate(values):
        event["sequence_number"] = sequence_number
    result, _, _ = asyncio.run(consume(sse(values), pydantic_stream=True, forbidden_value="secret"))
    assert result.thinking == "padding\n\nsec" and result.text == "ret"


@pytest.mark.parametrize("reasoning_id", ["mcp_test-return", "mcp_test-other"])
def test_sdk_terminal_mcp_backfill_uses_the_return_key_namespace(reasoning_id: str) -> None:
    values = [event for event in summary_events("rs_other") if event.get("output_index") != 1][:-1]
    for event in values:
        if "item" in event:
            event["item"]["id"] = reasoning_id
        if "item_id" in event:
            event["item_id"] = reasoning_id
    reasoning = values[-1]["item"]
    tools = {
        "type": "mcp_list_tools",
        "id": "mcp_test",
        "server_label": "public",
        "tools": [{"name": "lookup", "input_schema": {"type": "object"}}],
    }
    values.append({"type": "response.completed", "response": response([reasoning, tools])})
    for sequence_number, event in enumerate(values):
        event["sequence_number"] = sequence_number
    if reasoning_id == "mcp_test-return":
        with pytest.raises(InferencePrivacyError) as caught:
            asyncio.run(consume(sse(values), pydantic_stream=True))
        assert caught.value.phase == "before_release" and caught.value.decision == "withhold"
    else:
        result, _, _ = asyncio.run(consume(sse(values), pydantic_stream=True))
        assert result.thinking == "padding\n\nsec"


def test_sdk_raw_reasoning_and_summary_zero_share_one_logical_part() -> None:
    item = {"type": "reasoning", "id": "rs_test-1", "summary": [{"type": "summary_text", "text": "summary"}]}
    values: list[dict[str, Any]] = [
        {"type": "response.created", "response": response([], status="in_progress")},
        {"type": "response.output_item.added", "output_index": 0, "item": {**item, "summary": []}},
        {
            "type": "response.reasoning_text.delta",
            "output_index": 0,
            "content_index": 0,
            "item_id": item["id"],
            "delta": "first",
        },
        {
            "type": "response.reasoning_text.delta",
            "output_index": 0,
            "content_index": 1,
            "item_id": item["id"],
            "delta": "second",
        },
        {
            "type": "response.reasoning_summary_text.delta",
            "output_index": 0,
            "summary_index": 0,
            "item_id": item["id"],
            "delta": "summary",
        },
        {"type": "response.output_item.done", "output_index": 0, "item": item},
        {"type": "response.completed", "response": response([item])},
    ]
    for sequence_number, event in enumerate(values):
        event["sequence_number"] = sequence_number
    result, _, _ = asyncio.run(consume(sse(values), pydantic_stream=True))
    assert result.thinking == "summary"
    assert len(result.parts) == 1


@pytest.mark.parametrize("reasoning_id", ["code_test-file-0", "code_test-file-1"])
def test_sdk_generated_file_keys_count_images_without_logs(reasoning_id: str) -> None:
    values = [event for event in summary_events("rs_other") if event.get("output_index") != 1][:-1]
    for event in values:
        if "item" in event:
            event["item"]["id"] = reasoning_id
        if "item_id" in event:
            event["item_id"] = reasoning_id
    reasoning = values[-1]["item"]
    item = {
        "type": "code_interpreter_call",
        "id": "code_test",
        "status": "completed",
        "container_id": "container_test",
        "code": "print('safe')",
        "outputs": [{"type": "logs", "logs": "safe"}, {"type": "image", "url": "data:image/png;base64,AA=="}],
    }
    values.extend([
        {"type": "response.output_item.added", "output_index": 1, "item": {**item, "code": "", "outputs": []}},
        {
            "type": "response.code_interpreter_call_code.delta",
            "output_index": 1,
            "item_id": "code_test",
            "delta": item["code"],
        },
        {
            "type": "response.code_interpreter_call_code.done",
            "output_index": 1,
            "item_id": "code_test",
            "code": item["code"],
        },
        {"type": "response.output_item.done", "output_index": 1, "item": item},
        {"type": "response.completed", "response": response([reasoning, item])},
    ])
    for sequence_number, event in enumerate(values):
        event["sequence_number"] = sequence_number
    if reasoning_id == "code_test-file-0":
        with pytest.raises(InferencePrivacyError) as caught:
            asyncio.run(consume(sse(values), pydantic_stream=True))
        assert caught.value.phase == "before_release" and caught.value.decision == "withhold"
    else:
        result, _, _ = asyncio.run(consume(sse(values), pydantic_stream=True))
        assert result.thinking == "padding\n\nsec" and len(result.files) == 1
