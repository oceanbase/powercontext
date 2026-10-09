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
from typing import Any

import pytest
from typer.testing import CliRunner

import powercontext.client.cli as client_cli
from powercontext.cli.app import create_cli
from powercontext.client.errors import ServerResponseError
from powercontext.client.session_import import (
    SessionImportResult,
    import_sessions,
    read_codex_prompts,
)
from powercontext.http import (
    CaptureContentSourceResponse,
    CaptureStatus,
    ScopeDescriptor,
    SourceReference,
)

_DESTINATION_ID = "https://server-a.example"


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


def test_import_sessions_resolves_scope_and_captures_sources(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_TEST_TOKEN", "TOKEN_VALUE")
    codex_home = tmp_path / "codex"
    session_dir = codex_home / "sessions"
    session_dir.mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_jsonl(
        session_dir / "rollout.jsonl",
        [
            {
                "timestamp": "2026-09-13T00:00:00Z",
                "ordinal": 0,
                "type": "session_meta",
                "payload": {"session_id": "session-a", "cwd": str(workspace)},
            },
            {
                "timestamp": "2026-09-13T00:00:02Z",
                "ordinal": 2,
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "TOKEN_VALUE"}],
                },
            },
        ],
    )
    checkpoint = tmp_path / "checkpoint.json"
    client = _ImportClient()

    result = _run(
        import_sessions(
            client,
            host="codex",
            destination_id=_DESTINATION_ID,
            codex_home=codex_home,
            checkpoint_file=checkpoint,
        )
    )

    assert result == SessionImportResult(
        host="codex",
        scanned_files=1,
        failed_files=0,
        discovered=1,
        imported=1,
        skipped=0,
        failed=0,
        skipped_by_reason={},
        failed_by_reason={},
        checkpoint_file=str(checkpoint),
        items=result.items,
    )
    assert len(client.resolve_scope_requests) == 1
    assert client.resolve_scope_requests[0].allow_default is False
    assert [key.kind for key in client.resolve_scope_requests[0].binding_keys] == ["session", "workspace"]
    assert len(client.capture_requests) == 1
    assert client.capture_requests[0].source_id.startswith("codex-user-prompt:")
    assert client.capture_requests[0].content == "[REDACTED]"
    assert client.capture_requests[0].metadata["origin"] == "codex"
    assert client.capture_requests[0].metadata["event"] == "user_prompt_submit"
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert saved["destination_id"] == _DESTINATION_ID


def test_import_sessions_redacts_auth_from_selected_codex_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("CODEX_HOME", raising=False)
    secret = "codex-home-token-value"  # noqa: S105 - synthetic redaction sentinel.
    codex_home = tmp_path / "codex"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text(json.dumps({"tokens": {"access_token": secret}}), encoding="utf-8")
    session_dir = codex_home / "sessions"
    session_dir.mkdir()
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
                    "content": [{"type": "input_text", "text": f"token is {secret}"}],
                },
            },
        ],
    )
    client = _ImportClient()

    _run(import_sessions(client, host="codex", destination_id=_DESTINATION_ID, codex_home=codex_home))

    assert secret not in client.capture_requests[0].content
    assert client.capture_requests[0].content == "token is [REDACTED]"


def test_import_sessions_dry_run_does_not_write_sources(tmp_path: Path) -> None:
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
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "dry run"}]},
            },
        ],
    )
    client = _ImportClient()

    result = _run(
        import_sessions(
            client,
            host="codex",
            destination_id=_DESTINATION_ID,
            codex_home=codex_home,
            scope_id="explicit",
            dry_run=True,
        )
    )

    assert result.imported == 1
    assert result.skipped_by_reason == {}
    assert result.checkpoint_file is None
    assert client.resolve_scope_requests == []
    assert client.capture_requests == []


def test_import_sessions_skips_unresolved_scope_without_default_fallback(tmp_path: Path) -> None:
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
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "skip me"}]},
            },
        ],
    )
    client = _ImportClient(resolve_error=ServerResponseError(status_code=404, request_id=None))

    result = _run(import_sessions(client, host="codex", destination_id=_DESTINATION_ID, codex_home=codex_home))

    assert result.imported == 0
    assert result.skipped == 1
    assert result.skipped_by_reason == {"unresolved_scope": 1}
    assert client.capture_requests == []


