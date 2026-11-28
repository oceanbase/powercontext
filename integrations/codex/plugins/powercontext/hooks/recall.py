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

"""Recall memory and capture the current Codex prompt without blocking Codex."""

from __future__ import annotations

import json
import os
import sys
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from time import monotonic
from typing import Any, cast

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLUGIN_ROOT))

from codex_host import CODEX  # noqa: E402
from powercontext_lifecycle.user_prompt_submit import (  # noqa: E402
    UserPromptEvent,
    payload_identifier,
    run_user_prompt_submit,
)
from settings import CodexPluginSettings  # noqa: E402


def main(settings: CodexPluginSettings | None = None) -> int:
    """Process one Codex hook payload and fail open."""

    try:
        settings = CodexPluginSettings() if settings is None else settings
        http_deadline = monotonic() + settings.http_budget_seconds
        stdin = sys.stdin
        if hasattr(stdin, "buffer"):
            payload = cast(dict[str, Any], json.loads(stdin.buffer.read().decode("utf-8")))
        else:
            payload = cast(dict[str, Any], json.load(stdin))
        if not _is_user_prompt_submit(payload.get("hook_event_name")):
            return 0
        prompt = payload.get("prompt")
        cwd = payload.get("cwd")
        event = UserPromptEvent(
            prompt=prompt if isinstance(prompt, str) else None,
            cwd=cwd if isinstance(cwd, str) else None,
            session_id=payload_identifier(payload, "session_id", "conversation_id", "thread_id"),
            prompt_id=payload_identifier(payload, "turn_id", "request_id"),
        )
        submitted = run_user_prompt_submit(CODEX, event, settings.prompt_settings(), deadline=http_deadline)
        if submitted is None:
            return 0
        agent_context = submitted.agent_context
        if agent_context and submitted.scope_id is not None:
            with suppress(Exception):
                _record_evaluation_trace(
                    event,
                    query=submitted.scrubbed_prompt,
                    injected_text=agent_context,
                    scope_id=submitted.scope_id,
                )
        _write_hook_output(context=agent_context, diagnostic_events=submitted.diagnostics)
    except Exception:
        return 0
    return 0


def _is_user_prompt_submit(value: object) -> bool:
    return isinstance(value, str) and value.replace("_", "").lower() == "userpromptsubmit"


def _record_evaluation_trace(
    event: UserPromptEvent,
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
    trace_event: dict[str, object] = {
        "event_type": "powercontext_injection",
        "observed_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "query": query,
        "injected_text": injected_text,
        # The prepared-context v1 response deliberately does not expose raw search hits.
        "hits": [],
        "scope_id": scope_id,
    }
    if event.session_id is not None:
        trace_event["session_id"] = event.session_id
    if event.prompt_id is not None:
        trace_event["turn_id"] = event.prompt_id
    encoded = (json.dumps(trace_event, separators=(",", ":")) + "\n").encode()
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


def _write_hook_output(
    *,
    context: str | None = None,
    diagnostic_events: list[dict[str, object]],
) -> None:
    output: dict[str, object] = {}
    if diagnostic_events:
        output["systemMessage"] = "\n".join(json.dumps(event, separators=(",", ":")) for event in diagnostic_events)
    if context:
        output["hookSpecificOutput"] = {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    if output:
        json.dump(output, sys.stdout, separators=(",", ":"))
        sys.stdout.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
