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

"""Bind native host tool calls to their results and logical user turns.

Only native call IDs pair calls with results. Assistant prose is never parsed as a call, and an incomplete or
ambiguous transcript is rejected instead of being partially analyzed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


class TranscriptError(ValueError):
    """The transcript cannot be bound to exact calls, results, and turns."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


@dataclass(frozen=True, slots=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens + self.cache_creation_input_tokens + self.cache_read_input_tokens


@dataclass(frozen=True, slots=True)
class ToolResult:
    is_error: bool
    text: str
    at: datetime


@dataclass(slots=True)
class ToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]
    at: datetime
    result: ToolResult | None = None


@dataclass(frozen=True, slots=True)
class ModelMessage:
    """One model response, deduplicated by its native message ID."""

    message_id: str
    at: datetime
    usage: Usage


@dataclass(slots=True)
class Turn:
    index: int
    started_at: datetime
    calls: list[ToolCall] = field(default_factory=list)
    messages: list[ModelMessage] = field(default_factory=list)
    terminal_text: str | None = None
    terminal_at: datetime | None = None
    # "message" when each model response carries usage; "turn" when the host reports one total per turn.
    usage_granularity: str = "message"


def _timestamp(event: dict[str, Any], where: str, problems: list[str]) -> datetime | None:
    value = event.get("timestamp")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else None
    except ValueError:
        problems.append(f"Invalid timestamp at {where}")
        return None


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text") or "") for block in content if isinstance(block, dict) and block.get("type") == "text"
        )
    return ""


def _usage(message: dict[str, Any]) -> Usage:
    usage = message.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    return Usage(
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        cache_creation_input_tokens=int(usage.get("cache_creation_input_tokens") or 0),
        cache_read_input_tokens=int(usage.get("cache_read_input_tokens") or 0),
    )


def read_claude_session(path: Path, prompts: list[str]) -> list[Turn]:  # noqa: C901, PLR0912, PLR0915
    """Parse a Claude Code session JSONL into exactly ``len(prompts)`` complete turns."""

    problems: list[str] = []
    turns: list[Turn] = []
    calls: dict[str, ToolCall] = {}
    pending: dict[str, int] = {}
    messages: dict[str, int] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        where = f"{path.name}:{line_number}"
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            problems.append(f"Invalid JSON at {where}")
            continue
        if not isinstance(event, dict) or event.get("type") not in {"user", "assistant"}:
            continue
        if event.get("isSidechain"):
            problems.append(f"Sidechain event at {where}")
            continue
        if event.get("isApiErrorMessage"):
            problems.append(f"Host API error at {where}")
        at = _timestamp(event, where, problems)
        message = event.get("message")
        if at is None or not isinstance(message, dict):
            problems.append(f"Event without timestamp or message at {where}")
            continue
        content = message.get("content")
        blocks = [block for block in content if isinstance(block, dict)] if isinstance(content, list) else []
        if event["type"] == "user":
            results = [block for block in blocks if block.get("type") == "tool_result"]
            if not results:
                text = _text(content).strip()
                if event.get("isMeta"):
                    continue
                expected = prompts[len(turns)] if len(turns) < len(prompts) else None
                if text != expected or pending:
                    problems.append(f"Ambiguous or out-of-order user turn at {where}")
                    continue
                turns.append(Turn(index=len(turns) + 1, started_at=at))
                continue
            for block in results:
                call_id = block.get("tool_use_id")
                if not isinstance(call_id, str) or pending.pop(call_id, None) != len(turns):
                    problems.append(f"Unmatched tool result at {where}")
                    continue
                calls[call_id].result = ToolResult(bool(block.get("is_error")), _text(block.get("content")), at)
            continue
        if not turns:
            problems.append(f"Assistant event before the first user turn at {where}")
            continue
        turn = turns[-1]
        message_id = message.get("id")
        if not isinstance(message_id, str) or not message_id:
            problems.append(f"Assistant message without ID at {where}")
            continue
        # Claude Code writes one line per content block and repeats the message usage on each line.
        if message_id in messages:
            index = messages[message_id]
            turn.messages[index] = ModelMessage(message_id, turn.messages[index].at, _usage(message))
        else:
            messages[message_id] = len(turn.messages)
            turn.messages.append(ModelMessage(message_id, at, _usage(message)))
        for block in blocks:
            if block.get("type") != "tool_use":
                continue
            call_id, name, arguments = block.get("id"), block.get("name"), block.get("input")
            if not isinstance(call_id, str) or not call_id or not isinstance(name, str) or not name:
                problems.append(f"Tool call without ID or name at {where}")
                continue
            if call_id in calls:
                problems.append(f"Duplicate tool call ID at {where}")
                continue
            if not isinstance(arguments, dict):
                problems.append(f"Tool call arguments are not an object at {where}")
                continue
            calls[call_id] = ToolCall(call_id, name, arguments, at)
            pending[call_id] = len(turns)
            turn.calls.append(calls[call_id])
            turn.terminal_text = turn.terminal_at = None
        text = _text(content).strip()
        if text and not pending and message.get("stop_reason") in {"end_turn", None}:
            turn.terminal_text, turn.terminal_at = text, at
    if len(turns) != len(prompts):
        problems.append(f"Expected {len(prompts)} user turns, found {len(turns)}")
    if pending:
        problems.append(f"Tool calls without results: {sorted(pending)}")
    problems.extend(f"Turn {turn.index} has no terminal assistant response" for turn in turns if not turn.terminal_at)
    if problems:
        raise TranscriptError(problems)
    return turns
