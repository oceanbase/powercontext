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

"""Map claude-code prompt events to the installed client's shared hook."""

from __future__ import annotations

import sys
from pathlib import Path

from powercontext.client.integration import prompt as prompt_operations
from powercontext.client.integration.diagnostics import Events

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLUGIN_ROOT))
sys.path.insert(0, str(_PLUGIN_ROOT / "hooks"))
sys.path.insert(0, str(_PLUGIN_ROOT / "scripts"))

from claude_code_settings import ClaudeCodePluginSettings  # noqa: E402
from scope_context import scope_context  # noqa: E402
from workspace_scope import resolve_scope_id  # noqa: E402


def main(settings: ClaudeCodePluginSettings | None = None) -> int:
    """Run one native event without blocking the host on an integration failure."""

    try:
        settings = ClaudeCodePluginSettings.from_environment() if settings is None else settings
        payload = prompt_operations.read_payload()
        if not prompt_operations.is_prompt_event(payload.get("hook_event_name")):
            return 0
        events = Events("claude-code")
        query, cwd = payload.get("prompt"), payload.get("cwd")
        if not isinstance(query, str):
            query = payload.get("user_prompt")
        if not isinstance(query, str) or not query.strip() or not isinstance(cwd, str):
            events.emit("skipped")
            events.write(include_empty=False)
            return 0
        deadline = prompt_operations.deadline(settings)
        session_id = prompt_operations.identifier(payload, "session_id")
        scope_id = resolve_scope_id(cwd, session_id=session_id, settings=settings, deadline=deadline)
        context = prompt_operations.run(
            "claude-code",
            query,
            cwd,
            scope_id,
            session_id,
            prompt_operations.identifier(payload, "prompt_id", "request_id"),
            event_id_field="prompt_id",
            settings=settings,
            deadline=deadline,
            events=events,
        )
        events.write(scope_context(context, scope_id), include_empty=False)
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
