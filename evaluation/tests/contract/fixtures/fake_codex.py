#!/usr/bin/env python3
"""Offline fake for the Codex JSONL contract."""

from __future__ import annotations

import os
import sys

prompt = sys.stdin.buffer.read()
mode = os.environ.get("FAKE_CODEX_MODE", "ok")
if mode == "malformed":
    sys.stdout.buffer.write(b'{"type":"agent_message","message":"partial"}\nnot-json\n')
    raise SystemExit(0)
if mode == "nonzero":
    sys.stderr.write("synthetic failure\n")
    raise SystemExit(19)
if mode == "missing-usage":
    sys.stdout.buffer.write(b'{"type":"agent_message","message":"done"}\n{"type":"turn.completed"}\n')
    raise SystemExit(0)

sys.stdout.buffer.write(
    b'{"type":"thread.started","thread_id":"fake"}\n'
    + b'{"type":"agent_message","message":'
    + repr(prompt.decode("utf-8")).replace("'", '"').encode()
    + b"}\n"
    + b'{"type":"turn.completed","usage":{"input_tokens":7,"output_tokens":3}}\n'
)
