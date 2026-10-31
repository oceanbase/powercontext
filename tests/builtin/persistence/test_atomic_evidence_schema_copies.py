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

"""Keep the runtime evidence revision and the Server bundle on identical DDL."""

from __future__ import annotations

import json
from pathlib import Path

_PERSISTENCE = Path(__file__).resolve().parents[3] / "src/powercontext/builtin/persistence"
_RUNTIME = _PERSISTENCE / "migrations/resources/schemas/atomic_memory_evidence_v1.json"
_SERVER = _PERSISTENCE / "migrations/server_resources/schemas"


def test_runtime_and_server_evidence_ddl_are_identical() -> None:
    # Runtime readiness compares SQLite `sqlite_master` text exactly, so a drift
    # between these two producer copies fails managed readiness outside CI.
    runtime = json.loads(_RUNTIME.read_text(encoding="utf-8"))
    sqlite = json.loads((_SERVER / "sqlite-v1_5_0_002.json").read_text(encoding="utf-8"))
    mysql = json.loads((_SERVER / "mysql-v1_5_0_002.json").read_text(encoding="utf-8"))

    assert runtime["sqlite"] == sqlite["create"]
    assert runtime["mysql"] == mysql["revisions"]["v1_5_0_002"]["statements"]
