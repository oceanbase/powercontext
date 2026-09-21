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

"""Observable data provenance, chronology and label-isolation guarantees."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from benchmark.locomo_plus.dataset import (
    DEFAULT_SMOKE_PATH,
    SMOKE_CASE_IDS,
    ensure_dataset,
    load_locomo_plus,
    load_smoke_dataset,
    render_case_session,
)


def _write_dataset(directory: Path, plus: list[dict[str, Any]] | None = None) -> None:
    conversations = [
        {
            "sample_id": sample_id,
            "conversation": {
                "speaker_a": "Alice",
                "speaker_b": "Bob",
                "session_1_date_time": "10:00 am on 1 January, 2024",
                "session_1": [{"dia_id": "D1:1", "speaker": "Alice", "text": "I finished a walk."}],
                "session_2_date_time": "10:00 am on 15 January, 2024",
                "session_2": [{"dia_id": "D2:1", "speaker": "Bob", "text": "We watched the sunset."}],
                "session_3_date_time": "10:00 am on 29 January, 2024",
                "session_3": [{"dia_id": "D3:1", "speaker": "Alice", "text": "I bought a new book."}],
            },
            "qa": [
                {
                    "question": f"factual-question-{category}",
                    "answer": "SECRET_GOLD",
                    "category": category,
                    "evidence": ["D1:1"],
                }
                for category in range(1, 6)
            ],
        }
        for sample_id in ("sample-1", "sample-2", "sample-3")
    ]
    (directory / "locomo10.json").write_text(json.dumps(conversations), encoding="utf-8")
    (directory / "locomo_plus.json").write_text(json.dumps(plus or [_cognitive()]), encoding="utf-8")


def _cognitive(**overrides: Any) -> dict[str, Any]:
    return {
        "relation_type": "causal",
        "cue_dialogue": "A: Walking helped my mood.\nB: I am glad you felt better.",
        "trigger_query": "A: TRIGGER_ONLY I have had a difficult afternoon.",
        "time_gap": "two weeks later",
        "answer": "SECRET_COGNITIVE_GOLD",
        "constraint": "SECRET_CONSTRAINT",
        "model_name": "MODEL_METADATA_ONLY",
        **overrides,
    }


def test_factual_and_cognitive_sources_preserve_dates_and_citations_without_labels(tmp_path: Path) -> None:
    _write_dataset(tmp_path)
    dataset = load_locomo_plus(tmp_path)
    assert {case.category for case in dataset.cases} == {1, 2, 3, 4, 5, 6}
    case = dataset.cases[-1]
    assert case.relation_type == "causal"
    assert case.query_time == "2024-02-05 10:00"
    assert [session.session_id for session in case.sessions] == ["D1", "D2", "D4", "D3"]
    assert case.sessions[2].date_time == "2024-01-22 10:00"
    assert case.sessions[0].date_time == "10:00 am on 1 January, 2024"
    assert case.evidence == ("D4:1", "D4:2")
    assert case.evidence_text == "[D4:1] Alice: Walking helped my mood.\n[D4:2] Bob: I am glad you felt better."
    assert case.question == "Alice: TRIGGER_ONLY I have had a difficult afternoon."
    assert case.answer == ""
    assert case.metadata["upstream"]["constraint"] == "SECRET_CONSTRAINT"
    for selected in dataset.cases:
        history = "\n".join(render_case_session(selected, session) for session in selected.sessions)
        assert "[D1:1] Alice: I finished a walk." in history
        assert all(
            word not in history for word in ("SECRET", "causal", "factual-question", "TRIGGER_ONLY", "MODEL_METADATA")
        )


def test_malformed_cues_are_reported_and_do_not_shift_seeded_assignment(tmp_path: Path) -> None:
    malformed = _cognitive(cue_dialogue="A: A cue with literal escapes.\\nB: A response.")
    _write_dataset(
        tmp_path, [_cognitive(), malformed, _cognitive(relation_type="state", time_gap="several months later")]
    )
    dataset = load_locomo_plus(tmp_path, seed=17)
    assert len(dataset.exclusions) == 1
    assert dataset.exclusions[0]["case_id"] == "cognitive:0001"
    assert dataset.exclusions[0]["source_index"] == 1
    assert "exactly two" in dataset.exclusions[0]["reason"]
    assert dataset.manifest["raw_cognitive_count"] == 3
    assert dataset.manifest["eligible_relation_counts"] == {"causal": 1, "state": 1}
    case = dataset.cases[-1]
    assert case.metadata["time_gap"] == "several months later"
    assert case.metadata["time_gap_parsed"] is False
    assert case.metadata["cue_time"] == case.query_time
    assert dataset.manifest["unparsed_time_gap_case_ids"] == ["cognitive:0002"]
    _write_dataset(
        tmp_path, [_cognitive(), _cognitive(), _cognitive(relation_type="state", time_gap="several months later")]
    )
    repaired = load_locomo_plus(tmp_path, seed=17)
    assert repaired.cases[-1] == case
    assert repaired == load_locomo_plus(tmp_path, seed=17)


def test_selection_limits_questions_without_shortening_histories(tmp_path: Path) -> None:
    _write_dataset(tmp_path)
    dataset = load_locomo_plus(tmp_path)
    selected = dataset.selected_cases(mode="full", categories=(6,), limit=1)
    assert selected == (dataset.cases[-1],)
    assert len(selected[0].sessions) == 4
    with pytest.raises(ValueError, match="missing fixed smoke cases"):
        dataset.selected_cases()
    with pytest.raises(ValueError, match="positive"):
        dataset.selected_cases(mode="full", limit=0)


def test_expanded_smoke_selects_fifty_unique_balanced_cases_and_keeps_fixed_anchors(tmp_path: Path) -> None:
    relations = ("causal", "state", "goal", "value")
    anchors = dict(zip(SMOKE_CASE_IDS, relations, strict=True))
    _write_dataset(
        tmp_path,
        [_cognitive(relation_type=anchors.get(f"cognitive:{index:04d}", relations[index % 4])) for index in range(320)],
    )
    dataset = load_locomo_plus(tmp_path)
    selected = dataset.selected_cases(limit=50)

    assert len(selected) == len({case.case_id for case in selected}) == 50
    assert selected[:4] == dataset.selected_cases()
    assert Counter(case.relation_type for case in selected) == {"causal": 13, "state": 13, "goal": 12, "value": 12}
    assert selected == dataset.selected_cases(limit=50)
    assert all(case.category == 6 and len(case.sessions) == 4 for case in selected)
    with pytest.raises(ValueError, match="only 320 are available"):
        dataset.selected_cases(limit=321)


def test_existing_local_data_is_preserved_and_loaded(tmp_path: Path) -> None:
    _write_dataset(tmp_path)
    original = (tmp_path / "locomo10.json").read_bytes()
    ensure_dataset(tmp_path)
    dataset = load_locomo_plus(tmp_path)
    assert len(dataset.cases) == 16
    assert (tmp_path / "locomo10.json").read_bytes() == original


def test_bundled_data_can_be_reformatted_without_changing_cases(tmp_path: Path) -> None:
    path = tmp_path / DEFAULT_SMOKE_PATH.name
    raw = json.loads(DEFAULT_SMOKE_PATH.read_text(encoding="utf-8"))
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    assert load_smoke_dataset(path).cases == load_smoke_dataset().cases


def test_relation_labels_are_never_inferred_from_record_order(tmp_path: Path) -> None:
    _write_dataset(tmp_path, [_cognitive(relation_type="invented")])
    with pytest.raises(ValueError, match="Unknown LoCoMo-Plus relation_type"):
        load_locomo_plus(tmp_path)


def test_cognitive_cases_sharing_a_host_keep_independent_source_scopes(tmp_path: Path) -> None:
    _write_dataset(tmp_path, [_cognitive() for _ in range(189)])
    dataset = load_locomo_plus(tmp_path)
    cognitive = [case for case in dataset.cases if case.category == 6]
    assert len({case.sample_id for case in cognitive}) == len(cognitive)
    assert not {case.sample_id for case in cognitive} & {case.sample_id for case in dataset.cases if case.category < 6}
    first, goal_smoke = cognitive[0], cognitive[188]
    assert first.sample_id != goal_smoke.sample_id
    for case in (first, goal_smoke):
        history = "\n".join(render_case_session(case, session) for session in case.sessions)
        assert "cognitive:" not in history
        assert case.metadata["host_sample_id"] == case.conversation.sample_id
