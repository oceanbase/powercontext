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

"""Bounded model-protocol decoding without policy decisions or transport state.

JSON decoding retains duplicate values and numeric spellings for privacy
inspection. Usage decoding preserves unknown provider counts as unknown.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal, cast

from powercontext.builtin.inference.models import InferenceUsage

PrivacyProtocol = Literal[
    "openai-chat", "openai-responses", "anthropic-messages", "openai-embeddings", "minimax-embeddings", "jev-rerank"
]

_OPAQUE_KEYS = frozenset({
    "image_url",
    "image",
    "audio",
    "input_audio",
    "video",
    "file_data",
    "file_id",
    "file_url",
    "encrypted_content",
})
_OPAQUE_TYPES = frozenset({
    "image",
    "image_url",
    "input_image",
    "input_audio",
    "audio",
    "input_file",
    "file",
    "video",
    "redacted_thinking",
    "item_reference",
    "image_generation_call",
})
_CONTENT_FIELDS = frozenset({
    "messages",
    "input",
    "output",
    "choices",
    "message",
    "delta",
    "content",
    "system",
    "content_block",
    "response",
})


@dataclass(frozen=True)
class _Number:
    lexical: str


@dataclass(frozen=True)
class _Object:
    pairs: list[tuple[str, object]]


class _InvalidCarrier(ValueError):
    pass


def _canonical_number_text(lexical: str) -> str:
    try:
        number = Decimal(lexical)
        if not number.is_finite() or abs(number.adjusted()) > 8192 or len(number.as_tuple().digits) > 8192:
            raise _InvalidCarrier
        return format(number, "f")
    except InvalidOperation as error:
        raise _InvalidCarrier from error


def _parse_json_document(text: str, max_depth: int) -> tuple[object, tuple[str, ...], bool]:  # noqa: C901 - one bounded visitor preserves all JSON scalar evidence
    """Retain scalar evidence from either a protocol object or nested JSON."""

    def reject_constant(_value: str) -> None:
        raise _InvalidCarrier

    value = json.loads(
        text, object_pairs_hook=_Object, parse_int=_Number, parse_float=_Number, parse_constant=reject_constant
    )
    strings: list[str] = []
    duplicate = False
    retained_size = 0
    retained_limit = max(256, len(text.encode("utf-8"))) * 2

    def record(text_value: str) -> None:
        nonlocal retained_size
        # Numeric exponent normalization must not amplify a bounded wire body
        # into an unbounded collection of expanded decimal strings.
        retained_size += len(text_value.encode("utf-8", errors="strict"))
        if retained_size > retained_limit:
            raise _InvalidCarrier
        strings.append(text_value)

    def visit(item: object, depth: int) -> object:
        nonlocal duplicate
        if depth > max_depth:
            raise _InvalidCarrier
        if isinstance(item, _Object):
            normalized: dict[str, object] = {}
            for key, child in item.pairs:
                record(key)
                duplicate = duplicate or key in normalized
                normalized[key] = visit(child, depth + 1)
            return normalized
        if isinstance(item, list):
            return [visit(child, depth + 1) for child in item]
        if isinstance(item, _Number):
            record(item.lexical)
            record(_canonical_number_text(item.lexical))
            return item
        if isinstance(item, str):
            # Reject lone surrogates: a later encoder must not reinterpret them.
            record(item)
        return item

    return visit(value, 0), tuple(strings), duplicate


def parse_json_evidence(text: str, max_depth: int) -> tuple[dict[str, object], tuple[str, ...], bool]:
    """Retain every key and number spelling from an object-root protocol carrier."""

    normalized, strings, duplicate = _parse_json_document(text, max_depth)
    if not isinstance(normalized, dict):
        raise _InvalidCarrier
    return cast(dict[str, object], normalized), strings, duplicate


def json_unsigned_integer(value: object) -> int | None:
    if isinstance(value, _Number) and value.lexical.isascii() and value.lexical.isdigit():
        try:
            return int(value.lexical)
        except ValueError:
            return None
    return None


def parse_model_json(body: bytes, max_depth: int = 64) -> dict[str, object]:
    """Parse an inspected carrier without dropping duplicate keys or number spelling.

    Numbers remain opaque lexical values; this helper is intended for locating
    decoded text fields after admission, not reserializing the request.
    """

    value, _strings, duplicate = parse_json_evidence(body.decode("utf-8", errors="strict"), max_depth)
    if duplicate:
        raise _InvalidCarrier("duplicate JSON keys")  # noqa: TRY003
    return value


def _opaque_document_source(source: object) -> bool:
    """Only inline Anthropic text documents expose all content to inspection."""

    if not isinstance(source, dict):
        return True
    if source.get("type") == "text":
        return source.get("media_type") != "text/plain" or not isinstance(source.get("data"), str)
    if source.get("type") == "content":
        content = source.get("content")
        if isinstance(content, str):
            return False
        return not isinstance(content, list) or any(
            not isinstance(block, dict)
            or block.get("type") != "text"
            or not isinstance(block.get("text"), str)
            or contains_opaque_content(block)
            for block in content
        )
    return True


def contains_opaque_content(value: object, *, protocol: PrivacyProtocol | None = None) -> bool:
    """Inspect protocol envelopes and blocks without interpreting schemas as content.

    Tool declarations and output schemas describe user data, not model content
    carriers. Anthropic tool inputs likewise contain arbitrary JSON arguments.
    All scalar evidence still receives the independent forbidden-content check.
    """

    if isinstance(value, dict):
        # This built-in Responses tool produces opaque images, including when
        # its completed output is replayed as an input item.
        tools = value.get("tools")
        if (
            protocol == "openai-responses"
            and isinstance(tools, list)
            and any(isinstance(tool, dict) and tool.get("type") == "image_generation" for tool in tools)
        ):
            return True
        # Responses ItemReference permits an omitted or null discriminator.
        # Restrict this check to root input items; IDs in schemas/tool data are text.
        inputs = value.get("input")
        if (
            protocol == "openai-responses"
            and isinstance(inputs, list)
            and any(isinstance(item, dict) and "id" in item and item.get("type") is None for item in inputs)
        ):
            return True
        if any(key in _OPAQUE_KEYS for key in value):
            return True
        content_type = value.get("type")
        if isinstance(content_type, str) and content_type in _OPAQUE_TYPES:
            return True
        if content_type == "document" and _opaque_document_source(value.get("source")):
            return True
        return any(
            contains_opaque_content(child)
            for field, child in value.items()
            if field in _CONTENT_FIELDS and not (content_type == "tool_use" and field == "input")
        )
    return isinstance(value, list) and any(contains_opaque_content(child) for child in value)


def joined_content_text(value: object) -> tuple[str, ...]:
    """Inspect a logical content value across adjacent protocol text blocks."""

    result: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "content" and isinstance(child, list):
                result.append(
                    "".join(
                        item if isinstance(item, str) else str(item.get("text", ""))
                        for item in child
                        if isinstance(item, (str, dict))
                    )
                )
            result.extend(joined_content_text(child))
    elif isinstance(value, list):
        for child in value:
            result.extend(joined_content_text(child))
    return tuple(result)


def nested_json_text(strings: tuple[str, ...], max_depth: int) -> tuple[tuple[str, ...], bool]:
    """Scan nested JSON objects and arrays without losing scalar evidence.

    Protocol roots remain objects. JSON nested in evidence strings may use
    either container, and each parsing level consumes the same work budget.
    """

    pending = list(strings)
    expanded: list[str] = []
    duplicate = False
    work_limit = max(256, sum(len(text.encode("utf-8")) for text in strings)) * 8
    work = 0
    for _ in range(min(max_depth, 8)):
        following: list[str] = []
        for text in pending:
            if not text.lstrip().startswith(("{", "[")):
                continue
            work += len(text.encode("utf-8"))
            if work > work_limit:
                raise _InvalidCarrier
            try:
                _value, nested, repeated = _parse_json_document(text, max_depth)
            except json.JSONDecodeError:
                continue  # Ordinary prose/code need not itself be a JSON document.
            work += sum(len(value.encode("utf-8")) for value in nested)
            if work > work_limit:
                raise _InvalidCarrier
            duplicate = duplicate or repeated
            following.extend(nested)
        if not following:
            return tuple(expanded), duplicate
        expanded.extend(following)
        pending = following
    # Reject an exhausted nested carrier rather than claiming complete coverage.
    if any(text.lstrip().startswith(("{", "[")) for text in pending):
        raise _InvalidCarrier
    return tuple(expanded), duplicate


def complete_response(value: dict[str, object], protocol: PrivacyProtocol) -> bool:
    if isinstance(value.get("error"), dict) and value["error"]:
        return True  # The raw error is inspected, then the SDK handles its status.
    if protocol == "openai-chat":
        choices = value.get("choices")
        return (
            isinstance(choices, list)
            and bool(choices)
            and all(
                isinstance(choice, dict) and choice.get("finish_reason") in ("stop", "tool_calls", "function_call")
                for choice in choices
            )
        )
    if protocol == "openai-responses":
        return value.get("status") == "completed"
    if protocol == "anthropic-messages":
        return value.get("stop_reason") in ("end_turn", "stop_sequence", "tool_use")
    return True


def tool_arguments(value: dict[str, object], protocol: PrivacyProtocol) -> list[object]:
    """Locate argument carriers by protocol, without interpreting tool schemas."""

    if protocol == "openai-responses":
        items = [
            item
            for field in ("input", "output")
            for values in [value.get(field)]
            if isinstance(values, list)
            for item in values
        ]
        return [
            item.get("arguments") for item in items if isinstance(item, dict) and item.get("type") == "function_call"
        ]
    if protocol != "openai-chat":
        return []
    message_values = value.get("messages")
    messages = list(message_values) if isinstance(message_values, list) else []
    choices = value.get("choices")
    if isinstance(choices, list):
        messages.extend(choice.get("message") for choice in choices if isinstance(choice, dict))
    arguments: list[object] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        calls = message.get("tool_calls")
        functions = (
            [call.get("function") for call in calls if isinstance(call, dict)] if isinstance(calls, list) else []
        )
        if "function_call" in message:
            functions.append(message.get("function_call"))
        arguments.extend(function.get("arguments") if isinstance(function, dict) else None for function in functions)
    return arguments


def complete_tool_arguments(arguments: object, max_depth: int) -> bool:
    """A serialized tool argument carrier must be one complete JSON object."""

    if not isinstance(arguments, str):
        return False
    try:
        _value, _strings, duplicate = parse_json_evidence(arguments, max_depth)
    except (ValueError, RecursionError, UnicodeError):
        return False
    return not duplicate


def _known_count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def response_usage(
    body: bytes, *, target_model: str | None = None, protocol: PrivacyProtocol | None = None
) -> InferenceUsage:
    """Preserve provider-reported counts, including known usage on rejected output."""

    try:
        document = json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        document = None
    usage = document.get("usage") if isinstance(document, dict) else None
    if isinstance(document, dict) and protocol == "minimax-embeddings":
        usage = {"input_tokens": document.get("total_tokens")}
    if not isinstance(usage, dict):
        return InferenceUsage(requests=1, incomplete=True, target_model=target_model)
    input_tokens = _known_count(usage.get("input_tokens", usage.get("prompt_tokens")))
    output_tokens = _known_count(usage.get("output_tokens", usage.get("completion_tokens")))
    input_details = usage.get("prompt_tokens_details", usage.get("input_tokens_details"))
    output_details = usage.get("completion_tokens_details", usage.get("output_tokens_details"))
    cache_read = _known_count(usage.get("cache_read_input_tokens"))
    if cache_read is None and isinstance(input_details, dict):
        cache_read = _known_count(input_details.get("cached_tokens"))
    cache_write = _known_count(usage.get("cache_creation_input_tokens"))
    if protocol == "anthropic-messages" and input_tokens is not None:
        input_tokens += (cache_read or 0) + (cache_write or 0)
    generating = protocol not in {"minimax-embeddings", "openai-embeddings"}
    return InferenceUsage(
        requests=1,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        reasoning_tokens=_known_count(output_details.get("reasoning_tokens"))
        if isinstance(output_details, dict)
        else None,
        target_model=target_model,
        incomplete=input_tokens is None or (generating and output_tokens is None),
    )


def chat_stream_usage(body: bytes, model: str) -> InferenceUsage:
    """Chat streaming reports cumulative counts in its optional final usage event."""

    result = InferenceUsage(requests=1, incomplete=True, target_model=model)
    for frame in body.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n\n"):
        data = b"\n".join(line[5:].removeprefix(b" ") for line in frame.split(b"\n") if line.startswith(b"data:"))
        try:
            value = json.loads(data)
        except (ValueError, UnicodeError, RecursionError):
            continue
        if isinstance(value, dict) and isinstance(value.get("usage"), dict):
            result = response_usage(data, target_model=model, protocol="openai-chat")
    return result
