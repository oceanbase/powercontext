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

"""Translate Antigravity lifecycle events to the shared PowerContext flows."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from contextlib import closing
from hashlib import sha256
from pathlib import Path
from time import monotonic
from typing import Any
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from powercontext_lifecycle.bootstrap_state import BootstrapState  # noqa: E402
from powercontext_lifecycle.client_config import (  # noqa: E402
    normalize_server_url,
    parse_boolean,
    resolve_allow_insecure_http,
)
from powercontext_lifecycle.diagnostics import DiagnosticThrottle  # noqa: E402
from powercontext_lifecycle.host_profile import HostProfile  # noqa: E402
from powercontext_lifecycle.scope_binding import ScopeBindingSettings, read_error_code, resolve_scope_id  # noqa: E402
from powercontext_lifecycle.session_start import BootstrapConnection, SessionStartEvent, run_session_start  # noqa: E402
from powercontext_lifecycle.user_prompt_submit import (  # noqa: E402
    PromptSettings,
    UserPromptEvent,
    run_user_prompt_submit,
)

HOST = HostProfile(
    integration="antigravity",
    user_agent="powercontext-antigravity-plugin/0.1.0",
    component="powercontext.antigravity",
    prompt_id_field="prompt_id",
    diagnostics=DiagnosticThrottle(state_file="antigravity-diagnostics.json"),
    bootstrap_state=BootstrapState(
        directory_variable="POWERCONTEXT_ANTIGRAVITY_STATE_DIR",
        plugin_data_variable="PLUGIN_DATA",
    ),
)
HTTP_BUDGET = 5.0
STATE_ENV = "POWERCONTEXT_ANTIGRAVITY_STATE_DIR"


def _scope_settings() -> ScopeBindingSettings:
    entry = json.loads((ROOT / "mcp_config.json").read_text(encoding="utf-8"))["mcpServers"]["memory"]
    url = entry["url"]
    allowed = resolve_allow_insecure_http(
        url, host=HOST.integration, host_environment="POWERCONTEXT_ANTIGRAVITY_ALLOW_INSECURE_HTTP"
    )
    authorization = entry.get("headers", {}).get("Authorization")
    return ScopeBindingSettings(normalize_server_url(url, allow_insecure_http=allowed), authorization, None, 2.0)


def _connect(event: SessionStartEvent) -> BootstrapConnection:
    settings = _scope_settings()
    return BootstrapConnection(
        server_url=settings.server_url,
        authorization=settings.authorization,
        request_timeout_seconds=settings.request_timeout_seconds,
        http_budget_seconds=HTTP_BUDGET,
        enabled=True,
        max_bytes=8192,
        handoff=None,
        resolve_scope_id=lambda deadline: resolve_scope_id(
            HOST, event.cwd, session_id=event.session_id, settings=settings, deadline=deadline, persist_session=True
        ),
        read_error_code=read_error_code,
    )


def _prompt_state_path() -> Path:
    root = Path(os.environ[STATE_ENV])
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root / "prompts.sqlite"


def _claim_prompt(conversation: str, prompt: str) -> str | None:
    """Claim the latest prompt atomically and return a token for that specific claim."""
    conversation_key = sha256(conversation.encode()).hexdigest()
    prompt_key = sha256(prompt.encode()).hexdigest()
    # The first 64 characters stay compatible with rows from older installs.
    claim = f"{prompt_key}:{uuid4().hex}"
    with closing(sqlite3.connect(_prompt_state_path(), timeout=1.0)) as database, database:
        database.execute("CREATE TABLE IF NOT EXISTS prompts (conversation TEXT PRIMARY KEY, digest TEXT NOT NULL)")
        changed = database.execute(
            "INSERT INTO prompts VALUES (?, ?) ON CONFLICT(conversation) DO UPDATE SET digest = excluded.digest "
            "WHERE substr(prompts.digest, 1, 64) != ?",
            (conversation_key, claim, prompt_key),
        ).rowcount
    return claim if changed else None


def _release_prompt(conversation: str, claim: str) -> None:
    # A late failure cannot delete a newer claim, even if its prompt text matches.
    conversation_key = sha256(conversation.encode()).hexdigest()
    with closing(sqlite3.connect(_prompt_state_path(), timeout=1.0)) as database, database:
        database.execute("DELETE FROM prompts WHERE conversation = ? AND digest = ?", (conversation_key, claim))


def _recall(payload: dict[str, Any], cwd: str, conversation: str) -> str | None:
    prompt = payload.get("lastUserInput")
    if not isinstance(prompt, str) or not prompt.strip():
        return None
    config = ROOT / "powercontext.json"
    capture = config.exists() and json.loads(config.read_text(encoding="utf-8")).get("capture_prompts") is True
    recall = parse_boolean(os.environ.get("POWERCONTEXT_ANTIGRAVITY_PROMPT_RECALL", "true"))
    if not (capture or recall):
        return None
    settings = PromptSettings(
        scope_binding=_scope_settings(),
        include_code=False,
        context_assembly=None,
        prompt_recall=recall,
        capture_prompts=capture,
        flush_on_capture=False,
        flush_max_calls=0,
    )
    claim = _claim_prompt(conversation, prompt)
    if claim is None:
        return None
    invocation = payload.get("invocationNum")
    prompt_id = str(invocation) if type(invocation) is int else None
    submitted = run_user_prompt_submit(
        HOST, UserPromptEvent(prompt, cwd, conversation, prompt_id), settings, deadline=monotonic() + HTTP_BUDGET
    )
    if submitted is None:
        return None
    if submitted.scope_id is None:
        # Scope resolution is the only known failure before recall and capture.
        _release_prompt(conversation, claim)
    for diagnostic in submitted.diagnostics:
        print(json.dumps(diagnostic), file=sys.stderr)
    return submitted.context


def main(event_name: str) -> None:
    """Read one native event and emit a single Antigravity response."""
    emitted = False

    def inject(content: str) -> None:
        nonlocal emitted
        emitted = True
        print(json.dumps({"injectSteps": [{"systemMessage": {"systemMessage": content}}]}), flush=True)

    try:
        state = os.environ.get(STATE_ENV) or os.environ.get("PLUGIN_DATA")
        os.environ[STATE_ENV] = (
            str(Path(state).expanduser()) if state else str(Path.home() / ".gemini/config/powercontext")
        )
        payload = json.load(sys.stdin)
        conversation = payload.get("conversationId")
        paths = payload.get("workspacePaths")
        cwd = payload.get("cwd")
        if not isinstance(cwd, str) or not Path(cwd).is_absolute():
            cwd = (
                next((path for path in paths if isinstance(path, str) and Path(path).is_absolute()), None)
                if isinstance(paths, list)
                else None
            )
        if not isinstance(conversation, str) or not 0 < len(conversation.strip()) <= 256 or cwd is None:
            return
        conversation = conversation.strip()
        if event_name == "SessionStart":
            event = SessionStartEvent(conversation, cwd, "startup", None)
            run_session_start(HOST, event, connect=lambda: _connect(event), emit=inject)
        elif event_name == "PreInvocation" and (context := _recall(payload, cwd, conversation)):
            inject(context)
    except Exception:  # noqa: S110 - errors may include prompt text or credentials.
        # Like other host adapters, let malformed inputs and local failures leave the host running.
        pass
    finally:
        if not emitted:
            print("{}")


if __name__ == "__main__":
    main(sys.argv[1])
