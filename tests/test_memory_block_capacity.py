# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "history,states", [("distinct", ["active", "accepted", "mixed"]), ("repeated", ["accepted", "mixed"])]
)
def test_capacity_cli_populates_current_schema_and_verifies_lookup_classification(history, states):
    script = Path(__file__).resolve().parents[1] / "scripts/benchmark_memory_block_capacity.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--counts",
            "3",
            "--states",
            *states,
            "--scope-counts",
            "2",
            "--histories",
            history,
            "--runs",
            "2",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    environment, *cases = [json.loads(line) for line in result.stdout.splitlines()]
    assert environment["backend"] == "sqlite"
    assert len(cases) == len(states)
    for case in cases:
        assert case["count"] == case["backup_rows"] == 3
        assert case["scope_count"] == 2 and case["history"] == history
        assert case["active_rows"] + case["accepted_rows"] == 3
        assert case["block_id_characters"] == 36 and case["binding_characters"] == 20
        assert len(case["index_bytes"]) == 2 and all(case["index_bytes"].values())
        assert case["frontier_miss"]["matched_rows"] == 0
        assert case["exact_hit"]["matched_rows"] == 1
        if case["state"] == "accepted":
            assert case["frontier_hit"] is None and case["active_rows"] == 0
        else:
            assert case["frontier_hit"]["matched_rows"] == 1
