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

import pytest
from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryEvidence, AtomicMemoryExtractionInput
from powercontext.sources import SourceRef

from powercontext_eval_evidence_admission.manifests import EvaluationError
from powercontext_eval_evidence_admission.models import Support, digest
from powercontext_eval_evidence_admission.v14.codec import (
    TemplateOutput,
    build_cells,
    cell_binding,
    render,
    split_cells,
)

ALIASES = ("source:1",)


def supplied(text):
    evidence = AtomicMemoryEvidence(
        evidence_id="source:1",
        source_ref=SourceRef(source_type="content", source_id="source-a"),
        journal_position=1,
        content={"content": text},
        source_metadata={},
    )
    value = AtomicMemoryExtractionInput(evidence=(evidence,))
    support = Support(
        evidence_id=evidence.evidence_id,
        source_hash=digest("source-a"),
        field_hash=digest("/content/content"),
        materialization_hash=digest("material-1"),
        projection_hash=digest(evidence.model_dump(mode="json")),
        byte_start=0,
        byte_end=len(text.encode()),
        char_start=0,
        char_end=len(text),
    )
    return value, (support,)


def copy_template(cell, evidence_id="e0"):
    return TemplateOutput.model_validate(
        {
            "candidates": [
                {
                    "kind": "fact",
                    "description": "Synthetic protocol candidate",
                    "evidence_ids": [evidence_id],
                    "parts": [{"type": "copy", "quote_id": cell.cell_id}],
                }
            ]
        }
    )


def test_backtick_spans_become_cells():
    text = "Deploy with `deployctl --region cn-2 --retries 03`; path `/srv/synthetic/app.toml`, job `batch_A-17`, limit `0.075`.\n"
    value, supports = supplied(text)
    cells = build_cells(value, supports)
    assert [c.value for c in cells] == [
        "deployctl --region cn-2 --retries 03",
        "/srv/synthetic/app.toml",
        "batch_A-17",
        "0.075",
    ]
    assert [c.cell_id for c in cells] == ["c0", "c1", "c2", "c3"]


def test_plain_ascii_without_backticks_has_no_cells():
    text = "The synthetic service number 0 uses region-0.\n"
    value, supports = supplied(text)
    assert build_cells(value, supports) == ()


def test_non_ascii_without_backticks_is_one_whole_cell():
    text = "用户要求：发布前检查中文说明、emoji 👩🏽‍💻 和组合字符 é。\r\n下一步保留原始换行。"
    value, supports = supplied(text)
    cells = build_cells(value, supports)
    assert len(cells) == 1
    assert cells[0].value == text
    assert cells[0].cell_id == "c0"


def test_render_copies_whole_cell_without_byte_offsets():
    text = "Deploy with `deployctl --region cn-2 --retries 03`; path `/srv/synthetic/app.toml`."
    value, supports = supplied(text)
    cells = build_cells(value, supports)
    output = copy_template(cells[0])
    rendered = render(output, cells, ALIASES)
    assert rendered.candidates[0].text == "deployctl --region cn-2 --retries 03"
    assert rendered.candidates[0].text.encode() == b"deployctl --region cn-2 --retries 03"


def test_original_unicode_whole_cell_preserved_byte_for_byte():
    text = "用户要求：检查 emoji 👩🏽‍💻 和组合字符 e\u0301。\r\n下一行"
    value, supports = supplied(text)
    cells = build_cells(value, supports)
    assert len(cells) == 1
    rendered = render(copy_template(cells[0]), cells, ALIASES)
    assert rendered.candidates[0].text.encode() == text.encode()


def test_plain_text_candidate_without_cells_is_valid():
    value, supports = supplied("plain ASCII fact without any cell")
    cells = build_cells(value, supports)
    assert cells == ()
    output = TemplateOutput.model_validate(
        {
            "candidates": [
                {
                    "kind": "fact",
                    "description": "Synthetic protocol candidate",
                    "evidence_ids": ["e0"],
                    "parts": [{"type": "text", "text": "plain fact"}],
                }
            ]
        }
    )
    rendered = render(output, cells, ALIASES)
    assert rendered.candidates[0].text == "plain fact"


def test_unknown_cell_id_rejected():
    value, supports = supplied("`known` `other`")
    cells = build_cells(value, supports)
    bad = TemplateOutput.model_validate(
        {
            "candidates": [
                {
                    "kind": "fact",
                    "description": "Synthetic protocol candidate",
                    "evidence_ids": ["e0"],
                    "parts": [{"type": "copy", "quote_id": "c999"}],
                }
            ]
        }
    )
    with pytest.raises(EvaluationError, match="copy_without_candidate_support"):
        render(bad, cells, ALIASES)


def test_cross_request_binding_rejected():
    a_value, a_supports = supplied("`first`")
    b_value, b_supports = supplied("`second`")
    a_cells = build_cells(a_value, a_supports)
    b_cells = build_cells(b_value, b_supports)
    assert a_cells[0].cell_id == b_cells[0].cell_id == "c0"
    assert cell_binding(a_cells) != cell_binding(b_cells)
    with pytest.raises(EvaluationError, match="cell_request_binding_mismatch"):
        render(copy_template(a_cells[0]), b_cells, ALIASES, request_hash=cell_binding(a_cells))


def test_copy_ref_rejects_byte_offsets():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TemplateOutput.model_validate(
            {
                "candidates": [
                    {
                        "kind": "fact",
                        "description": "Synthetic protocol candidate",
                        "evidence_ids": ["e0"],
                        "parts": [{"type": "copy", "quote_id": "c0", "byte_start": 0, "byte_end": 1}],
                    }
                ]
            }
        )


def test_split_cells_deterministic_and_no_substring_enumeration():
    text = "a `b c` d `e`"
    assert split_cells(text) == [("b c", 3, 6), ("e", 11, 12)]
    # Plain ASCII without backticks → no cells, not every substring.
    assert split_cells("plain text") == []
    # Non-ASCII or CRLF without backticks → one whole-string cell.
    assert split_cells("中文说明") == [("中文说明", 0, 4)]
    assert split_cells("line\r\nnext") == [("line\r\nnext", 0, 10)]


def test_full_identity_bound_internally_not_in_index():
    value, supports = supplied("`x`")
    cells = build_cells(value, supports)
    index = cells[0].index()
    assert set(index) == {"c", "v"}  # no byte offsets, no hashes exposed
    internal = cells[0].identity()
    assert internal["protocol"] == "v14-cell"
    assert len(internal["support"]["field_hash"]) == 64
