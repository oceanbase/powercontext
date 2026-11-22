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

"""Inspect buffered Responses events and the content assembled from their deltas."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import cast

from powercontext.builtin.inference.model_payload import (
    complete_tool_arguments,
    contains_opaque_content,
    joined_content_text,
    json_unsigned_integer,
    nested_json_text,
    parse_json_evidence,
    response_usage,
    responses_output_text,
    tool_arguments,
)
from powercontext.builtin.inference.models import InferenceUsage
from powercontext.builtin.inference.privacy import PrivacyDecision, PrivacyPolicy


class _Fragments:
    """Retain delta assemblies separately from repeated completed snapshots."""

    def __init__(self) -> None:
        self.parts: dict[tuple[int, int, str], list[str]] = defaultdict(list)
        self.completed: dict[int, dict[str, object]] = {}
        self.order: dict[int, int] = {}
        self.content_counts: dict[int, int] = defaultdict(int)
        self.created = False
        self.item_ids: dict[int, str] = {}
        self.item_positions: dict[str, int] = {}
        self.identity_mismatch = False
        self.refusals: list[list[str]] = [[]]
        self.sdk_parts: dict[tuple[str, int, int], list[str]] = {}
        self.sdk_identities: dict[str | tuple[str, int], tuple[str, int, int]] = {}

    def _part(self, part: object, output: int, content: int) -> None:
        if isinstance(part, dict):
            for field in ("text", "refusal"):
                text = part.get(field)
                if isinstance(text, str):
                    kind = str(part.get("type", field))
                    if kind == "summary_text":
                        kind = "reasoning_summary_text"
                    self.parts[output, content, kind].append(text)

    def _sdk_identity(self, vendor_id: str | tuple[str, int], owner: tuple[str, int, int]) -> None:
        # Pydantic AI's string-derived keys share one namespace across thinking
        # and tool parts. Distinct raw item IDs can still alias after derivation.
        if self.sdk_identities.get(vendor_id, owner) != owner:
            self.identity_mismatch = True
        self.sdk_identities[vendor_id] = owner

    def _sdk_part(self, kind: str, output: int, content: int, text: str = "") -> None:
        identity = self.item_ids.get(output)
        if identity is not None:
            if kind == "text":
                self._sdk_identity((identity, content), (kind, output, content))
            elif kind == "thinking":
                vendor_id = identity if content == 0 else f"{identity}-{content}"
                self._sdk_identity(vendor_id, (kind, output, content))
        self.sdk_parts.setdefault((kind, output, content), []).append(text)

    def _sdk_item_identities(self, item: dict[str, object], output: int, *, done: bool) -> None:
        identity = item.get("id")
        if not isinstance(identity, str):
            return
        kind = item.get("type")
        if kind == "function_call" or (kind == "tool_search_call" and item.get("execution") == "client"):
            self._sdk_identity(identity, ("function_call", output, 0))
        elif kind in {"web_search_call", "file_search_call"}:
            self._sdk_identity(f"{identity}-call", ("native_call", output, 0))
            if done:
                self._sdk_identity(f"{identity}-return", ("native_return", output, 0))
        elif kind in {"code_interpreter_call", "mcp_call", "mcp_list_tools"}:
            suffix = "return" if done else "call"
            self._sdk_identity(f"{identity}-{suffix}", (f"native_{suffix}", output, 0))
            outputs = item.get("outputs")
            if kind == "code_interpreter_call" and done and isinstance(outputs, list):
                images = [value for value in outputs if isinstance(value, dict) and value.get("type") == "image"]
                for index in range(len(images)):
                    self._sdk_identity(f"{identity}-file-{index}", ("file", output, index))
        elif kind == "tool_search_call":
            self._sdk_identity(f"{identity}-call", ("native_call", output, 0))
        elif kind == "tool_search_output" and done and item.get("execution") == "server":
            self._sdk_identity(f"{identity}-return", ("native_return", output, 0))

    def _sdk_terminal_identities(self, value: dict[str, object]) -> None:
        snapshot = value.get("response")
        items = snapshot.get("output") if isinstance(snapshot, dict) else None
        if isinstance(items, list):
            for index, item in enumerate(items):
                if isinstance(item, dict) and item.get("type") == "mcp_list_tools":
                    identity = item.get("id")
                    output = self.item_positions.get(identity, index) if isinstance(identity, str) else index
                    # The SDK backfills a list-tools return omitted from item.done.
                    self._sdk_item_identities(cast(dict[str, object], item), output, done=True)

    def _sdk_argument_identity(self, value: dict[str, object], event_type: str) -> None:
        if event_type not in {
            "response.code_interpreter_call_code.delta",
            "response.code_interpreter_call_code.done",
            "response.mcp_call_arguments.delta",
            "response.mcp_call_arguments.done",
        }:
            return
        output = json_unsigned_integer(value.get("output_index"))
        identity = value.get("item_id")
        if output is None or not isinstance(identity, str):
            self.identity_mismatch = True
            return
        self._identity(output, identity, required=True)
        self._sdk_identity(f"{identity}-call", ("native_call", output, 0))

    def _sdk_aggregates(self) -> tuple[str, str]:
        # ModelResponse.text separates text with two newlines when a thinking or
        # tool part intervenes; ModelResponse.thinking separates every part.
        texts: list[list[str]] = []
        thinking: list[str] = []
        adjacent = False
        for (kind, _output, _content), fragments in self.sdk_parts.items():
            text = "".join(fragments)
            if kind == "text":
                if adjacent:
                    texts[-1].append(text)
                else:
                    texts.append([text])
                adjacent = True
            else:
                adjacent = False
                if kind == "thinking":
                    thinking.append(text)
        return "\n\n".join("".join(parts) for parts in texts), "\n\n".join(thinking)

    def _identity(self, output: int, identity: object, *, required: bool = False) -> None:
        if identity is None and not required:
            return
        if not isinstance(identity, str):
            self.identity_mismatch = True
            return
        if self.item_ids.get(output, identity) != identity or self.item_positions.get(identity, output) != output:
            self.identity_mismatch = True
        self.item_ids[output] = identity
        self.item_positions[identity] = output

    def _item(self, item: dict[str, object], output: int) -> None:
        self._identity(output, item.get("id"))
        self.order.setdefault(output, len(self.order))
        arguments = item.get("arguments")
        if item.get("type") == "function_call" and isinstance(arguments, str):
            self.parts[output, 0, "function_call_arguments"].append(arguments)
        for field in ("content", "summary"):
            content = item.get(field)
            if isinstance(content, list):
                if field == "content":
                    self.content_counts[output] = len(content)
                for index, part in enumerate(content):
                    self._part(part, output, index)

    def _initial_response(self, value: dict[str, object]) -> None:
        if self.created:
            return
        self.created = True
        snapshot = value.get("response")
        output = snapshot.get("output") if isinstance(snapshot, dict) else None
        if isinstance(output, list):
            for index, item in enumerate(output):
                if isinstance(item, dict):
                    self._item(cast(dict[str, object], item), index)

    def add(self, value: dict[str, object], event_type: str) -> None:  # noqa: C901 - protocol content positions differ by event
        self._sdk_argument_identity(value, event_type)
        if event_type == "response.completed":
            self._sdk_terminal_identities(value)
            return
        if event_type == "response.refusal.done":
            refusal = value.get("refusal")
            if not isinstance(refusal, str):
                raise ValueError("content_unsupported")
            # Pydantic AI replaces its global refusal text at done, then appends
            # later refusal deltas regardless of item/content identity.
            self.refusals.append([refusal])
            return
        if event_type == "response.created":
            self._initial_response(value)
            return
        if not event_type.endswith(".delta") and event_type not in {
            "response.output_item.added",
            "response.output_item.done",
            "response.content_part.added",
            "response.reasoning_summary_part.added",
        }:
            return
        output = json_unsigned_integer(value.get("output_index"))
        if output is None:
            raise ValueError("output_identity_unsupported")
        self.order.setdefault(output, len(self.order))
        self._identity(
            output,
            value.get("item_id"),
            required=event_type
            in {
                "response.output_text.delta",
                "response.function_call_arguments.delta",
                "response.reasoning_summary_text.delta",
                "response.reasoning_summary_part.added",
                "response.reasoning_text.delta",
                "response.code_interpreter_call_code.delta",
            },
        )
        if event_type.startswith("response.output_item."):
            item = value.get("item")
            if not isinstance(item, dict):
                raise ValueError("sse_invalid")
            self._identity(output, item.get("id"))
            self._sdk_item_identities(cast(dict[str, object], item), output, done=event_type.endswith(".done"))
            if item.get("type") in {
                "function_call",
                "web_search_call",
                "file_search_call",
                "code_interpreter_call",
                "mcp_call",
                "mcp_list_tools",
                "tool_search_call",
                "tool_search_output",
            }:
                phase = 0 if item.get("type") == "function_call" or event_type.endswith(".added") else 1
                self._sdk_part("tool", output, phase)
            if event_type.endswith(".done"):
                self.completed[output] = cast(dict[str, object], item)
                return
            self._item(cast(dict[str, object], item), output)
        elif event_type in {"response.content_part.added", "response.reasoning_summary_part.added"}:
            if event_type == "response.content_part.added":
                # The SDK appends parts; the added event's declared content_index
                # does not select the position later addressed by text deltas.
                content = self.content_counts[output]
                self.content_counts[output] += 1
            else:
                content = json_unsigned_integer(value.get("summary_index"))
                if content is None:
                    raise ValueError("content_identity_unsupported")
            part = value.get("part")
            self._part(part, output, content)
            if event_type == "response.reasoning_summary_part.added" and isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    self._sdk_part("thinking", output, content, text)
        else:
            delta = value.get("delta")
            if not isinstance(delta, str):
                raise ValueError("content_unsupported")
            index_field = (
                "summary_index"
                if event_type == "response.reasoning_summary_text.delta"
                else "content_index"
                if event_type
                in {"response.output_text.delta", "response.refusal.delta", "response.reasoning_text.delta"}
                else None
            )
            content = json_unsigned_integer(value.get(index_field)) if index_field is not None else 0
            if content is None:
                raise ValueError("content_identity_unsupported")
            # Item IDs and incidental metadata must not split the text that SDKs
            # concatenate by output/content position.
            kind = event_type.removeprefix("response.").removesuffix(".delta")
            self.parts[output, content, kind].append(delta)
            if event_type == "response.refusal.delta":
                self.refusals[-1].append(delta)
            elif event_type == "response.output_text.delta":
                self._sdk_part("text", output, content, delta)
            elif event_type == "response.reasoning_summary_text.delta":
                self._sdk_part("thinking", output, content, delta)
            elif event_type == "response.reasoning_text.delta":
                # Raw CoT lives in provider details on the first ThinkingPart.
                self._sdk_part("thinking", output, 0)

    def text(self) -> tuple[str, ...]:
        joined = {key: "".join(parts) for key, parts in self.parts.items()}
        ordered = sorted(joined, key=lambda key: (self.order[key[0]], key[1], key[2]))
        output_text = "".join(joined[key] for key in ordered if key[2] == "output_text")
        completed = {"output": [item for _, item in sorted(self.completed.items())]}
        return (
            *joined.values(),
            output_text,
            responses_output_text(completed),
            *("".join(parts) for parts in self.refusals),
            *self._sdk_aggregates(),
        )

    def arguments(self) -> list[object]:
        return [
            "".join(parts) for key, parts in self.parts.items() if key[2] == "function_call_arguments" and any(parts)
        ] + tool_arguments({"output": list(self.completed.values())}, "openai-responses")


def inspect_responses_events(  # noqa: C901 - inspect evidence before evaluating protocol terminal state
    policy: PrivacyPolicy, events: list[str], profile_id: str
) -> PrivacyDecision:
    """Only a successful terminal response admits the original, complete stream."""

    phase = "before_release"
    profile = policy.config.targets[profile_id]
    fragments = _Fragments()
    terminal: bytes | None = None
    done = False
    response_id: str | None = None
    try:
        for event in events:
            if done or (terminal is not None and event != "[DONE]"):
                return policy.decision(phase, "withhold", "sse_data_after_terminal")
            if event == "[DONE]":
                done = True
                continue
            value, strings, duplicate = parse_json_evidence(event, policy.config.max_json_depth)
            snapshot = value.get("response")
            strings += joined_content_text(value)
            if isinstance(snapshot, dict):
                snapshot = cast(dict[str, object], snapshot)
                strings += (responses_output_text(snapshot),)
            nested, repeated = nested_json_text(strings, policy.config.max_json_depth)
            if policy.forbidden(strings + nested + joined_content_text(value)):
                return policy.decision(phase, "block", "forbidden_content")
            if duplicate or repeated:
                return policy.decision(phase, "withhold", "json_duplicate_key")
            event_type = value.get("type")
            if not isinstance(event_type, str):
                return policy.decision(phase, "withhold", "sse_invalid")
            if (
                contains_opaque_content({key: child for key, child in value.items() if key != "response"})
                or contains_opaque_content(snapshot, protocol="openai-responses")
                or contains_opaque_content(value.get("item"))
                or contains_opaque_content(value.get("part"))
                or event_type.startswith((
                    "response.audio.",
                    "response.output_audio.",
                    "response.image_generation_call.",
                ))
            ):
                return policy.decision(phase, "withhold", "content_unsupported")
            if isinstance(snapshot, dict):
                if "model" in snapshot and snapshot.get("model") not in (profile.model, *profile.response_models):
                    return policy.decision(phase, "withhold", "response_model_mismatch")
                identity = snapshot.get("id")
                if isinstance(identity, str):
                    if response_id is not None and identity != response_id:
                        return policy.decision(phase, "withhold", "response_identity_mismatch")
                    response_id = identity
            if event_type in {"error", "response.failed", "response.incomplete"} or value.get("error"):
                return policy.decision(phase, "withhold", "provider_stream_error")
            fragments.add(value, event_type)
            if event_type == "response.completed":
                if not isinstance(snapshot, dict) or snapshot.get("status") != "completed" or snapshot.get("error"):
                    return policy.decision(phase, "withhold", "response_incomplete")
                # Every original scalar (including duplicate/numeric evidence) was
                # checked above. Reuse the JSON policy for final response admission.
                terminal = json.dumps(json.loads(event)["response"], ensure_ascii=False).encode()
        joined = fragments.text()
        nested, duplicate = nested_json_text(joined, policy.config.max_json_depth)
        if policy.forbidden(joined + nested):
            return policy.decision(phase, "block", "forbidden_content")
        if duplicate:
            return policy.decision(phase, "withhold", "json_duplicate_key")
        if fragments.identity_mismatch:
            return policy.decision(phase, "withhold", "output_identity_unsupported")
        if not all(complete_tool_arguments(value, policy.config.max_json_depth) for value in fragments.arguments()):
            return policy.decision(phase, "withhold", "tool_arguments_incomplete")
    except (ValueError, RecursionError, UnicodeError):
        return policy.decision(phase, "withhold", "sse_invalid")
    if terminal is None:
        return policy.decision(phase, "withhold", "response_incomplete")
    return policy.inspect_json(terminal, phase=phase, target_profile=profile_id)


def responses_stream_usage(body: bytes, model: str) -> InferenceUsage:
    """Read cumulative counts from a terminal response, including failed attempts."""

    result = InferenceUsage(requests=1, incomplete=True, target_model=model)
    for frame in body.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n\n"):
        data = b"\n".join(line[5:].removeprefix(b" ") for line in frame.split(b"\n") if line.startswith(b"data:"))
        try:
            value = json.loads(data)
        except (ValueError, UnicodeError, RecursionError):
            continue
        if isinstance(value, dict) and value.get("type") in {
            "response.completed",
            "response.failed",
            "response.incomplete",
        }:
            snapshot = value.get("response")
            if isinstance(snapshot, dict):
                result = response_usage(json.dumps(snapshot).encode(), target_model=model, protocol="openai-responses")
    return result
