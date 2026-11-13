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

"""Inspectable text/function carriers in the final Code Assist wire envelope."""

from __future__ import annotations

from collections.abc import Iterator

from powercontext.builtin.inference.model_payload import json_unsigned_integer

_REQUEST_FIELDS = frozenset({
    "contents",
    "systemInstruction",
    "generationConfig",
    "tools",
    "toolConfig",
    "safetySettings",
})
_GENERATION_FIELDS = frozenset({
    "temperature",
    "topP",
    "topK",
    "candidateCount",
    "maxOutputTokens",
    "stopSequences",
    "seed",
    "responseMimeType",
    "responseSchema",
    "responseJsonSchema",
    "thinkingConfig",
    "presencePenalty",
    "frequencyPenalty",
})


def _contents(value: dict[str, object]) -> Iterator[object]:
    request = value.get("request")
    if isinstance(request, dict):
        contents = request.get("contents")
        if isinstance(contents, list):
            yield from contents
        else:
            yield None
        if "systemInstruction" in request:
            yield request.get("systemInstruction")
    response = value.get("response")
    if isinstance(response, dict):
        candidates = response.get("candidates")
        if isinstance(candidates, list):
            for candidate in candidates:
                yield candidate.get("content") if isinstance(candidate, dict) else None
        else:
            yield None


def _part_supported(part: object) -> bool:
    if not isinstance(part, dict):
        return False
    if "text" in part:
        return set(part) <= {"text", "thought"} and isinstance(part.get("text"), str)
    for key, payload in (("functionCall", "args"), ("functionResponse", "response")):
        if set(part) == {key}:
            call = part.get(key)
            return (
                isinstance(call, dict)
                and set(call) <= {"name", "id", payload}
                and isinstance(call.get("name"), str)
                and isinstance(call.get(payload), dict)
            )
    return False


def supported_carrier(value: dict[str, object]) -> bool:
    if not isinstance(value.get("request", value.get("response")), dict):
        return False
    for content in _contents(value):
        if not isinstance(content, dict) or set(content) - {"role", "parts"}:
            return False
        parts = content.get("parts")
        if not isinstance(parts, list) or not parts or not all(_part_supported(part) for part in parts):
            return False
    return True


def request_reason(value: dict[str, object]) -> str | None:
    request = value.get("request")
    if not isinstance(request, dict) or set(request) - _REQUEST_FIELDS:
        return "content_field_unsupported"
    if not isinstance(value.get("project"), str) or not value["project"]:
        return "content_field_unsupported"
    generation = request.get("generationConfig", {})
    if not isinstance(generation, dict) or set(generation) - _GENERATION_FIELDS:
        return "content_field_unsupported"
    if "candidateCount" in generation and json_unsigned_integer(generation.get("candidateCount")) != 1:
        return "multiple_choices_unsupported"
    tools = request.get("tools", [])
    if not isinstance(tools, list) or any(
        not isinstance(tool, dict)
        or set(tool) != {"functionDeclarations"}
        or not isinstance(tool.get("functionDeclarations"), list)
        for tool in tools
    ):
        return "content_unsupported"
    return None


def output_limit(value: dict[str, object]) -> object:
    request = value.get("request")
    generation = request.get("generationConfig") if isinstance(request, dict) else None
    return generation.get("maxOutputTokens") if isinstance(generation, dict) else None


def complete_response(value: dict[str, object]) -> bool:
    response = value.get("response")
    candidates = response.get("candidates") if isinstance(response, dict) else None
    return (
        isinstance(candidates, list)
        and len(candidates) == 1
        and isinstance(candidates[0], dict)
        and candidates[0].get("finishReason") == "STOP"
    )


def joined_text(value: dict[str, object]) -> tuple[str, ...]:
    texts: list[str] = []
    for content in _contents(value):
        parts = content.get("parts") if isinstance(content, dict) else None
        if not isinstance(parts, list):
            continue
        fragments = []
        for part in parts:
            text = part.get("text") if isinstance(part, dict) else None
            if isinstance(text, str):
                fragments.append(text)
        texts.append("".join(fragments))
    return tuple(texts)
