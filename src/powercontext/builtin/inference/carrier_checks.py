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

"""Privacy checks for complete, bounded streaming model carriers."""

from __future__ import annotations

from collections import defaultdict
from typing import cast

from powercontext.builtin.inference.model_payload import (
    complete_tool_arguments,
    contains_opaque_content,
    json_unsigned_integer,
    nested_json_text,
    parse_json_evidence,
)
from powercontext.builtin.inference.privacy import PrivacyDecision, PrivacyPolicy


def _stream_fragments(value: object, parts: dict[tuple[str, ...], list[str]], path: tuple[str, ...] = ()) -> None:
    """Join a content field across events, retaining choice/tool identities."""

    if isinstance(value, dict):
        for key, child in value.items():
            key = cast(str, key)
            if (
                key
                in {
                    "content",
                    "text",
                    "name",
                    "arguments",
                    "partial_json",
                    "refusal",
                    "thinking",
                    "reasoning",
                    "reasoning_content",
                    "delta",
                }
                or (key == "id" and path[:4] == ("choices", "0", "delta", "tool_calls"))
            ) and isinstance(child, str):
                parts[(*path, key)].append(child)
            elif isinstance(child, (dict, list)):
                _stream_fragments(child, parts, (*path, key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            # Only these protocol positions define stream identities. Extra
            # index/type metadata must not split fields that the SDK joins.
            identity = None
            if path in {("choices",), ("choices", "0", "delta", "tool_calls")} and isinstance(child, dict):
                identity = json_unsigned_integer(child.get("index"))
            _stream_fragments(child, parts, (*path, str(index if identity is None else identity)))


def inspect_sse(policy: PrivacyPolicy, body: bytes, profile_id: str) -> PrivacyDecision:  # noqa: C901 - protocol-specific terminal signals
    """Inspect a fully buffered SSE carrier and every supported joined text field.

    This supports OpenAI Chat with one choice, separate from the public JSON
    carrier. Nothing is yielded before the entire bounded stream passes.
    """

    phase = "before_release"
    profile = policy.config.targets.get(profile_id)
    if profile is None or not profile.allow_streaming or profile.protocol != "openai-chat":
        return policy.decision(phase, "withhold", "streaming_unsupported")
    if len(body) > policy.config.max_response_bytes:
        return policy.decision(phase, "withhold", "payload_too_large")
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeError:
        return policy.decision(phase, "withhold", "encoding_unsupported")
    if policy.forbidden((text,)):
        return policy.decision(phase, "block", "forbidden_content")
    parts: dict[tuple[str, ...], list[str]] = defaultdict(list)
    terminal = False
    finished = False
    tool_count = 0
    data_lines: list[str] = []
    events: list[str] = []
    # Only blank-line-terminated events are complete. EOF inside a frame with
    # otherwise valid JSON is still an interrupted carrier.
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not line:
            if data_lines:
                events.append("\n".join(data_lines))
                data_lines.clear()
        elif line.startswith("data:"):
            data_lines.append(line[5:].removeprefix(" "))
        elif not line.startswith((":", "event:", "id:", "retry:")):
            return policy.decision(phase, "withhold", "sse_invalid")
    if data_lines or len(events) > 16384:
        return policy.decision(phase, "withhold", "sse_incomplete_or_too_large")
    for event in events:
        if terminal:
            return policy.decision(phase, "withhold", "sse_data_after_terminal")
        if event == "[DONE]":
            terminal = True
            continue
        try:
            value, strings, duplicate = parse_json_evidence(event, policy.config.max_json_depth)
        except (ValueError, RecursionError, UnicodeError):
            return policy.decision(phase, "withhold", "sse_invalid")
        if policy.forbidden(strings):
            return policy.decision(phase, "block", "forbidden_content")
        if duplicate or contains_opaque_content(value, protocol=profile.protocol):
            return policy.decision(phase, "withhold", "content_unsupported")
        # This carrier supports Chat only; its model identity is always at the root.
        # Unrelated message/response extension fields cannot replace that identity.
        if "model" in value and value["model"] not in (profile.model, *profile.response_models):
            return policy.decision(phase, "withhold", "response_model_mismatch")
        _stream_fragments(value, parts)
        if "error" in value or value.get("type") == "error":
            return policy.decision(phase, "withhold", "provider_stream_error")
        if profile.protocol == "openai-chat":
            choices = value.get("choices")
            if isinstance(choices, list):
                if len(choices) > 1:
                    return policy.decision(phase, "withhold", "multiple_choices_unsupported")
                for choice in choices:
                    if not isinstance(choice, dict) or getattr(choice.get("index"), "lexical", None) != "0":
                        return policy.decision(phase, "withhold", "choice_identity_unsupported")
                    delta = choice.get("delta")
                    if isinstance(delta, dict) and delta.get("tool_calls") is not None:
                        calls = delta.get("tool_calls")
                        if not isinstance(calls, list):
                            return policy.decision(phase, "withhold", "tool_identity_unsupported")
                        # SDKs preserve the initial array order, then update by
                        # list position. Require canonical initialization/append
                        # while allowing existing tools' deltas in any order.
                        initial_tools = tool_count == 0
                        seen: set[int] = set()
                        for position, call in enumerate(calls):
                            index = json_unsigned_integer(call.get("index")) if isinstance(call, dict) else None
                            if (
                                index is None
                                or index in seen
                                or index > tool_count
                                or (initial_tools and index != position)
                            ):
                                return policy.decision(phase, "withhold", "tool_identity_unsupported")
                            seen.add(index)
                            tool_count = max(tool_count, index + 1)
                    if finished:
                        return policy.decision(phase, "withhold", "sse_data_after_finish")
                    if choice.get("finish_reason") is not None:
                        if choice.get("finish_reason") not in ("stop", "tool_calls", "function_call"):
                            return policy.decision(phase, "withhold", "response_incomplete")
                        finished = True
    joined = tuple("".join(fragments) for fragments in parts.values())
    try:
        nested, duplicate = nested_json_text(joined, policy.config.max_json_depth)
    except (ValueError, RecursionError, UnicodeError):
        return policy.decision(phase, "withhold", "json_invalid_or_too_deep")
    if policy.forbidden(joined + nested):
        return policy.decision(phase, "block", "forbidden_content")
    if duplicate:
        return policy.decision(phase, "withhold", "json_duplicate_key")
    if any(
        not complete_tool_arguments("".join(fragments), policy.config.max_json_depth)
        for path, fragments in parts.items()
        if path[-1] == "arguments"
    ):
        return policy.decision(phase, "withhold", "tool_arguments_incomplete")
    if not terminal or not finished:
        return policy.decision(phase, "withhold", "response_incomplete")
    return policy.decision(phase, "allow", complete=True)
