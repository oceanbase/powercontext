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

import json
from pathlib import Path

import pytest

from integrations.codex.source_plugin.reader import read_codex_prompts


def test_codex_reader_imports_only_real_user_prompts_with_workspace(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex"
    session_dir = codex_home / "sessions" / "2026" / "09" / "13"
    session_dir.mkdir(parents=True)
    session = session_dir / "rollout.jsonl"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_jsonl(
        session,
        [
            {
                "timestamp": "2026-09-13T00:00:00Z",
                "ordinal": 0,
                "type": "session_meta",
                "payload": {"session_id": "session-a", "cwd": str(workspace)},
            },
            {
                "timestamp": "2026-09-13T00:00:01Z",
                "ordinal": 1,
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"<environment_context>\n<cwd>{workspace}</cwd>\n</environment_context>",
                        }
                    ],
                },
            },
            {
                "timestamp": "2026-09-13T00:00:02Z",
                "ordinal": 2,
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "remember this project decision"}],
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-a"},
                },
            },
            {
                "timestamp": "2026-09-13T00:00:03Z",
                "ordinal": 3,
                "type": "response_item",
                "payload": {"type": "message", "role": "assistant", "content": "assistant reply"},
            },
        ],
    )

    read_result = read_codex_prompts(codex_home=codex_home)

    assert read_result.scanned_files == 1
    assert read_result.failed_files == 0
    prompts = read_result.prompts
    assert len(prompts) == 1
    prompt = prompts[0]
    assert prompt.content == "remember this project decision"
    assert prompt.cwd == str(workspace)
    assert prompt.session_id == "session-a"
    assert prompt.turn_id == "turn-a"
    assert prompt.relative_session_file == "2026/09/13/rollout.jsonl"
    assert prompt.source_id_for_scope("scope-a").startswith("codex-user-prompt:")


def test_codex_reader_skips_sessions_without_workspace(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex"
    session_dir = codex_home / "sessions"
    session_dir.mkdir(parents=True)
    _write_jsonl(
        session_dir / "rollout.jsonl",
        [
            {
                "timestamp": "2026-09-13T00:00:02Z",
                "ordinal": 2,
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "no workspace for this one"}],
                },
            },
        ],
    )

    read_result = read_codex_prompts(codex_home=codex_home)

    assert read_result.scanned_files == 1
    assert read_result.prompts == ()


def test_codex_reader_recovers_turn_ids_from_turn_context_and_task_started(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex"
    session_dir = codex_home / "sessions"
    session_dir.mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_jsonl(
        session_dir / "rollout.jsonl",
        [
            {"ordinal": 0, "type": "session_meta", "payload": {"session_id": "session-a", "cwd": str(workspace)}},
            {"ordinal": 1, "type": "turn_context", "payload": {"turn_id": "turn-a", "cwd": str(workspace)}},
            {
                "ordinal": 2,
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "same"}]},
            },
            {
                "ordinal": 3,
                "type": "event_msg",
                "payload": {"type": "task_started", "request_id": "turn-b"},
            },
            {
                "ordinal": 4,
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "same"}]},
            },
        ],
    )

    read_result = read_codex_prompts(codex_home=codex_home)

    assert [prompt.turn_id for prompt in read_result.prompts] == ["turn-a", "turn-b"]
    assert read_result.prompts[0].source_id_for_scope("scope-a") != read_result.prompts[1].source_id_for_scope(
        "scope-a"
    )


def test_codex_reader_skips_host_generated_user_messages(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex"
    session_dir = codex_home / "sessions"
    session_dir.mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_jsonl(
        session_dir / "rollout.jsonl",
        [
            {"ordinal": 0, "type": "session_meta", "payload": {"cwd": str(workspace)}},
            {
                "ordinal": 1,
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "# AGENTS.md instructions for /repo\n\n<environment_context>...</environment_context>",
                        }
                    ],
                },
            },
            {
                "ordinal": 2,
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "<turn_aborted>interrupted</turn_aborted>"}],
                },
            },
            {
                "ordinal": 3,
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": '<codex_internal_context source="goal">continue</codex_internal_context>',
                        }
                    ],
                },
            },
            {
                "ordinal": 4,
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "real prompt"}],
                },
            },
        ],
    )

    read_result = read_codex_prompts(codex_home=codex_home)

    assert [prompt.content for prompt in read_result.prompts] == ["real prompt"]


def test_codex_reader_counts_bad_session_files_and_continues(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex"
    session_dir = codex_home / "sessions"
    session_dir.mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (session_dir / "bad.jsonl").write_text("{not json}\n", encoding="utf-8")
    _write_jsonl(
        session_dir / "good.jsonl",
        [
            {"ordinal": 0, "type": "session_meta", "payload": {"cwd": str(workspace)}},
            {
                "ordinal": 1,
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "good"}]},
            },
        ],
    )

    read_result = read_codex_prompts(codex_home=codex_home)

    assert read_result.scanned_files == 2
    assert read_result.failed_files == 1
    assert [prompt.content for prompt in read_result.prompts] == ["good"]


def test_codex_reader_counts_invalid_utf8_session_files_and_continues(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex"
    session_dir = codex_home / "sessions"
    session_dir.mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (session_dir / "bad.jsonl").write_bytes(b"\xff\n")
    _write_jsonl(
        session_dir / "good.jsonl",
        [
            {"ordinal": 0, "type": "session_meta", "payload": {"cwd": str(workspace)}},
            {
                "ordinal": 1,
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "good"}]},
            },
        ],
    )

    read_result = read_codex_prompts(codex_home=codex_home)

    assert read_result.scanned_files == 2
    assert read_result.failed_files == 1
    assert [prompt.content for prompt in read_result.prompts] == ["good"]


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(f"{json.dumps(row)}\n" for row in rows), encoding="utf-8")


@pytest.mark.parametrize(
    "text",
    [
        '<external_codex_apps_open_page>{"page_id":null}</external_codex_apps_open_page>',
        '<external_codex_apps_selected_page_part_1_of_1>{"page_id":null}</external_codex_apps_selected_page_part_1_of_1>',
    ],
)
def test_codex_reader_excludes_page_context(tmp_path, text):
    home = tmp_path / "codex"
    sessions = home / "sessions"
    sessions.mkdir(parents=True)
    _write_jsonl(
        sessions / "rollout.jsonl",
        [
            {"type": "session_meta", "payload": {"id": "session", "cwd": str(tmp_path)}},
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": text}},
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "real prompt"}},
        ],
    )
    assert [prompt.content for prompt in read_codex_prompts(codex_home=home).prompts] == ["real prompt"]
