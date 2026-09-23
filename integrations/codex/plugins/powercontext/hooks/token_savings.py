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

"""Show scoped recall-token estimates after a Codex turn with bounded latency."""

from __future__ import annotations

import json
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from time import monotonic
from typing import Any

from powercontext.client.integration.diagnostics import should_emit as _should_emit_diagnostic
from powercontext.client.integration.native import (
    HttpStatusError as _HttpStatusError,
)
from powercontext.client.integration.native import (
    UnavailableError as _ServerUnavailableError,
)
from powercontext.client.integration.native import request_json as _request_stats

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS_ROOT = _PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(_PLUGIN_ROOT))
sys.path.insert(0, str(_SCRIPTS_ROOT))

from scope_binding import (  # noqa: E402
    ScopeBindingRejectedError,
    ScopeBindingStatusError,
    ScopeBindingUnavailableError,
    resolve_scope_id,
)
from settings import CodexPluginSettings  # noqa: E402

_COMPONENT = "powercontext.codex.token_savings"
# The Stop entry has a 10-second host deadline (hooks.json). Keep
# the internal HTTP budget below it so client start-up and the bounded output
# fit inside the host limit instead of being killed mid-request.
_HOST_TIMEOUT_SECONDS = 10.0
_STARTUP_AND_OUTPUT_MARGIN_SECONDS = 2.0


def stop_http_budget(configured_seconds: float) -> float:
    """Bound the Stop hook's HTTP budget below the host process deadline."""

    return min(configured_seconds, _HOST_TIMEOUT_SECONDS - _STARTUP_AND_OUTPUT_MARGIN_SECONDS)


def compact_tokens(value: int) -> str:
    """Format an integer using the compact OpenCode statusline convention."""

    absolute = abs(value)
    if absolute >= 1_000_000:
        return _scaled(value / 1_000_000, "m")
    if absolute >= 1_000:
        return _scaled(value / 1_000, "k")
    return str(value)


def _scaled(value: float, suffix: str) -> str:
    digits = 1 if abs(value) < 10 else 0
    quantum = Decimal(1).scaleb(-digits)
    rounded = Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)
    return f"{rounded:.{digits}f}".removesuffix(".0") + suffix


def token_reduction(value: object) -> int | None:
    """Return a validated signed reduction from one scoped-statistics response."""

    if not isinstance(value, dict):
        return None
    recall = value.get("recall")
    if not isinstance(recall, dict):
        return None
    totals = recall.get("totals")
    if not isinstance(totals, dict):
        return None
    for name in (
        "preparations",
        "ready_preparations",
        "comparable_preparations",
        "baseline_tokens",
        "recalled_tokens",
    ):
        field = totals.get(name)
        if not isinstance(field, int) or isinstance(field, bool) or field < 0:
            return None
    reduction = totals.get("token_reduction")
    if not isinstance(reduction, int) or isinstance(reduction, bool):
        return None
    return reduction


def savings_phrase(reduction: int | None, suffix: str) -> str:
    if reduction is None:
        return f"no data {suffix}"
    amount = compact_tokens(abs(reduction))
    return f"{'saved' if reduction >= 0 else 'cost'} {amount} {suffix}"


def format_message(today: object, month: object) -> str:
    """Render the same two-window compression proxy used by OpenCode."""

    return (
        f"PowerContext · {savings_phrase(token_reduction(today), 'today')}"
        f" · {savings_phrase(token_reduction(month), 'in 30d')}"
    )


def _load_stats(settings: CodexPluginSettings, scope_id: str, period: str, *, deadline: float) -> object:
    result = _request_stats(
        "/v1/stats",
        {"selection": {"mode": "exact", "scope_ids": [scope_id]}, "period": period},
        settings=settings,
        deadline=deadline,
    )
    if token_reduction(result) is None:
        raise TypeError
    return result


