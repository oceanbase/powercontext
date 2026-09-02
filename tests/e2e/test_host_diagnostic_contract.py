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

from __future__ import annotations

import json
from pathlib import Path

import pytest

CONTRACT = json.loads(
    (Path(__file__).resolve().parents[1] / "fixtures" / "host_diagnostic_contract.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize(
    ("host", "component"),
    [
        ("codex", "powercontext.codex.recall"),
        ("claude_code", "powercontext.claude_code.recall"),
    ],
)
def test_host_diagnostic_contract_uses_visible_content_free_system_message(host: str, component: str) -> None:
    case = CONTRACT["cases"][host]
    message = json.loads(case["systemMessage"])

    assert case["event"] == "UserPromptSubmit"
    assert case["status"] == "completed"
    assert message == {
        "component": component,
        "event": "context_prepare",
        "outcome": "server_unavailable",
        "recovery": "powercontext doctor",
    }
    assert "prompt" not in case["systemMessage"]
    assert "scope" not in case["systemMessage"]
    assert "response" not in case["systemMessage"]
    if host == "codex":
        assert case["host_observation"] == f"UserPromptSubmit (completed) says: {case['systemMessage']}"
