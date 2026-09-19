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

"""Opt-in import of pre-install agent session prompts into Content Sources."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from shutil import which
from typing import Literal, Protocol, cast

from powercontext.client.capture import redact_known_secrets
from powercontext.client.errors import ClientError, ServerResponseError
from powercontext.http import (
    CaptureContentSourceRequest,
    CaptureContentSourceResponse,
    FlushMemoryRequest,
    FlushMemoryResponse,
    ResolveScopeBindingRequest,
    ScopeBindingKey,
    ScopeDescriptor,
)

SUPPORTED_SESSION_IMPORT_HOSTS = ("codex",)
_MAX_CONTENT_LENGTH = 200_000
_UNRESOLVED_SCOPE_STATUSES = frozenset({404, 422})
_CHECKPOINT_SCHEMA = "powercontext.session-import.codex.v1"


class SessionImportError(RuntimeError):
    """Raised when a host session import cannot be completed."""

    @classmethod
    def unsupported_host(cls, host: str) -> SessionImportError:
        return cls(f"unsupported host: {host}. Choose from: {', '.join(SUPPORTED_SESSION_IMPORT_HOSTS)}.")

    @classmethod
    def unreadable_codex_session(cls, session_file: Path) -> SessionImportError:
        return cls(f"cannot read Codex session file: {session_file}")

    @classmethod
    def unparsable_codex_session(cls, session_file: Path, line_number: int) -> SessionImportError:
        return cls(f"cannot parse Codex session file {session_file} line {line_number}")

    @classmethod
    def invalid_codex_record(cls, session_file: Path, line_number: int) -> SessionImportError:
        return cls(f"invalid Codex session record in {session_file} line {line_number}")

    @classmethod
    def stalled_flush(cls, scope_id: str, target_position: int, current_cursor: int) -> SessionImportError:
        return cls(
            f"memory flush for Scope {scope_id} stopped at cursor {current_cursor} before imported Source "
            f"position {target_position}"
        )


class SessionImportClient(Protocol):
    """Client surface needed by session importers."""

    async def resolve_scope_binding(self, request: ResolveScopeBindingRequest) -> ScopeDescriptor: ...

    async def capture_content_source(self, request: CaptureContentSourceRequest) -> CaptureContentSourceResponse: ...

    async def flush_memory(self, request: FlushMemoryRequest) -> FlushMemoryResponse: ...


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


class SessionImportItemStatus(StrEnum):
    ACCEPTED = "accepted"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class SessionImportItemResult:
    """Observable result for one importable prompt."""

    status: SessionImportItemStatus
    source_id: str | None
    reason: str | None
    session_file: str
    line_number: int
    scope_id: str | None = None
    position: int | None = None

    def as_json(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "status": self.status.value,
            "session_file": self.session_file,
            "line_number": self.line_number,
        }
        if self.source_id is not None:
            payload["source_id"] = self.source_id
        if self.scope_id is not None:
            payload["scope_id"] = self.scope_id
        if self.position is not None:
            payload["position"] = self.position
        if self.reason is not None:
            payload["reason"] = self.reason
        return payload


@dataclass(frozen=True, slots=True)
class CodexPromptReadResult:
    """Prompt records and file-level scan statistics for Codex sessions."""

    prompts: tuple[ImportedPrompt, ...]
    scanned_files: int
    failed_files: int = 0


@dataclass(frozen=True, slots=True)
class SessionImportResult:
    """Summary of one import run."""

    host: str
    scanned_files: int
    failed_files: int
    discovered: int
    imported: int
    skipped: int
    failed: int
    skipped_by_reason: Mapping[str, int]
    failed_by_reason: Mapping[str, int]
    checkpoint_file: str | None
    items: tuple[SessionImportItemResult, ...]
    flushed_scopes: tuple[str, ...] = ()

    def as_json(self) -> dict[str, object]:
        return {
            "host": self.host,
            "scanned_files": self.scanned_files,
            "failed_files": self.failed_files,
            "discovered": self.discovered,
            "imported": self.imported,
            "skipped": self.skipped,
            "failed": self.failed,
            "skipped_by_reason": dict(self.skipped_by_reason),
            "failed_by_reason": dict(self.failed_by_reason),
            "checkpoint_file": self.checkpoint_file,
            "items": [item.as_json() for item in self.items],
            "flushed_scopes": list(self.flushed_scopes),
        }


@dataclass(frozen=True, slots=True)
class _PromptProcessingResult:
    item: SessionImportItemResult
    imported: bool = False
    pending_flush: tuple[str, int] | None = None


async def import_sessions(
    client: SessionImportClient,
    *,
    host: str,
    scope_id: str | None = None,
    codex_home: Path | None = None,
    checkpoint_file: Path | None = None,
    dry_run: bool = False,
    flush: bool = False,
) -> SessionImportResult:
    """Import historical user prompts from one supported host."""

    if host != "codex":
        raise SessionImportError.unsupported_host(host)

    codex_home = _codex_home(codex_home)
    checkpoint_file = _checkpoint_file(codex_home, checkpoint_file)
    checkpoint = _load_checkpoint(checkpoint_file)
    read_result = read_codex_prompts(codex_home=codex_home)
    imported = 0
    skipped_by_reason: dict[str, int] = {}
    failed_by_reason: dict[str, int] = {}
    items: list[SessionImportItemResult] = []
    pending_flush_positions: dict[str, int] = {}
    for prompt in read_result.prompts:
        processed = await _process_prompt(
            client,
            prompt,
            scope_id=scope_id,
            codex_home=codex_home,
            checkpoint=checkpoint,
            checkpoint_file=checkpoint_file,
            dry_run=dry_run,
        )
        item = processed.item
        items.append(item)
        if item.status == SessionImportItemStatus.SKIPPED and item.reason is not None:
            _count_skip(skipped_by_reason, item.reason)
        if item.status == SessionImportItemStatus.FAILED and item.reason is not None:
            _count_skip(failed_by_reason, item.reason)
        if processed.imported:
            imported += 1
        if processed.pending_flush is not None:
            pending_scope_id, pending_position = processed.pending_flush
            pending_flush_positions[pending_scope_id] = max(
                pending_position,
                pending_flush_positions.get(pending_scope_id, 0),
            )
    flushed_scopes: tuple[str, ...] = ()
    if flush and not dry_run:
        flushed_scopes = await _flush_scopes_through(client, pending_flush_positions)
    return SessionImportResult(
        host=host,
        scanned_files=read_result.scanned_files,
        failed_files=read_result.failed_files,
        discovered=len(read_result.prompts),
        imported=imported,
        skipped=sum(skipped_by_reason.values()),
        failed=sum(failed_by_reason.values()),
        skipped_by_reason=skipped_by_reason,
        failed_by_reason=failed_by_reason,
        checkpoint_file=None if dry_run else str(checkpoint_file),
        items=tuple(items),
        flushed_scopes=flushed_scopes,
    )


async def _process_prompt(
    client: SessionImportClient,
    prompt: ImportedPrompt,
    *,
    scope_id: str | None,
    codex_home: Path,
    checkpoint: dict[str, object],
    checkpoint_file: Path,
    dry_run: bool,
) -> _PromptProcessingResult:
    content = redact_known_secrets(prompt.content, codex_home=codex_home).strip()
    if not content:
        return _PromptProcessingResult(
            _item_result(prompt, SessionImportItemStatus.SKIPPED, reason="empty_after_redaction")
        )
    if len(content) > _MAX_CONTENT_LENGTH:
        return _PromptProcessingResult(_item_result(prompt, SessionImportItemStatus.SKIPPED, reason="too_large"))
    resolved_scope_id = scope_id or await _resolve_codex_scope(client, prompt)
    if resolved_scope_id is None:
        return _PromptProcessingResult(_item_result(prompt, SessionImportItemStatus.SKIPPED, reason="unresolved_scope"))
    source_id = prompt.source_id_for_scope(resolved_scope_id)
    checkpoint_position = _checkpoint_position(checkpoint, source_id)
    if _checkpoint_status(checkpoint, source_id) == SessionImportItemStatus.ACCEPTED.value:
        return _PromptProcessingResult(
            _item_result(
                prompt,
                SessionImportItemStatus.SKIPPED,
                source_id=source_id,
                scope_id=resolved_scope_id,
                position=checkpoint_position,
                reason="checkpoint_accepted",
            ),
            pending_flush=None if checkpoint_position is None else (resolved_scope_id, checkpoint_position),
        )
    if dry_run:
        return _PromptProcessingResult(
            _item_result(prompt, SessionImportItemStatus.ACCEPTED, source_id=source_id, scope_id=resolved_scope_id),
            imported=True,
        )
    try:
        captured = await client.capture_content_source(
            CaptureContentSourceRequest(
                scope_id=resolved_scope_id,
                source_id=source_id,
                content=content,
                metadata=_metadata(prompt),
            )
        )
    except ClientError as error:
        if _is_redacted_source_conflict(error, prompt, content):
            skipped = _item_result(
                prompt,
                SessionImportItemStatus.SKIPPED,
                source_id=source_id,
                scope_id=resolved_scope_id,
                reason="redaction_conflict",
            )
            _record_checkpoint(checkpoint, skipped)
            _save_checkpoint(checkpoint_file, checkpoint)
            return _PromptProcessingResult(skipped)
        failed = _item_result(
            prompt,
            SessionImportItemStatus.FAILED,
            source_id=source_id,
            scope_id=resolved_scope_id,
            reason="capture_failed",
        )
        _record_checkpoint(checkpoint, failed)
        _save_checkpoint(checkpoint_file, checkpoint)
        return _PromptProcessingResult(failed)
    accepted = _item_result(
        prompt,
        SessionImportItemStatus.ACCEPTED,
        source_id=source_id,
        scope_id=resolved_scope_id,
        position=captured.position,
    )
    _record_checkpoint(checkpoint, accepted)
    _save_checkpoint(checkpoint_file, checkpoint)
    return _PromptProcessingResult(accepted, imported=True, pending_flush=(resolved_scope_id, captured.position))


def _is_redacted_source_conflict(error: ClientError, prompt: ImportedPrompt, content: str) -> bool:
    return (
        isinstance(error, ServerResponseError)
        and error.status_code == 409
        and error.code == "source_conflict"
        and content != prompt.content
    )


async def _flush_scopes_through(
    client: SessionImportClient,
    pending_flush_positions: Mapping[str, int],
) -> tuple[str, ...]:
    flushed_scopes: list[str] = []
    for scope_id, target_position in sorted(pending_flush_positions.items()):
        current_cursor = 0
        while current_cursor < target_position:
            flushed = await client.flush_memory(FlushMemoryRequest(scope_id=scope_id))
            if flushed.current_cursor <= current_cursor:
                raise SessionImportError.stalled_flush(scope_id, target_position, flushed.current_cursor)
            current_cursor = flushed.current_cursor
        flushed_scopes.append(scope_id)
    return tuple(flushed_scopes)


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
    if content is None or _is_synthetic_codex_user_message(content) or cwd is None:
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


async def _resolve_codex_scope(client: SessionImportClient, prompt: ImportedPrompt) -> str | None:
    try:
        descriptor = await client.resolve_scope_binding(
            ResolveScopeBindingRequest(
                allow_default=False,
                binding_keys=[
                    *_session_binding_keys(prompt.session_id),
                    _workspace_binding_key(prompt.cwd),
                ],
            )
        )
    except ServerResponseError as error:
        if error.status_code in _UNRESOLVED_SCOPE_STATUSES:
            return None
        raise
    return descriptor.scope_id


def _count_skip(skipped_by_reason: dict[str, int], reason: str) -> None:
    skipped_by_reason[reason] = skipped_by_reason.get(reason, 0) + 1


def _item_result(
    prompt: ImportedPrompt,
    status: SessionImportItemStatus,
    *,
    source_id: str | None = None,
    scope_id: str | None = None,
    position: int | None = None,
    reason: str | None = None,
) -> SessionImportItemResult:
    return SessionImportItemResult(
        status=status,
        source_id=source_id,
        scope_id=scope_id,
        position=position,
        reason=reason,
        session_file=prompt.relative_session_file,
        line_number=prompt.line_number,
    )


def _checkpoint_file(codex_home: Path, checkpoint_file: Path | None) -> Path:
    if checkpoint_file is not None:
        return checkpoint_file.expanduser()
    return codex_home / ".powercontext" / "session-import" / "codex.json"


def _load_checkpoint(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema": _CHECKPOINT_SCHEMA, "items": {}}
    except (OSError, json.JSONDecodeError):
        return {"schema": _CHECKPOINT_SCHEMA, "items": {}}
    if not isinstance(value, dict) or value.get("schema") != _CHECKPOINT_SCHEMA:
        return {"schema": _CHECKPOINT_SCHEMA, "items": {}}
    items = value.get("items")
    if not isinstance(items, dict):
        value["items"] = {}
    return value


def _save_checkpoint(path: Path, checkpoint: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(checkpoint, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    temporary.replace(path)


def _checkpoint_status(checkpoint: Mapping[str, object], source_id: str) -> str | None:
    items = checkpoint.get("items")
    if not isinstance(items, Mapping):
        return None
    item = items.get(source_id)
    if not isinstance(item, Mapping):
        return None
    status = item.get("status")
    return status if isinstance(status, str) else None


def _checkpoint_position(checkpoint: Mapping[str, object], source_id: str) -> int | None:
    items = checkpoint.get("items")
    if not isinstance(items, Mapping):
        return None
    item = items.get(source_id)
    if not isinstance(item, Mapping):
        return None
    position = item.get("position")
    return position if isinstance(position, int) and position >= 1 else None


def _record_checkpoint(checkpoint: dict[str, object], result: SessionImportItemResult) -> None:
    if result.source_id is None:
        return
    items = checkpoint.get("items")
    if not isinstance(items, dict):
        checkpoint["items"] = {}
        items = checkpoint["items"]
    items = cast(dict[str, object], items)
    items[result.source_id] = result.as_json()


def _session_binding_keys(session_id: str | None) -> tuple[ScopeBindingKey, ...]:
    if session_id is None:
        return ()
    value = session_id.strip()
    if not value:
        return ()
    return (ScopeBindingKey(integration="codex", kind="session", external_id=value),)


def _workspace_binding_key(cwd: str) -> ScopeBindingKey:
    root_value = _git_value(cwd, "rev-parse", "--show-toplevel")
    root = Path(root_value or cwd).resolve(strict=False)
    external_id = sha256(os.fsencode(root)).hexdigest()
    return ScopeBindingKey(integration="codex", kind="workspace", external_id=external_id)


def _metadata(prompt: ImportedPrompt) -> dict[str, object]:
    metadata: dict[str, object] = {
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


def _is_synthetic_codex_user_message(content: str) -> bool:
    stripped = content.strip()
    return (
        stripped.startswith("<environment_context>") and stripped.endswith("</environment_context>")
    ) or stripped.startswith("# AGENTS.md instructions")


def _environment_cwd_from_record(record: Mapping[str, object], payload: Mapping[str, object]) -> str | None:
    if record.get("type") != "response_item" or payload.get("type") != "message" or payload.get("role") != "user":
        return None
    content = _message_text(payload.get("content"))
    if content is None or not _is_synthetic_codex_user_message(content):
        return None
    return _cwd_from_environment_context(content)


def _cwd_from_environment_context(content: str) -> str | None:
    for raw in content.splitlines():
        stripped = raw.strip()
        if stripped.startswith("<cwd>") and stripped.endswith("</cwd>"):
            return stripped.removeprefix("<cwd>").removesuffix("</cwd>")
    return None


def _git_value(cwd: str, *arguments: str) -> str | None:
    executable = which("git")
    if executable is None:
        return None
    try:
        completed = subprocess.run(  # noqa: S603 - git executable is discovered and arguments are fixed internally.
            [executable, *arguments],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or None


def _string(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _int(value: object, *, default: int) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else default


__all__ = [
    "SUPPORTED_SESSION_IMPORT_HOSTS",
    "CodexPromptReadResult",
    "ImportedPrompt",
    "SessionImportError",
    "SessionImportItemResult",
    "SessionImportItemStatus",
    "SessionImportResult",
    "import_sessions",
    "read_codex_prompts",
]