def _payload() -> dict[str, Any]:
    stdin = sys.stdin
    value = json.loads(stdin.buffer.read().decode("utf-8")) if hasattr(stdin, "buffer") else json.load(stdin)
    if not isinstance(value, dict):
        raise TypeError
    return value


def _emit_failure(error: BaseException, *, emitted: set[str], events: list[dict[str, object]]) -> None:
    """Classify one failure into the Plugin-visible diagnostic contract."""

    if isinstance(error, _HttpStatusError):
        outcome = (
            "authentication_failed"
            if error.status == 401
            else ("server_unavailable" if error.status == 503 else "invalid_response")
        )
        _append_event(
            outcome,
            {"http_status": error.status, "error_code": error.code},
            recovery="powercontext doctor" if outcome == "server_unavailable" else None,
            emitted=emitted,
            events=events,
        )
        return
    if isinstance(error, ScopeBindingRejectedError):
        _append_event("authentication_failed", {}, recovery=None, emitted=emitted, events=events)
        return
    if isinstance(error, ScopeBindingStatusError):
        if error.status == 503:
            _append_event(
                "server_unavailable",
                {"http_status": error.status},
                recovery="powercontext doctor",
                emitted=emitted,
                events=events,
            )
        elif error.status == 404 and error.path == "/v1/scope-bindings/resolve":
            _append_event(
                "version_mismatch", {"http_status": error.status}, recovery=None, emitted=emitted, events=events
            )
        else:
            _append_event(
                "invalid_response", {"http_status": error.status}, recovery=None, emitted=emitted, events=events
            )
        return
    if isinstance(error, (ScopeBindingUnavailableError, _ServerUnavailableError, TimeoutError)):
        _append_event(
            "server_unavailable",
            {},
            recovery="powercontext doctor",
            emitted=emitted,
            events=events,
        )
        return
    _append_event("invalid_response", {}, recovery=None, emitted=emitted, events=events)


def _append_event(
    outcome: str,
    fields: dict[str, object],
    *,
    recovery: str | None,
    emitted: set[str],
    events: list[dict[str, object]],
) -> None:
    if outcome in emitted:
        return
    emitted.add(outcome)
    if not _should_emit_diagnostic("codex", outcome):
        return
    event: dict[str, object] = {"component": _COMPONENT, "event": "status", "outcome": outcome}
    event.update(fields)
    if recovery is not None:
        event["recovery"] = recovery
    events.append(event)


def _write_hook_output(*, message: str | None = None, events: list[dict[str, object]]) -> None:
    output: dict[str, object] = {"continue": True}
    if events:
        output["systemMessage"] = "\n".join(json.dumps(event, separators=(",", ":")) for event in events)
    elif message is not None:
        output["systemMessage"] = message
    else:
        return
    json.dump(output, sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")


def main(settings: CodexPluginSettings | None = None) -> int:
    """Process one Codex Stop payload and fail open with classified diagnostics."""

    try:
        payload = _payload()
        if payload.get("hook_event_name") != "Stop":
            return 0
        cwd = payload.get("cwd")
        if not isinstance(cwd, str) or not cwd.strip():
            return 0
        settings = CodexPluginSettings() if settings is None else settings
        http_deadline = monotonic() + stop_http_budget(settings.http_budget_seconds)
        session_id = next(
            (
                value
                for name in ("session_id", "conversation_id", "thread_id")
                if isinstance((value := payload.get(name)), str) and value.strip()
            ),
            None,
        )
        emitted: set[str] = set()
        events: list[dict[str, object]] = []
        try:
            scope_id = resolve_scope_id(
                cwd,
                session_id=session_id,
                settings=settings,
                deadline=http_deadline,
            )
            today = _load_stats(settings, scope_id, "today", deadline=http_deadline)
            month = _load_stats(settings, scope_id, "30d", deadline=http_deadline)
        except Exception as error:
            _emit_failure(error, emitted=emitted, events=events)
            _write_hook_output(events=events)
            return 0
        _write_hook_output(message=format_message(today, month), events=[])
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
