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
