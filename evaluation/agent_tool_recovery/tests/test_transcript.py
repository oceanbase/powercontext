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

"""Synthetic Claude Code transcripts establish parser behavior, not model behavior."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ..codex import read_transcript as codex_read
from ..transcript import TranscriptError, read_claude_session

PROMPT = "Generate an Experience."
TOOL = "mcp__powercontext__generate_experience"


def user(text: str, second: int = 0) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": f"2026-10-19T00:00:{second:02d}Z",
        "message": {"role": "user", "content": text},
    }


def tool_use(call_id: str, message_id: str = "msg-1", second: int = 1) -> dict[str, Any]:
    return {
        "type": "assistant",
        "timestamp": f"2026-10-19T00:00:{second:02d}Z",
        "message": {
            "id": message_id,
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5},
            "content": [{"type": "tool_use", "id": call_id, "name": TOOL, "input": {"scope_id": "s"}}],
        },
    }


def tool_result(call_id: str, second: int = 2, *, is_error: bool = True) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": f"2026-10-19T00:00:{second:02d}Z",
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": call_id, "is_error": is_error, "content": "HTTP error 503"}
            ],
        },
    }


def answer(message_id: str = "msg-2", second: int = 3) -> dict[str, Any]:
    return {
        "type": "assistant",
        "timestamp": f"2026-10-19T00:00:{second:02d}Z",
        "message": {
            "id": message_id,
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 20, "output_tokens": 7},
            "content": [{"type": "text", "text": "Generation is unavailable."}],
        },
    }


def write(tmp_path: Path, events: list[dict[str, Any]]) -> Path:
    path = tmp_path / "session.jsonl"
    path.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
    return path


def test_pairs_calls_and_results_by_native_id(tmp_path: Path) -> None:
    turns = read_claude_session(write(tmp_path, [user(PROMPT), tool_use("t1"), tool_result("t1"), answer()]), [PROMPT])

    assert [(call.call_id, call.result.is_error if call.result else None) for call in turns[0].calls] == [("t1", True)]
    assert turns[0].terminal_text == "Generation is unavailable."


def test_repeated_usage_lines_count_once_per_model_message(tmp_path: Path) -> None:
    duplicate = tool_use("t2", message_id="msg-1", second=1)
    turns = read_claude_session(
        write(tmp_path, [user(PROMPT), tool_use("t1"), duplicate, tool_result("t1"), tool_result("t2"), answer()]),
        [PROMPT],
    )

    assert [message.message_id for message in turns[0].messages] == ["msg-1", "msg-2"]
    assert sum(message.usage.total for message in turns[0].messages) == 42


@pytest.mark.parametrize(
    ("events", "problem"),
    [
        ([user(PROMPT), tool_result("t9"), answer()], "Unmatched tool result"),
        ([user(PROMPT), tool_use("t1"), tool_use("t1", "msg-3"), tool_result("t1"), answer()], "Duplicate"),
        ([user(PROMPT), tool_use("t1"), user(PROMPT), tool_result("t1"), answer()], "Ambiguous"),
        ([user("Something else"), answer()], "Ambiguous"),
        ([user(PROMPT), tool_use("t1")], "without results"),
        ([user(PROMPT), tool_use("t1"), tool_result("t1")], "no terminal assistant response"),
        ([answer()], "before the first user turn"),
    ],
    ids=[
        "unmatched-result",
        "duplicate-id",
        "prompt-during-call",
        "unknown-prompt",
        "missing-result",
        "no-terminal-answer",
        "assistant-first",
    ],
)
def test_rejects_transcripts_that_cannot_be_bound_exactly(
    tmp_path: Path, events: list[dict[str, Any]], problem: str
) -> None:
    with pytest.raises(TranscriptError) as error:
        read_claude_session(write(tmp_path, events), [PROMPT])

    assert any(problem in item for item in error.value.problems)


def test_rejects_missing_logical_turn(tmp_path: Path) -> None:
    with pytest.raises(TranscriptError, match="Expected 2 user turns"):
        read_claude_session(write(tmp_path, [user(PROMPT), answer()]), [PROMPT, "Second turn."])


def codex(tmp_path: Path, events: list[dict[str, Any]]) -> Path:
    path = tmp_path / "transcript.jsonl"
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    receipts = [f"2026-10-19T00:00:{second:02d}+00:00" for second in range(len(events))]
    (tmp_path / "receipt-times.json").write_text(json.dumps(receipts), encoding="utf-8")
    return path


def mcp_item(kind: str, *, status: str = "completed", error: str | None = None) -> dict[str, Any]:
    item = {
        "id": "item_1",
        "type": "mcp_tool_call",
        "server": "powercontext",
        "tool": "generate_experience",
        "arguments": {"scope_id": "s"},
        "status": status,
    }
    if error:
        item["error"] = {"message": error}
    return {"type": kind, "item": item}


MESSAGE = {"type": "item.completed", "item": {"id": "item_2", "type": "agent_message", "text": "Unavailable."}}
DONE = {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 9}}


def test_codex_binds_items_and_marks_usage_as_turn_level(tmp_path: Path) -> None:
    events = [
        {"type": "turn.started"},
        mcp_item("item.started", status="in_progress"),
        mcp_item("item.completed", status="failed", error="HTTP error 503"),
        MESSAGE,
        DONE,
    ]

    turn = codex_read(codex(tmp_path, events), [PROMPT])[0]

    assert [(call.name, call.result.is_error if call.result else None) for call in turn.calls] == [
        ("mcp__powercontext__generate_experience", True)
    ]
    assert (turn.usage_granularity, turn.messages[0].usage.output_tokens, turn.terminal_text) == (
        "turn",
        9,
        "Unavailable.",
    )


@pytest.mark.parametrize(
    ("events", "problem"),
    [
        ([{"type": "turn.started"}, mcp_item("item.started", status="in_progress"), DONE], "without results"),
        ([{"type": "turn.started"}, MESSAGE, {"type": "turn.failed", "error": {"message": "x"}}], "failure"),
        ([{"type": "turn.started"}, MESSAGE], "did not complete"),
        ([{"type": "turn.started"}, MESSAGE, DONE, {"type": "turn.started"}, MESSAGE, DONE], "Additional turn"),
        (
            [{"type": "turn.started"}, mcp_item("item.completed"), mcp_item("item.completed"), MESSAGE, DONE],
            "Duplicate",
        ),
        (
            [
                {"type": "turn.started"},
                mcp_item(
                    "item.completed",
                    status="failed",
                    error="MCP tool call requires approval, but approval policy is never",
                ),
                MESSAGE,
                DONE,
            ],
            "refused",
        ),
        (
            [
                {"type": "turn.started"},
                mcp_item(
                    "item.completed",
                    status="failed",
                    error="PowerContext could not resolve the current Scope binding.",
                ),
                MESSAGE,
                DONE,
            ],
            "refused",
        ),
    ],
    ids=[
        "missing-result",
        "turn-failed",
        "incomplete",
        "additional-turn",
        "duplicate-id",
        "host-refused-call",
        "plugin-hook-refused-call",
    ],
)
def test_codex_rejects_unbindable_runs(tmp_path: Path, events: list[dict[str, Any]], problem: str) -> None:
    with pytest.raises(TranscriptError) as error:
        codex_read(codex(tmp_path, events), [PROMPT])

    assert any(problem in item for item in error.value.problems)


def test_codex_requires_a_receipt_time_for_every_line(tmp_path: Path) -> None:
    path = codex(tmp_path, [{"type": "turn.started"}, MESSAGE, DONE])
    (tmp_path / "receipt-times.json").write_text("[]", encoding="utf-8")

    with pytest.raises(TranscriptError, match="Receipt times"):
        codex_read(path, [PROMPT])


def test_codex_binds_shell_commands_as_host_tool_calls(tmp_path: Path) -> None:
    shell = {"id": "item_0", "type": "command_execution", "command": "Get-Content SKILL.md", "status": "in_progress"}
    events = [
        {"type": "turn.started"},
        {"type": "item.started", "item": shell},
        {"type": "item.completed", "item": {**shell, "status": "completed", "exit_code": 0, "aggregated_output": "x"}},
        MESSAGE,
        DONE,
    ]

    turn = codex_read(codex(tmp_path, events), [PROMPT])[0]

    assert [(call.name, call.arguments, call.result.is_error if call.result else None) for call in turn.calls] == [
        ("shell", {"command": "Get-Content SKILL.md"}, False)
    ]


def test_codex_keeps_issue_times_of_overlapping_calls(tmp_path: Path) -> None:
    def item(item_id: str, kind: str) -> dict[str, Any]:
        event = mcp_item(kind, status="in_progress" if kind == "item.started" else "failed", error="HTTP error 503")
        return {**event, "item": {**event["item"], "id": item_id}}

    # Both calls start before either completes, and the second completes first.
    events = [
        {"type": "turn.started"},
        item("item_a", "item.started"),
        item("item_b", "item.started"),
        item("item_b", "item.completed"),
        item("item_a", "item.completed"),
        MESSAGE,
        DONE,
    ]

    turn = codex_read(codex(tmp_path, events), [PROMPT])[0]

    assert [(call.call_id, call.at.second, call.result.at.second if call.result else None) for call in turn.calls] == [
        ("item_a", 1, 4),
        ("item_b", 2, 3),
    ]
