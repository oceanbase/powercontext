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

"""Reference-only tool wrapper; record with the isolated reference interpreter."""

from __future__ import annotations

import importlib.util
import json
import os
import runpy
from functools import lru_cache
from pathlib import Path
from typing import Any


@lru_cache(maxsize=1)
def protocol() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "python/powercontext_topic_chunk_rs/_tool_protocol.py"
    return runpy.run_path(str(path))


def tool_outcome(request: str, /) -> str:
    phase = os.environ["PC_TOPIC_TOOL_PHASE"]
    if phase not in ("record", "bench"):
        message = "Reference wrapper used outside reference recording or benchmark"
        raise RuntimeError(message)
    if phase == "record" and importlib.util.find_spec("powercontext_topic_chunk_rs") is not None:
        message = "Reference recorder contains the candidate package"
        raise RuntimeError(message)
    api = protocol()
    data, _ = api["context"]()
    identity, _ = api["decode_request"](request)
    from powercontext.builtin.artifacts.topic_memory.chunking import chunk_topic_memory_detail

    result = api["capture"](chunk_topic_memory_detail, request)
    golden = {
        row["id"]: row["expected"]
        for row in (
            json.loads(line) for line in (Path(data["root"]) / "corpus/golden.jsonl").read_text().split("\n") if line
        )
    }
    if result != api["canonical"](golden[identity]):
        message = "Reference wrapper differs from the frozen golden"
        raise RuntimeError(message)
    return result
