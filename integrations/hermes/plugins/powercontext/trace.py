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

"""Evaluation-trace persistence for Hermes PowerContext sessions."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .helpers import SCOPE_SAFE_RE

logger = logging.getLogger(__name__)

MAX_TRACE_EVENTS = 1000
MAX_TRACE_OUTPUT_CHARS = 200_000


def session_path(provider: Any, session_id: str) -> Path | None:
    if provider._trace_dir is None or not session_id.strip():
        return None
    safe_name = SCOPE_SAFE_RE.sub("_", session_id.strip()).strip("_")[:160] or "session"
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]
    return provider._trace_dir / "sessions" / f"{safe_name}-{digest}.jsonl"


def index_path(provider: Any) -> Path | None:
    return provider._trace_dir / "index.jsonl" if provider._trace_dir is not None else None


def timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def append_line(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "ab") as trace_file:
            trace_file.write(encoded)
            trace_file.flush()
    except Exception:
        with suppress(OSError):
            os.close(descriptor)
        raise


def record_event(provider: Any, event_type: str, *, session_id: str | None = None, **fields: Any) -> None:
    if not provider._trace_enabled:
        return
    effective_session = (session_id or provider._session_id).strip()
    trace_path = session_path(provider, effective_session)
    trace_index_path = index_path(provider)
    if trace_path is None or trace_index_path is None:
        return

    with provider._trace_lock:
        provider._trace_turn += 1
        event = {
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "observed_at": timestamp(),
            "session_id": effective_session,
            "parent_session_id": provider._parent_session_id or None,
            "profile": provider._profile,
            "scope_id": provider._scope_id,
            "turn_id": provider._trace_turn,
            **fields,
        }
        try:
            append_line(trace_path, event)
            if event_type in {"session_start", "session_switch"}:
                append_line(
                    trace_index_path,
                    {
                        "event_id": event["event_id"],
                        "event_type": event_type,
                        "observed_at": event["observed_at"],
                        "session_id": effective_session,
                        "parent_session_id": provider._parent_session_id or None,
                        "profile": event["profile"],
                        "scope_id": provider._scope_id,
                    },
                )
        except OSError:
            logger.debug("Could not write PowerContext evaluation trace", exc_info=True)


def events(provider: Any, session_id: str) -> list[dict[str, Any]]:
    path = session_path(provider, session_id)
    if path is None:
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-MAX_TRACE_EVENTS:]
    except (OSError, UnicodeDecodeError):
        return []
    result: list[dict[str, Any]] = []
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            result.append(value)
    return result


def sessions(provider: Any) -> list[dict[str, Any]]:
    path = index_path(provider)
    if path is None:
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    result: dict[str, dict[str, Any]] = {}
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and str(value.get("session_id", "")).strip():
            result[str(value["session_id"])] = value
    return list(result.values())


def clear_session(provider: Any, session_id: str) -> None:
    trace_path = session_path(provider, session_id)
    trace_index_path = index_path(provider)
    if trace_path is None or trace_index_path is None:
        return
    with provider._trace_lock:
        with suppress(OSError):
            trace_path.unlink()
        try:
            lines = trace_index_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return
        kept: list[str] = []
        for line in lines:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                kept.append(line)
                continue
            if not isinstance(value, dict) or str(value.get("session_id", "")) != session_id:
                kept.append(line)
        temporary_path = trace_index_path.with_name(f".{trace_index_path.name}.tmp")
        try:
            temporary_path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
            os.replace(temporary_path, trace_index_path)
        except OSError:
            logger.debug("Could not update PowerContext evaluation trace index", exc_info=True)
            temporary_path.unlink(missing_ok=True)


def command(provider: Any, args: list[str]) -> str:
    action = args[0].lower() if args else "status"
    if action == "status":
        result = {
            "enabled": provider._trace_enabled,
            "session_id": provider._session_id,
            "parent_session_id": provider._parent_session_id or None,
            "trace_dir": str(provider._trace_dir) if provider._trace_dir else None,
            "event_count": len(events(provider, provider._session_id)),
        }
        return json.dumps(result, ensure_ascii=False, indent=2)
    if action == "enable":
        provider._trace_enabled = True
        provider._record_trace_event("trace_enabled")
        return "PowerContext evaluation trace enabled for the current Hermes process."
    if action == "disable":
        provider._record_trace_event("trace_disabled")
        provider._trace_enabled = False
        return "PowerContext evaluation trace disabled for the current Hermes process."
    if action in {"sessions", "list"}:
        return json.dumps(sessions(provider), ensure_ascii=False, indent=2)
    if action == "show":
        session_id = provider._session_id
        if len(args) >= 3 and args[1] == "--session":
            session_id = args[2]
        trace_events = events(provider, session_id)
        while len(trace_events) > 1 and len(json.dumps(trace_events, ensure_ascii=False)) > MAX_TRACE_OUTPUT_CHARS:
            trace_events.pop(0)
        return json.dumps(trace_events, ensure_ascii=False, indent=2)
    if action == "clear":
        session_id = provider._session_id
        if len(args) >= 3 and args[1] == "--session":
            session_id = args[2]
        clear_session(provider, session_id)
        return f"Cleared evaluation trace for session {session_id}."
    return "Usage: /pc trace {status|enable|disable|sessions|show [--session ID]|clear [--session ID]}"
