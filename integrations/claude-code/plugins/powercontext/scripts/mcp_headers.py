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

"""Emit optional PowerContext MCP headers without producing an empty auth header."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLUGIN_ROOT))

from claude_code_settings import ClaudeCodePluginSettings  # noqa: E402


def main() -> int:
    try:
        settings = ClaudeCodePluginSettings(
            authorization=os.environ.get("POWERCONTEXT_CLAUDE_AUTHORIZATION"),
        )
        headers = {} if settings.authorization is None else {"Authorization": settings.authorization}
        json.dump(headers, sys.stdout, separators=(",", ":"))
        sys.stdout.write("\n")
    except Exception:
        json.dump({}, sys.stdout)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
