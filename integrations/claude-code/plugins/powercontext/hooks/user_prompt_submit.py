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

"""Recall memory and capture the current Claude Code prompt without blocking Claude."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from pathlib import Path
from time import monotonic
from typing import Any, cast

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLUGIN_ROOT))

from claude_code_host import CLAUDE_CODE  # noqa: E402
from claude_code_settings import ClaudeCodePluginSettings  # noqa: E402
from powercontext_lifecycle.user_prompt_submit import (  # noqa: E402
    UserPromptEvent,
    payload_identifier,
    run_user_prompt_submit,
)


def main(settings: ClaudeCodePluginSettings | None = None) -> int:
    """Process one Claude Code hook payload and fail open."""

    try:
        settings = ClaudeCodePluginSettings.from_environment() if settings is None else settings
        stdin = sys.stdin
        if hasattr(stdin, "buffer"):
            payload = cast(dict[str, Any], json.loads(stdin.buffer.read().decode("utf-8")))
        else:
            payload = cast(dict[str, Any], json.load(stdin))
        if not _is_user_prompt_submit(payload.get("hook_event_name")):
            return 0
        cwd = payload.get("cwd")
        event = UserPromptEvent(
            prompt=_prompt(payload),
            cwd=cwd if isinstance(cwd, str) else None,
            session_id=payload_identifier(payload, "session_id"),
            prompt_id=payload_identifier(payload, "prompt_id", "request_id"),
        )
        submitted = run_user_prompt_submit(
            CLAUDE_CODE,
            event,
            settings.prompt_settings(),
            deadline=monotonic() + settings.http_budget_seconds,
        )
        if submitted is not None:
            _write_hook_output(context=submitted.agent_context, diagnostic_events=submitted.diagnostics)
    except Exception:
        return 0
    return 0


def _prompt(payload: Mapping[str, object]) -> str | None:
    prompt = payload.get("prompt")
    if isinstance(prompt, str):
        return prompt
    fallback = payload.get("user_prompt")
    return fallback if isinstance(fallback, str) else None


def _is_user_prompt_submit(value: object) -> bool:
    return isinstance(value, str) and value.replace("_", "").lower() == "userpromptsubmit"


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
