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

from __future__ import annotations

import json
from pathlib import Path

CASES_PATH = Path(__file__).with_name("cases.json")
REQUIRED_CASES = {
    "cursor_catches_up",
    "malformed_acknowledgement",
    "flush_failure",
    "duplicate_delivery",
    "scope_isolation",
    "closing_boundaries",
    "cancellation_boundaries",
    "best_effort_fail_open",
    "required_mode_fail_closed",
    "extraction_unavailable",
}


def test_pre_compaction_conformance_fixture_is_complete_and_bounded() -> None:
    payload = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    cases = payload["cases"]

    assert payload["schema_version"] == 1
    assert {case["id"] for case in cases} == REQUIRED_CASES
    assert len(cases) == len(REQUIRED_CASES)
    assert all(case["description"].strip() for case in cases)
    assert all(isinstance(case["input"], dict) and case["input"] for case in cases)
    assert all(isinstance(case["expected"], dict) and case["expected"] for case in cases)
    assert all(set(case["modes"]) <= {"best_effort", "required_capable"} for case in cases)
    required = next(case for case in cases if case["id"] == "required_mode_fail_closed")
    assert required["modes"] == ["required_capable"]
    assert required["expected"] == {"host_continues": False}
