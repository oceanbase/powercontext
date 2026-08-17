#!/usr/bin/env python3
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