def test_import_sessions_uses_live_hook_source_identity(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex"
    session_dir = codex_home / "sessions"
    session_dir.mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_jsonl(
        session_dir / "rollout.jsonl",
        [
            {
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
                    "content": [{"type": "input_text", "text": "overlap"}],
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-a"},
                },
            },
        ],
    )
    client = _ImportClient()

    _run(import_sessions(client, host="codex", destination_id=_DESTINATION_ID, codex_home=codex_home))

    assert len(client.capture_requests) == 1
    assert client.capture_requests[0].source_id == _live_hook_source_id("scope-a", "session-a", "turn-a", "overlap")
    assert client.capture_requests[0].metadata == {
        "origin": "codex",
        "event": "user_prompt_submit",
        "cwd": str(workspace),
        "session_id": "session-a",
        "turn_id": "turn-a",
    }


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


@pytest.mark.parametrize(
    ("content", "metadata"),
    [
        ('<external_codex_apps_open_page>{"page_id":null}</external_codex_apps_open_page>', {}),
        (
            '<external_codex_apps_open_page>{"page_id":"page-a"}</external_codex_apps_open_page>',
            {"content_item_kinds": ["additional_content.codex_apps_open_page"]},
        ),
        ("Page context", {"content_item_kinds": ["additional_content.codex_apps_open_page"]}),
    ],
)
def test_import_sessions_excludes_host_generated_page_context(
    tmp_path: Path, content: str, metadata: dict[str, object]
) -> None:
    codex_home = tmp_path / "codex"
    session_dir = codex_home / "sessions"
    session_dir.mkdir(parents=True)
    _write_jsonl(
        session_dir / "rollout.jsonl",
        [
            {"type": "session_meta", "payload": {"id": "session-a", "cwd": str(tmp_path)}},
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": content}],
                    "internal_chat_message_metadata_passthrough": metadata,
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "real prompt"}],
                    "internal_chat_message_metadata_passthrough": {"content_item_kinds": ["text"], "turn_id": "turn-a"},
                },
            },
        ],
    )
    client = _ImportClient()

    result = _run(import_sessions(client, host="codex", destination_id=_DESTINATION_ID, codex_home=codex_home))

    assert result.discovered == result.imported == 1
    assert result.failed == result.failed_files == 0
    assert [source["content"] for source in client.stored_sources.values()] == ["real prompt"]
    assert client.capture_requests[0].metadata["turn_id"] == "turn-a"


def test_import_sessions_overlaps_with_live_capture_without_conflict(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex"
    session_dir = codex_home / "sessions"
    session_dir.mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_jsonl(
        session_dir / "rollout.jsonl",
        [
            {
                "ordinal": 0,
                "type": "session_meta",
                "payload": {"session_id": "session-a", "cwd": str(workspace)},
            },
            {
                "ordinal": 1,
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "already captured live"}],
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-a"},
                },
            },
        ],
    )
    source_id = _live_hook_source_id("scope-a", "session-a", "turn-a", "already captured live")
    client = _ImportClient(
        stored_sources={
            source_id: {
                "content": "already captured live",
                "metadata": {
                    "origin": "codex",
                    "event": "user_prompt_submit",
                    "cwd": str(workspace),
                    "session_id": "session-a",
                    "turn_id": "turn-a",
                },
                "position": 1,
            }
        }
    )

    result = _run(import_sessions(client, host="codex", destination_id=_DESTINATION_ID, codex_home=codex_home))

    assert result.imported == 1
    assert result.failed == 0
    assert len(client.capture_requests) == 1
    assert client.capture_requests[0].source_id == source_id


def test_import_sessions_skips_redaction_conflict_with_existing_live_capture(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_TEST_TOKEN", "RAW_SECRET")
    codex_home = tmp_path / "codex"
    session_dir = codex_home / "sessions"
    session_dir.mkdir(parents=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write_jsonl(
        session_dir / "rollout.jsonl",
        [
            {
                "ordinal": 0,
                "type": "session_meta",
                "payload": {"session_id": "session-a", "cwd": str(workspace)},
            },
            {
                "ordinal": 1,
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "RAW_SECRET"}],
                    "internal_chat_message_metadata_passthrough": {"turn_id": "turn-a"},
                },
            },
        ],
    )
    checkpoint = tmp_path / "checkpoint.json"
    source_id = _live_hook_source_id("scope-a", "session-a", "turn-a", "RAW_SECRET")
    client = _ImportClient(
        stored_sources={
            source_id: {
                "content": "RAW_SECRET",
                "metadata": {
                    "origin": "codex",
                    "event": "user_prompt_submit",
                    "cwd": str(workspace),
                    "session_id": "session-a",
                    "turn_id": "turn-a",
                },
                "position": 1,
            }
        }
    )

    result = _run(
        import_sessions(
            client,
            host="codex",
            destination_id=_DESTINATION_ID,
            codex_home=codex_home,
            checkpoint_file=checkpoint,
        )
    )

    assert result.imported == 0
    assert result.failed == 0
    assert result.skipped_by_reason == {"redaction_conflict": 1}
    assert client.capture_requests[0].source_id == source_id
    assert client.capture_requests[0].content == "[REDACTED]"
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert next(iter(saved["items"].values()))["status"] == "skipped"
    assert next(iter(saved["items"].values()))["reason"] == "redaction_conflict"


