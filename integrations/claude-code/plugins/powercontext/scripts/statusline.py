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

"""Render scoped PowerContext recall-token estimates for Claude Code."""

from __future__ import annotations

import argparse
import json
import os
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.error import HTTPError

from powercontext.client.integration.native import scope_request as _request_stats

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS_ROOT = _PLUGIN_ROOT / "scripts"
sys.path.insert(0, str(_PLUGIN_ROOT))
sys.path.insert(0, str(_SCRIPTS_ROOT))

from claude_code_settings import ClaudeCodePluginSettings  # noqa: E402
from workspace_scope import resolve_scope_id  # noqa: E402

from powercontext.client.integration.native import (  # noqa: E402
    ScopeBindingRejectedError,
    ScopeBindingStatusError,
    ScopeBindingUnavailableError,
)

_GREEN = "\033[32m"
_RED = "\033[31m"
_MUTED = "\033[90m"
_RESET = "\033[0m"
_FAILURE_LINES = {
    "authentication_failed": "PC auth failed",
    "version_mismatch": "PC version mismatch",
    "server_unavailable": "PC offline · run powercontext doctor",
    "invalid_response": "PC invalid response",
}


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
    non_negative = (
        "preparations",
        "ready_preparations",
        "comparable_preparations",
        "baseline_tokens",
        "recalled_tokens",
    )
    for name in non_negative:
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


def format_statusline(today: object, month: object, *, color: bool = True) -> str:
    """Render the same two-window compression proxy used by OpenCode."""

    today_reduction = token_reduction(today)
    month_reduction = token_reduction(month)
    if not color:
        return f"● PC online · {savings_phrase(today_reduction, 'today')} · {savings_phrase(month_reduction, 'in 30d')}"
    return "".join((
        _GREEN,
        "●",
        _RESET,
        _MUTED,
        " PC online · ",
        _RESET,
        _colored_savings(today_reduction, "today"),
        _MUTED,
        " · ",
        _RESET,
        _colored_savings(month_reduction, "in 30d"),
    ))


def _colored_savings(reduction: int | None, suffix: str) -> str:
    color = _MUTED if reduction is None or reduction == 0 else (_GREEN if reduction > 0 else _RED)
    return f"{color}{savings_phrase(reduction, suffix)}{_RESET}"


def _load_stats(settings: ClaudeCodePluginSettings, scope_id: str, period: str, *, deadline: float) -> object:
    result = _request_stats(
        "/v1/stats",
        {"selection": {"mode": "exact", "scope_ids": [scope_id]}, "period": period},
        settings=settings,
        deadline=deadline,
    )
    if token_reduction(result) is None:
        raise TypeError
    return result


def _statusline_input() -> dict[str, Any]:
    value = json.load(sys.stdin)
    if not isinstance(value, dict):
        raise TypeError("Claude Code statusline input must be an object")  # noqa: TRY003
    return value


def _working_directory(value: dict[str, Any]) -> str | None:
    workspace = value.get("workspace")
    if isinstance(workspace, dict):
        current = workspace.get("current_dir")
        if isinstance(current, str) and current.strip():
            return current
    cwd = value.get("cwd")
    return cwd if isinstance(cwd, str) and cwd.strip() else None


def failure_outcome(error: BaseException) -> str:
    """Classify one statusline failure into the documented Plugin outcomes."""

    if isinstance(error, ScopeBindingRejectedError):
        return "authentication_failed"
    if isinstance(error, ScopeBindingUnavailableError):
        return "server_unavailable"
    if isinstance(error, ScopeBindingStatusError):
        return (
            "version_mismatch"
            if error.status == 404 and error.path == "/v1/scope-bindings/resolve"
            else "invalid_response"
        )
    if isinstance(error, HTTPError):
        if error.code == 401:
            return "authentication_failed"
        if error.code == 503:
            return "server_unavailable"
        return "invalid_response"
    if isinstance(error, (OSError, TimeoutError)):
        return "server_unavailable"
    return "invalid_response"


def render(server_url: str) -> str:
    """Load both bounded statistics windows and return one fail-open classified line."""

    try:
        value = _statusline_input()
        cwd = _working_directory(value)
        settings = ClaudeCodePluginSettings.from_environment(server_url=server_url)
        if cwd is None and settings.scope_id is None:
            return f"{_RED}●{_RESET}{_MUTED} PC unavailable{_RESET}"
        session_id = value.get("session_id")
        http_deadline = monotonic() + settings.http_budget_seconds
        scope_id = resolve_scope_id(
            cwd or os.getcwd(),
            session_id=session_id if isinstance(session_id, str) else None,
            settings=settings,
            deadline=http_deadline,
        )
        today = _load_stats(settings, scope_id, "today", deadline=http_deadline)
        month = _load_stats(settings, scope_id, "30d", deadline=http_deadline)
        return format_statusline(today, month)
    except Exception as error:
        return f"{_RED}●{_RESET}{_MUTED} {_FAILURE_LINES[failure_outcome(error)]}{_RESET}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-url", default="http://127.0.0.1:8000")
    arguments = parser.parse_args()
    print(render(arguments.server_url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
