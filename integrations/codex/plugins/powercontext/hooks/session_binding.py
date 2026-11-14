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

"""Bind a Codex Session and optionally inject one bounded bootstrap package."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from time import monotonic
from typing import Any, cast

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS_ROOT = _PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(_PLUGIN_ROOT))
sys.path.insert(0, str(_SCRIPTS_ROOT))

from codex_host import CODEX  # noqa: E402
from powercontext_lifecycle.session_start import (  # noqa: E402
    BootstrapConnection,
    SessionStartEvent,
    hook_event_id,
    run_session_start,
)
from scope_binding import read_error_code, resolve_scope_id  # noqa: E402
from settings import CodexPluginSettings  # noqa: E402

_LIFECYCLES = frozenset({"startup", "resume", "clear", "compact", "restore", "fork"})


def main(settings: CodexPluginSettings | None = None) -> int:
    try:
        payload = cast(dict[str, Any], json.load(sys.stdin))
        session_id = payload.get("session_id")
        cwd = payload.get("cwd")
        if not isinstance(session_id, str) or not isinstance(cwd, str):
            return 0
        source = payload.get("source")
        if not isinstance(source, str) or source not in _LIFECYCLES:
            settings = CodexPluginSettings() if settings is None else settings
            resolve_scope_id(
                cwd,
                session_id=session_id,
                settings=settings,
                deadline=monotonic() + settings.http_budget_seconds,
                persist_session=True,
            )
            return 0
        event = SessionStartEvent(session_id=session_id, cwd=cwd, lifecycle=source, event_id=hook_event_id(payload))
        run_session_start(CODEX, event, connect=lambda: _connect(event, settings), emit=_emit_context)
    except Exception:
        return 0
    return 0


def _connect(event: SessionStartEvent, settings: CodexPluginSettings | None) -> BootstrapConnection:
    loaded = CodexPluginSettings() if settings is None else settings
    return BootstrapConnection(
        server_url=loaded.server_url,
        authorization=None if loaded.authorization is None else loaded.authorization.get_secret_value(),
        request_timeout_seconds=loaded.request_timeout_seconds,
        http_budget_seconds=loaded.http_budget_seconds,
        enabled=loaded.bootstrap_context,
        max_bytes=loaded.bootstrap_max_bytes,
        handoff=None if loaded.bootstrap_handoff is None else loaded.bootstrap_handoff.model_dump(),
        resolve_scope_id=lambda deadline: resolve_scope_id(
            event.cwd,
            session_id=event.session_id,
            settings=loaded,
            deadline=deadline,
            persist_session=True,
        ),
        read_error_code=read_error_code,
    )


def _emit_context(content: str) -> None:
    try:
        json.dump(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": content,
                }
            },
            sys.stdout,
            separators=(",", ":"),
        )
        sys.stdout.write("\n")
        sys.stdout.flush()
    except BrokenPipeError:
        # Python flushes stdout again at exit; pointing it at the null device keeps that flush quiet.
        try:
            stdout = sys.stdout.fileno()
            descriptor = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(descriptor, stdout)
            finally:
                os.close(descriptor)
        except (AttributeError, OSError, ValueError):
            pass
        raise


if __name__ == "__main__":
    raise SystemExit(main())