def test_import_sessions_checkpoint_skips_already_accepted_items(tmp_path: Path) -> None:
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
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "once"}]},
            },
        ],
    )
    checkpoint = tmp_path / "checkpoint.json"
    client = _ImportClient()

    first = _run(
        import_sessions(
            client,
            host="codex",
            destination_id=_DESTINATION_ID,
            codex_home=codex_home,
            checkpoint_file=checkpoint,
        )
    )
    second = _run(
        import_sessions(
            client,
            host="codex",
            destination_id=_DESTINATION_ID,
            codex_home=codex_home,
            checkpoint_file=checkpoint,
        )
    )

    assert first.imported == 1
    assert second.imported == 0
    assert second.skipped_by_reason == {"checkpoint_accepted": 1}
    assert len(client.capture_requests) == 1
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert next(iter(saved["items"].values()))["status"] == "accepted"


def test_import_sessions_does_not_trust_checkpoint_from_another_destination(tmp_path: Path) -> None:
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
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "once"}]},
            },
        ],
    )
    checkpoint = tmp_path / "checkpoint.json"
    first_client = _ImportClient()
    second_client = _ImportClient()

    _run(
        import_sessions(
            first_client,
            host="codex",
            destination_id="https://server-a.example",
            codex_home=codex_home,
            checkpoint_file=checkpoint,
        )
    )
    second = _run(
        import_sessions(
            second_client,
            host="codex",
            destination_id="https://server-b.example",
            codex_home=codex_home,
            checkpoint_file=checkpoint,
        )
    )

    assert second.imported == 1
    assert second.skipped_by_reason == {}
    assert len(second_client.capture_requests) == 1
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert saved["destination_id"] == "https://server-b.example"


def test_import_sessions_does_not_trust_legacy_checkpoint_without_destination(tmp_path: Path) -> None:
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
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "first"}]},
            },
        ],
    )
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(
        json.dumps({
            "schema": "powercontext.session-import.codex.v1",
            "items": {"legacy-source": {"status": "accepted"}},
        }),
        encoding="utf-8",
    )
    client = _ImportClient()

    result = _run(
        import_sessions(
            client,
            host="codex",
            destination_id=_DESTINATION_ID,
            codex_home=codex_home,
            checkpoint_file=checkpoint,
        )
    )

    assert result.imported == 1
    assert len(client.capture_requests) == 1
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert saved["schema"] == "powercontext.session-import.codex.v2"
    assert saved["destination_id"] == _DESTINATION_ID


def test_import_sessions_records_failed_items_in_checkpoint(tmp_path: Path) -> None:
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
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "fails"}]},
            },
        ],
    )
    checkpoint = tmp_path / "checkpoint.json"
    client = _ImportClient(capture_error=ServerResponseError(status_code=503, request_id=None))

    result = _run(
        import_sessions(
            client,
            host="codex",
            destination_id=_DESTINATION_ID,
            codex_home=codex_home,
            checkpoint_file=checkpoint,
        )
    )

    assert result.imported == 0
    assert result.failed == 1
    assert result.failed_by_reason == {"capture_failed": 1}
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert next(iter(saved["items"].values()))["status"] == "failed"


def test_import_sessions_retries_failed_checkpoint_items_without_duplicate_writes(tmp_path: Path) -> None:
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
                    "content": [{"type": "input_text", "text": "resume me"}],
                },
            },
        ],
    )
    checkpoint = tmp_path / "checkpoint.json"
    failing_client = _ImportClient(capture_error=ServerResponseError(status_code=503, request_id=None))
    successful_client = _ImportClient()

    failed = _run(
        import_sessions(
            failing_client,
            host="codex",
            destination_id=_DESTINATION_ID,
            codex_home=codex_home,
            checkpoint_file=checkpoint,
        )
    )
    resumed = _run(
        import_sessions(
            successful_client,
            host="codex",
            destination_id=_DESTINATION_ID,
            codex_home=codex_home,
            checkpoint_file=checkpoint,
        )
    )
    repeated = _run(
        import_sessions(
            successful_client,
            host="codex",
            destination_id=_DESTINATION_ID,
            codex_home=codex_home,
            checkpoint_file=checkpoint,
        )
    )

    assert failed.failed_by_reason == {"capture_failed": 1}
    assert resumed.imported == 1
    assert repeated.skipped_by_reason == {"checkpoint_accepted": 1}
    assert len(successful_client.capture_requests) == 1
    saved = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert next(iter(saved["items"].values()))["status"] == "accepted"


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


