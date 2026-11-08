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

"""Codex session decoding owned by the optional Source plugin."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal, cast

from pydantic import JsonValue


class SessionImportError(RuntimeError):
    """Raised when a host session import cannot be completed."""

    @classmethod
    def unreadable_codex_session(cls, session_file: Path) -> SessionImportError:
        return cls(f"cannot read Codex session file: {session_file}")

    @classmethod
    def unparsable_codex_session(cls, session_file: Path, line_number: int) -> SessionImportError:
        return cls(f"cannot parse Codex session file {session_file} line {line_number}")

    @classmethod
    def invalid_codex_record(cls, session_file: Path, line_number: int) -> SessionImportError:
        return cls(f"invalid Codex session record in {session_file} line {line_number}")


@dataclass(frozen=True, slots=True)
class ImportedPrompt:
    """One user prompt recovered from a host session file."""

    host: Literal["codex"]
    session_file: Path
    relative_session_file: str
    line_number: int
    ordinal: int
    prompt_index: int
    content: str
    cwd: str
    session_id: str | None = None
    turn_id: str | None = None
    timestamp: str | None = None

    def source_id_for_scope(self, scope_id: str) -> str:
        identity = "\0".join((scope_id, self.session_id or "", self.turn_id or "", self.content))
        return f"codex-user-prompt:{sha256(identity.encode()).hexdigest()}"


@dataclass(frozen=True, slots=True)
class CodexPromptReadResult:
    """Prompt records and file-level scan statistics for Codex sessions."""

    prompts: tuple[ImportedPrompt, ...]
    scanned_files: int
    failed_files: int = 0


def read_codex_prompts(*, codex_home: Path | None = None) -> CodexPromptReadResult:
    """Read user prompts from Codex JSONL session files."""

    root = _codex_home(codex_home)
    session_root = root / "sessions"
    if not session_root.exists():
        return CodexPromptReadResult(prompts=(), scanned_files=0)
    prompts: list[ImportedPrompt] = []
    scanned_files = 0
    failed_files = 0
    for session_file in sorted(session_root.rglob("*.jsonl")):
        if not session_file.is_file():
            continue
        scanned_files += 1
        try:
            prompts.extend(_read_codex_session_file(session_file, session_root=session_root))
        except SessionImportError:
            failed_files += 1
    return CodexPromptReadResult(prompts=tuple(prompts), scanned_files=scanned_files, failed_files=failed_files)


def _read_codex_session_file(session_file: Path, *, session_root: Path) -> tuple[ImportedPrompt, ...]:
    session_id: str | None = None
    turn_id: str | None = None
    cwd: str | None = None
    environment_cwd: str | None = None
    relative_session_file = _relative_session_file(session_file, session_root)
    prompts: list[ImportedPrompt] = []
    try:
        stream = session_file.open(encoding="utf-8")
    except OSError as error:
        raise SessionImportError.unreadable_codex_session(session_file) from error
    try:
        with stream:
            for line_number, line in enumerate(stream, start=1):
                record = _read_codex_record(line, session_file=session_file, line_number=line_number)
                if record is None:
                    continue
                payload = record.get("payload")
                if not isinstance(payload, dict):
                    continue
                typed_payload = cast(dict[str, object], payload)
                record_type = record.get("type")
                if record_type == "session_meta":
                    session_id = (
                        _string(typed_payload.get("session_id")) or _string(typed_payload.get("id")) or session_id
                    )
                    cwd = _string(typed_payload.get("cwd")) or cwd
                    continue
                if record_type == "turn_context" or (
                    record_type == "event_msg" and typed_payload.get("type") == "task_started"
                ):
                    turn_id = _record_turn_id(record, typed_payload) or turn_id
                    cwd = _string(typed_payload.get("cwd")) or cwd
                    continue
                environment_cwd = _environment_cwd_from_record(record, typed_payload) or environment_cwd
                prompt = _prompt_from_codex_record(
                    record,
                    typed_payload,
                    session_file=session_file,
                    relative_session_file=relative_session_file,
                    line_number=line_number,
                    prompt_index=len(prompts),
                    cwd=cwd or environment_cwd,
                    session_id=session_id,
                    turn_id=turn_id,
                )
                if prompt is None:
                    continue
                prompts.append(prompt)
    except UnicodeDecodeError as error:
        raise SessionImportError.unreadable_codex_session(session_file) from error
    return tuple(prompts)


def _read_codex_record(line: str, *, session_file: Path, line_number: int) -> dict[str, object] | None:
    if not line.strip():
        return None
    try:
        record = json.loads(line)
    except json.JSONDecodeError as error:
        raise SessionImportError.unparsable_codex_session(session_file, line_number) from error
    if not isinstance(record, dict):
        raise SessionImportError.invalid_codex_record(session_file, line_number)
    return record


def _prompt_from_codex_record(
    record: Mapping[str, object],
    payload: Mapping[str, object],
    *,
    session_file: Path,
    relative_session_file: str,
    line_number: int,
    prompt_index: int,
    cwd: str | None,
    session_id: str | None,
    turn_id: str | None,
) -> ImportedPrompt | None:
    if record.get("type") != "response_item" or payload.get("type") != "message" or payload.get("role") != "user":
        return None
    content = _message_text(payload.get("content"))
    if content is None or _is_synthetic_codex_user_message(content, payload) or cwd is None:
        return None
    return ImportedPrompt(
        host="codex",
        session_file=session_file,
        relative_session_file=relative_session_file,
        line_number=line_number,
        ordinal=_int(record.get("ordinal"), default=line_number),
        prompt_index=prompt_index,
        content=content,
        cwd=cwd,
        session_id=session_id,
        turn_id=_codex_turn_id(payload) or turn_id,
        timestamp=_string(record.get("timestamp")),
    )


def _metadata(prompt: ImportedPrompt) -> dict[str, JsonValue]:
    metadata: dict[str, JsonValue] = {
        "origin": "codex",
        "event": "user_prompt_submit",
        "cwd": prompt.cwd,
    }
    if prompt.session_id is not None:
        metadata["session_id"] = prompt.session_id
    if prompt.turn_id is not None:
        metadata["turn_id"] = prompt.turn_id
    return metadata


def _codex_home(codex_home: Path | None) -> Path:
    if codex_home is not None:
        return codex_home.expanduser()
    return Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()


def _relative_session_file(session_file: Path, session_root: Path) -> str:
    try:
        return session_file.relative_to(session_root).as_posix()
    except ValueError:
        return session_file.name


def _message_text(content: object) -> str | None:
    if isinstance(content, str):
        return content if content.strip() else None
    if not isinstance(content, list):
        return None
    parts: list[str] = []
    for item in content:
        if not isinstance(item, Mapping):
            continue
        text = item.get("text")
        if item.get("type") in {"input_text", "text"} and isinstance(text, str):
            parts.append(text)
    rendered = "\n".join(parts)
    return rendered if rendered.strip() else None


def _codex_turn_id(payload: Mapping[str, object]) -> str | None:
    direct = _string(payload.get("turn_id"))
    if direct is not None:
        return direct
    metadata = payload.get("internal_chat_message_metadata_passthrough")
    if isinstance(metadata, Mapping):
        return _string(metadata.get("turn_id"))
    return None


def _record_turn_id(record: Mapping[str, object], payload: Mapping[str, object]) -> str | None:
    return _payload_identifier(payload, "turn_id", "request_id", "id") or _payload_identifier(
        record, "turn_id", "request_id"
    )


def _payload_identifier(payload: Mapping[str, object], *keys: str) -> str | None:
    for key in keys:
        value = _string(payload.get(key))
        if value:
            return value
    return None


def _is_synthetic_codex_user_message(content: str, payload: Mapping[str, object]) -> bool:
    metadata = payload.get("internal_chat_message_metadata_passthrough")
    if isinstance(metadata, Mapping):
        kinds = metadata.get("content_item_kinds")
        if (
            isinstance(kinds, list)
            and kinds
            and all(
                kind in {"additional_content.codex_apps_open_page", "additional_content.codex_apps_selected_page"}
                for kind in kinds
            )
        ):
            return True
    stripped = content.strip()
    return (
        stripped.startswith("<environment_context>") and stripped.endswith("</environment_context>")
    ) or stripped.startswith((
        "# AGENTS.md instructions",
        "<turn_aborted",
        "<codex_internal_context",
        "<external_codex_apps_open_page>",
        "<external_codex_apps_selected_page_part_",
    ))


def _environment_cwd_from_record(record: Mapping[str, object], payload: Mapping[str, object]) -> str | None:
    if record.get("type") != "response_item" or payload.get("type") != "message" or payload.get("role") != "user":
        return None
    content = _message_text(payload.get("content"))
    if content is None or not _is_synthetic_codex_user_message(content, payload):
        return None
    return _cwd_from_environment_context(content)


def _cwd_from_environment_context(content: str) -> str | None:
    for raw in content.splitlines():
        stripped = raw.strip()
        if stripped.startswith("<cwd>") and stripped.endswith("</cwd>"):
            return stripped.removeprefix("<cwd>").removesuffix("</cwd>")
    return None


def _string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _int(value: object, *, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default
