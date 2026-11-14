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

"""Hosts that recall with the end of a long prompt bound the query the same way.

Claude Code, Codex, WorkBuddy, Hermes, LangChain, LangGraph, and Pydantic AI ship generated copies of the helper
tested here; the in-process adapters are also driven end to end below. The TypeScript hosts, Kimi Code, ZCode, and the
Bub harness tests read the same fixture.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from powercontext_integration_lifecycle import prepared_context
from tests.inprocess_adapter_turns import ADAPTERS, run_turn

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "tests/fixtures/recall_query_bound.json").read_text(encoding="utf-8"))


def _expand(segments: list[dict[str, Any]]) -> str:
    return "".join(segment["text"] * segment.get("repeat", 1) for segment in segments)


def test_fixture_character_bound_is_the_prepare_contract_limit() -> None:
    contract = yaml.safe_load((ROOT / "openapi/powercontext.yaml").read_text(encoding="utf-8"))
    query = contract["components"]["schemas"]["PrepareContextRequest"]["properties"]["query"]

    assert FIXTURE["max_characters"] == query["maxLength"]


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=[case["id"] for case in FIXTURE["cases"]])
def test_hook_recall_query_keeps_the_end_of_the_prompt(case: dict[str, Any]) -> None:
    limits = (prepared_context.MAX_QUERY_CHARACTERS, prepared_context.MAX_QUERY_BYTES)

    assert (FIXTURE["max_characters"], FIXTURE["max_bytes"]) == limits
    assert prepared_context.bounded_recall_query(_expand(case["prompt"])) == _expand(case["query"])


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=[case["id"] for case in FIXTURE["cases"]])
@pytest.mark.parametrize("adapter", ADAPTERS)
def test_inprocess_adapter_recalls_with_the_end_of_the_prompt(
    adapter: str, case: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    turn = run_turn(adapter, _expand(case["prompt"]), monkeypatch=monkeypatch, home=tmp_path)

    assert turn.queries == [_expand(case["query"])]