def test_import_sessions_cli_requires_host() -> None:
    result = CliRunner().invoke(create_cli([]), ["import-sessions", "--dry-run"])

    assert result.exit_code != 0
    assert "Missing option" in result.output


def test_import_sessions_cli_does_not_offer_shared_journal_flush() -> None:
    result = CliRunner().invoke(create_cli([]), ["import-sessions", "--help"])

    assert result.exit_code == 0
    assert "--flush" not in result.output


def test_import_sessions_cli_writes_json_summary(monkeypatch, tmp_path: Path) -> None:
    async def fake_import(_client: object, **kwargs: object) -> SessionImportResult:
        assert kwargs["host"] == "codex"
        assert kwargs["scope_id"] == "scope-a"
        assert kwargs["codex_home"] == tmp_path
        assert kwargs["checkpoint_file"] is None
        assert kwargs["dry_run"] is True
        assert kwargs["destination_id"] == "http://127.0.0.1:8000"
        return SessionImportResult(
            host="codex",
            scanned_files=2,
            failed_files=0,
            discovered=3,
            imported=3,
            skipped=0,
            failed=0,
            skipped_by_reason={},
            failed_by_reason={},
            checkpoint_file=None,
            items=(),
        )

    monkeypatch.setattr(client_cli, "PowerContextClient", _ClientContext)
    monkeypatch.setattr(client_cli, "run_session_import", fake_import)

    result = CliRunner().invoke(
        create_cli([]),
        [
            "--json",
            "import-sessions",
            "--host",
            "codex",
            "--scope-id",
            "scope-a",
            "--codex-home",
            str(tmp_path),
            "--dry-run",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.output) == {
        "host": "codex",
        "scanned_files": 2,
        "failed_files": 0,
        "discovered": 3,
        "imported": 3,
        "skipped": 0,
        "failed": 0,
        "skipped_by_reason": {},
        "failed_by_reason": {},
        "checkpoint_file": None,
        "items": [],
    }


class _ImportClient:
    def __init__(
        self,
        *,
        resolve_error: ServerResponseError | None = None,
        capture_error: ServerResponseError | None = None,
        stored_sources: dict[str, dict[str, object]] | None = None,
    ) -> None:
        self.resolve_error = resolve_error
        self.capture_error = capture_error
        self.stored_sources = stored_sources or {}
        self.resolve_scope_requests: list[Any] = []
        self.capture_requests: list[Any] = []

    async def resolve_scope_binding(self, request: Any) -> ScopeDescriptor:
        self.resolve_scope_requests.append(request)
        if self.resolve_error is not None:
            raise self.resolve_error
        return ScopeDescriptor(
            scope_id="scope-a",
            title="Scope",
            summary="Scope",
            context_references=[],
            external_references=[],
            version=1,
        )

    async def capture_content_source(self, request: Any) -> CaptureContentSourceResponse:
        self.capture_requests.append(request)
        if self.capture_error is not None:
            raise self.capture_error
        existing = self.stored_sources.get(request.source_id)
        if existing is not None:
            if existing["content"] != request.content or existing["metadata"] != request.metadata:
                raise ServerResponseError(status_code=409, request_id=None, code="source_conflict")
            position = existing["position"]
            assert isinstance(position, int)
            return CaptureContentSourceResponse(
                status=CaptureStatus.ACCEPTED,
                source=SourceReference(name="content", source_id=request.source_id),
                position=position,
            )
        position = len(self.stored_sources) + 1
        self.stored_sources[request.source_id] = {
            "content": request.content,
            "metadata": request.metadata,
            "position": position,
        }
        return CaptureContentSourceResponse(
            status=CaptureStatus.ACCEPTED,
            source=SourceReference(name="content", source_id=request.source_id),
            position=position,
        )


class _ClientContext:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_args: object) -> None:
        return None


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("".join(f"{json.dumps(row)}\n" for row in rows), encoding="utf-8")


def _live_hook_source_id(scope_id: str, session_id: str, turn_id: str, prompt: str) -> str:
    from hashlib import sha256

    identity = "\0".join((scope_id, session_id, turn_id, prompt))
    return f"codex-user-prompt:{sha256(identity.encode()).hexdigest()}"


def _run(awaitable):
    import asyncio

    return asyncio.run(awaitable)
