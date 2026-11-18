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

"""The published budget matrix must stay identical to the values the code resolves."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "inference_budget_matrix.py"
DOCS = (
    ROOT / "docs" / "zh" / "development" / "inference-budgets.md",
    ROOT / "docs" / "en" / "development" / "inference-budgets.md",
)


def _renderer():
    spec = importlib.util.spec_from_file_location("inference_budget_matrix", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _block(text: str) -> str:
    start = text.index("<!-- budget-matrix:begin -->")
    end = text.index("<!-- budget-matrix:end -->") + len("<!-- budget-matrix:end -->")
    return text[start:end]


def test_both_language_pages_publish_the_resolved_values() -> None:
    module = _renderer()
    expected = module.render_matrix()

    for doc in DOCS:
        assert _block(doc.read_text(encoding="utf-8")) == expected, f"{doc} drifted from the resolved budgets"


def test_the_two_language_pages_embed_the_identical_block() -> None:
    blocks = [_block(doc.read_text(encoding="utf-8")) for doc in DOCS]

    assert blocks[0] == blocks[1]
