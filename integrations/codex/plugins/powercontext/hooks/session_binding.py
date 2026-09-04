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

"""Fix one Codex Session binding without blocking Session startup."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from time import monotonic
from typing import Any, cast

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLUGIN_ROOT))

from scripts.scope_binding import ScopeBindingError, resolve_scope_id  # noqa: E402
from settings import CodexPluginSettings  # noqa: E402


def main(settings: CodexPluginSettings | None = None) -> int:
    try:
        payload = cast(dict[str, Any], json.load(sys.stdin))
        session_id = payload.get("session_id")
        cwd = payload.get("cwd")
        if not isinstance(session_id, str) or not isinstance(cwd, str):
            return 0
        settings = CodexPluginSettings() if settings is None else settings
        resolve_scope_id(
            cwd,
            session_id=session_id,
            settings=settings,
            deadline=monotonic() + settings.http_budget_seconds,
            persist_session=True,
        )
    except (ScopeBindingError, ValueError, OSError, json.JSONDecodeError):
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
