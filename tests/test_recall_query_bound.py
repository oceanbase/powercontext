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

Claude Code, Codex, and WorkBuddy share the helper tested here. The TypeScript hosts, Kimi Code,
ZCode, and the Bub harness tests read the same fixture.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads((ROOT / "tests/fixtures/recall_query_bound.json").read_text(encoding="utf-8"))
# Codex and WorkBuddy ship identical copies of this module; test_integration_shared_files.py keeps them so.
HOOK_MODULE = ROOT / "integrations/claude-code/plugins/powercontext/hooks/prepared_context.py"


def _expand(segments: list[dict[str, Any]]) -> str:
    return "".join(segment["text"] * segment.get("repeat", 1) for segment in segments)


def _hook_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("powercontext_hook_prepared_context", HOOK_MODULE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_fixture_character_bound_is_the_prepare_contract_limit() -> None:
    contract = yaml.safe_load((ROOT / "openapi/powercontext.yaml").read_text(encoding="utf-8"))
    query = contract["components"]["schemas"]["PrepareContextRequest"]["properties"]["query"]

    assert FIXTURE["max_characters"] == query["maxLength"]


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=[case["id"] for case in FIXTURE["cases"]])
def test_hook_recall_query_keeps_the_end_of_the_prompt(case: dict[str, Any]) -> None:
    module = _hook_module()

    assert (FIXTURE["max_characters"], FIXTURE["max_bytes"]) == (module.MAX_QUERY_CHARACTERS, module.MAX_QUERY_BYTES)
    assert module.bounded_recall_query(_expand(case["prompt"])) == _expand(case["query"])
