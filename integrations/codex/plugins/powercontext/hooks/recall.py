#!/usr/bin/env python3
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

"""Map codex prompt events to the installed client's shared hook."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from powercontext.client.integration import prompt as prompt_operations
from powercontext.client.integration.diagnostics import Events

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLUGIN_ROOT))
sys.path.insert(0, str(_PLUGIN_ROOT / "hooks"))
sys.path.insert(0, str(_PLUGIN_ROOT / "scripts"))

from scope_binding import resolve_scope_id  # noqa: E402
from scope_context import scope_context  # noqa: E402
from settings import CodexPluginSettings  # noqa: E402


def main(settings: CodexPluginSettings | None = None) -> int:
    """Run one native event without blocking the host on an integration failure."""

    try:
        settings = CodexPluginSettings() if settings is None else settings
        payload = prompt_operations.read_payload()
        if not prompt_operations.is_prompt_event(payload.get("hook_event_name")):
            return 0
        events = Events("codex")
        query, cwd = payload.get("prompt"), payload.get("cwd")
        if not isinstance(query, str) or not query.strip() or not isinstance(cwd, str):
            events.emit("skipped")
            events.write(include_empty=False)
            return 0
        deadline = prompt_operations.deadline(settings)
        session_id = prompt_operations.identifier(payload, "session_id", "conversation_id", "thread_id")
        scope_id = resolve_scope_id(cwd, session_id=session_id, settings=settings, deadline=deadline)
        context = prompt_operations.run(
            "codex",
            query,
            cwd,
            scope_id,
            session_id,
            prompt_operations.identifier(payload, "turn_id", "request_id"),
            event_id_field="turn_id",
            settings=settings,
            deadline=deadline,
            events=events,
        )
        context = scope_context(context, scope_id)
        events.write(context, include_empty=False)
        if context:
            with suppress(Exception):
                _record_evaluation_trace(payload, query=query, injected_text=context, scope_id=scope_id)
    except Exception:
        return 0
    return 0


def _record_evaluation_trace(
    payload: Mapping[str, object],
    *,
    query: str,
    injected_text: str,
    scope_id: str,
) -> None:
    """Append the exact injected context when the isolated evaluator requests an audit trace."""

    raw_path = os.environ.get("POWERCONTEXT_EVAL_TRACE_PATH")
    if raw_path is None or not raw_path.strip():
        eval_home = os.environ.get("POWERCONTEXT_HOME")
        if not scope_id.startswith("eval:") or eval_home is None or not eval_home.strip():
            return
        home = Path(eval_home)
        if not home.is_absolute():
            return
        raw_path = os.fspath(home / "evaluation-injections.jsonl")
    event: dict[str, object] = {
        "event_type": "powercontext_injection",
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "query": query,
        "injected_text": injected_text,
        # The prepared-context v1 response deliberately does not expose raw search hits.
        "hits": [],
        "scope_id": scope_id,
    }
    session_id = prompt_operations.identifier(payload, "session_id", "conversation_id", "thread_id")
    turn_id = prompt_operations.identifier(payload, "turn_id", "request_id")
    if session_id is not None:
        event["session_id"] = session_id
    if turn_id is not None:
        event["turn_id"] = turn_id
    encoded = (json.dumps(event, separators=(",", ":")) + "\n").encode()
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(raw_path, flags, 0o600)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "ab", closefd=False) as trace:
            trace.write(encoded)
            trace.flush()
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    raise SystemExit(main())
