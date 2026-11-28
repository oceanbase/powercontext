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

"""Thin Antigravity lifecycle shell delegating behavior to powercontext_lifecycle."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from time import monotonic
from typing import Any

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

from antigravity_host import ANTIGRAVITY, debug_log, resolve_cwd, resolve_session_id  # noqa: E402
from powercontext_lifecycle.scope_binding import read_error_code, resolve_scope_id  # noqa: E402
from powercontext_lifecycle.session_start import (  # noqa: E402
    BootstrapConnection,
    SessionStartEvent,
    hook_event_id,
    run_session_start,
)
from powercontext_lifecycle.user_prompt_submit import UserPromptEvent, run_user_prompt_submit  # noqa: E402
from settings import AntigravityPluginSettings, host_bounded_http_budget  # noqa: E402

_USER_REQUEST_RE = re.compile(r"^\s*<USER_REQUEST>\s*([\s\S]*?)\s*</USER_REQUEST>")
_INJECTED_RE = re.compile(r"<(?:powercontext|openviking)-context\b[^>]*>[\s\S]*?</(?:powercontext|openviking)-context>")


def _state_dir() -> Path:
    plugin_data = os.environ.get("PLUGIN_DATA")
    if plugin_data and plugin_data.strip():
        return Path(plugin_data).expanduser() / "state"
    return Path.home() / ".config" / "powercontext" / "state" / "antigravity"


def _clean_text(text: object) -> str:
    return _INJECTED_RE.sub("", str(text or "")).strip()


def _unwrap_user_request(text: object) -> str:
    raw = str(text or "")
    matched = _USER_REQUEST_RE.match(raw)
    return _clean_text(matched.group(1) if matched else raw)


def _same_text(text: object) -> str:
    return " ".join(_unwrap_user_request(text).split())


def _transcript_path(raw_path: object) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    path = Path(raw_path)
    if path.name == "transcript.jsonl":
        full = path.with_name("transcript_full.jsonl")
        if full.exists():
            return full
    return path if path.exists() else None


def _load_state(session_id: str) -> dict[str, Any]:
    path = _state_dir() / f"{session_id}.json"
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return {}


def _save_state(session_id: str, state: dict[str, Any]) -> None:
    state_dir = _state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        with contextlib.suppress(OSError):
            os.chmod(state_dir, 0o700)
    target = state_dir / f"{session_id}.json"
    fd, tmp_name = tempfile.mkstemp(prefix=f".{session_id}.", dir=state_dir)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(state, ensure_ascii=False))
        if os.name != "nt":
            with contextlib.suppress(OSError):
                os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, target)
    finally:
        tmp_path.unlink(missing_ok=True)


def _iter_transcript_steps(jsonl: str, *, limit: int | None = None) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for line in jsonl.splitlines():
        if not line.strip():
            continue
        try:
            step = json.loads(line)
        except Exception:
            break
        index = step.get("step_index")
        if not isinstance(index, int) or index < 0:
            continue
        if limit is not None and index >= limit:
            break
        steps.append(step)
    return steps


def _resolve_turn_step(jsonl: str, initial_num_steps: int | None, last_user_input: str) -> int | None:
    """Return step_index + 1 for an unanswered USER_INPUT step, None if model already ran, or -1 if unseen."""

    limit = initial_num_steps if isinstance(initial_num_steps, int) and initial_num_steps > 0 else None
    steps = _iter_transcript_steps(jsonl, limit=limit)
    if not steps:
        return -1

    last_user_step = -1
    last_user_text: str | None = ""
    model_ran = False
    for step in steps:
        if step.get("type") == "USER_INPUT" and step.get("source") == "USER_EXPLICIT":
            last_user_step = step["step_index"]
            truncated = step.get("truncated_fields")
            last_user_text = (
                None if isinstance(truncated, list) and "content" in truncated else _same_text(step.get("content"))
            )
            model_ran = False
        elif last_user_step >= 0 and step.get("source") == "MODEL":
            model_ran = True

    if last_user_step < 0 or model_ran:
        pending = _same_text(last_user_input)
        return -1 if pending and last_user_text is not None and pending != last_user_text else None
    return last_user_step + 1


def _emit_inject(*blocks: str | None) -> None:
    steps = [{"systemMessage": {"systemMessage": block}} for block in blocks if block]
    with contextlib.suppress(BrokenPipeError):
        if steps:
            sys.stdout.write(json.dumps({"injectSteps": steps}, ensure_ascii=False, separators=(",", ":")) + "\n")
        else:
            sys.stdout.write("{}\n")


def _emit_empty() -> None:
    with contextlib.suppress(BrokenPipeError):
        sys.stdout.write("{}\n")


def _handle_session_start(payload: dict[str, Any], settings: AntigravityPluginSettings) -> None:
    session_id = resolve_session_id(payload)
    cwd = resolve_cwd(payload)
    if session_id is None or cwd is None:
        _emit_empty()
        return
    event = SessionStartEvent(session_id=session_id, cwd=cwd, lifecycle="startup", event_id=hook_event_id(payload))
    captured: list[str] = []

    def _connect() -> BootstrapConnection:
        budget = host_bounded_http_budget(settings.http_budget_seconds)
        return BootstrapConnection(
            server_url=settings.server_url,
            authorization=settings.authorization,
            request_timeout_seconds=settings.request_timeout_seconds,
            http_budget_seconds=budget,
            enabled=settings.bootstrap_context,
            max_bytes=settings.bootstrap_max_bytes,
            handoff=None if settings.bootstrap_handoff is None else settings.bootstrap_handoff.as_request(),
            resolve_scope_id=lambda deadline: resolve_scope_id(
                ANTIGRAVITY,
                cwd,
                session_id=session_id,
                settings=settings.scope_binding_settings(),
                deadline=deadline,
                persist_session=True,
            ),
            read_error_code=read_error_code,
        )

    run_session_start(ANTIGRAVITY, event, connect=_connect, emit=captured.append)
    msg = f'<powercontext-context source="session-start">\n{captured[0]}\n</powercontext-context>' if captured else None
    debug_log("session-start", "done", session_id=session_id, cwd=cwd, injected=bool(captured))
    _emit_inject(msg)


def _handle_pre_invocation(payload: dict[str, Any], settings: AntigravityPluginSettings) -> None:
    invocation_num = payload.get("invocationNum")
    if isinstance(invocation_num, int) and invocation_num > 0:
        _emit_empty()
        return

    session_id = resolve_session_id(payload)
    cwd = resolve_cwd(payload)
    if session_id is None or cwd is None:
        _emit_empty()
        return

    last_user_input = _unwrap_user_request(payload.get("lastUserInput"))
    if not last_user_input:
        _emit_empty()
        return

    tpath = _transcript_path(payload.get("transcriptPath"))
    turn_step: int | None = -1
    if tpath is not None:
        turn_step = _resolve_turn_step(
            tpath.read_text(encoding="utf-8", errors="replace"),
            payload.get("initialNumSteps"),
            last_user_input,
        )
    if turn_step is None:
        _emit_empty()
        return

    state = _load_state(session_id)
    prompt_hash = hashlib.sha256(_same_text(last_user_input).encode("utf-8")).hexdigest()[:16]
    if turn_step > 0:
        if state.get("last_recalled_step") == turn_step:
            _emit_empty()
            return
        prompt_id = f"{session_id}:{turn_step}"
    else:
        if state.get("last_unindexed_hash") == prompt_hash:
            _emit_empty()
            return
        prompt_id = f"{session_id}:unindexed:{prompt_hash}"

    event = UserPromptEvent(
        prompt=last_user_input,
        cwd=cwd,
        session_id=session_id,
        prompt_id=prompt_id,
    )
    budget = host_bounded_http_budget(settings.http_budget_seconds)
    submitted = run_user_prompt_submit(
        ANTIGRAVITY,
        event,
        settings.prompt_settings(),
        deadline=monotonic() + budget,
    )
    if turn_step > 0:
        state["last_recalled_step"] = turn_step
        state.pop("last_unindexed_hash", None)
    else:
        state["last_unindexed_hash"] = prompt_hash
    _save_state(session_id, state)

    ctx = submitted.context if submitted and submitted.context else None
    diag_msg = (
        "\n".join(json.dumps(item, ensure_ascii=False, separators=(",", ":")) for item in submitted.diagnostics)
        if submitted and submitted.diagnostics
        else None
    )
    ctx_msg = f'<powercontext-context source="pre-invocation">\n{ctx}\n</powercontext-context>' if ctx else None
    debug_log(
        "pre-invocation",
        "done",
        session_id=session_id,
        cwd=cwd,
        scope_id=submitted.scope_id if submitted else None,
        injected=bool(ctx),
        diagnostics=submitted.diagnostics if submitted else [],
    )
    _emit_inject(diag_msg, ctx_msg)


def main() -> int:
    event_name = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
        settings = AntigravityPluginSettings.from_environment()
        if event_name == "session-start":
            _handle_session_start(payload, settings)
        elif event_name == "pre-invocation":
            _handle_pre_invocation(payload, settings)
        else:
            _emit_empty()
    except Exception as exc:
        debug_log(event_name or "unknown", "error", error=str(exc))
        _emit_empty()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
