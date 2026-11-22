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

"""Print, bind, or clear the Scope of a Claude Code workspace from the command line."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from time import monotonic

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLUGIN_ROOT))

from claude_code_host import CLAUDE_CODE  # noqa: E402
from claude_code_settings import ClaudeCodePluginSettings  # noqa: E402
from powercontext_lifecycle.scope_binding import bind_scope, clear_scope_binding, resolve_scope_id  # noqa: E402


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", default=os.getcwd())
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--bind-scope", metavar="SCOPE_ID")
    action.add_argument("--clear-scope", action="store_true")
    arguments = parser.parse_args(argv)
    loaded = ClaudeCodePluginSettings.from_environment()
    settings = loaded.scope_binding_settings()
    deadline = monotonic() + loaded.http_budget_seconds
    if arguments.bind_scope is not None:
        print(bind_scope(CLAUDE_CODE, arguments.cwd, arguments.bind_scope, settings=settings, deadline=deadline))
        return 0
    if arguments.clear_scope:
        clear_scope_binding(CLAUDE_CODE, arguments.cwd, settings=settings, deadline=deadline)
    print(resolve_scope_id(CLAUDE_CODE, arguments.cwd, session_id=None, settings=settings, deadline=deadline))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
