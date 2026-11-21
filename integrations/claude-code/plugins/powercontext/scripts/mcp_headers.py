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

"""Emit the Authorization header that setup saved for the endpoint Claude Code connects to.

Claude Code runs this as the plugin MCP server's `headersHelper` and passes the endpoint in
`CLAUDE_CODE_MCP_SERVER_URL`. The emitted header overrides the static one. When nothing is saved for that endpoint, or
on any failure, no header is emitted and the static `POWERCONTEXT_CLAUDE_AUTHORIZATION` header still applies.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    try:
        from claude_code_settings import mcp_authorization

        authorization = mcp_authorization(os.environ.get("CLAUDE_CODE_MCP_SERVER_URL", ""))
    except Exception:
        authorization = None
    json.dump({} if authorization is None else {"Authorization": authorization}, sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
