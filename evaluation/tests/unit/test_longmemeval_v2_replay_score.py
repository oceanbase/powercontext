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
from typing import Any

import pytest

from powercontext_eval.benchmarks.longmemeval_v2 import replay_score
from powercontext_eval.benchmarks.longmemeval_v2.replay_score import ReplayScoreError, replay_score_smoke


class FakeMetrics:
    def eval_name(self, spec: str) -> str:
        return spec

    def eval_from_spec(self, spec: str, prediction: str, answer: str) -> bool:
        return spec == "exact" and prediction == answer

    def score_to_bool(self, value: Any) -> bool:
        return bool(value)


def score_artifacts(tmp_path: Path) -> Path:
    root = tmp_path / "score"
    root.mkdir()
    (root / "score-manifest.json").write_text(
        json.dumps({"classification": "smoke-subset-score-only"}), encoding="utf-8"
    )
    (root / "score-summary.json").write_text(json.dumps({"failed": 0}), encoding="utf-8")
    (root / "scoring-inputs.local.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "question_id": "deterministic",
                        "eval_function": "exact",
                        "reference_answer": "answer",
                        "parsed_prediction": "answer",
                        "reader_response_sha256": "reader-1",
                    }
                ),
                json.dumps(
                    {
                        "question_id": "judge",
                        "eval_function": "llm_gotchas_checker",
                        "reference_answer": "gold",
                        "parsed_prediction": "prediction",
                        "reader_response_sha256": "reader-2",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "judge-outputs.jsonl").write_text(json.dumps({"question_id": "judge", "label": 1}) + "\n", encoding="utf-8")
    return root


def test_replay_score_uses_saved_judge_label_without_network(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    score_dir = score_artifacts(tmp_path)
    output = tmp_path / "replay"
    monkeypatch.setattr(replay_score, "validate_harness_checkout", lambda root: None)
    monkeypatch.setattr(replay_score, "_load_metrics", lambda root: FakeMetrics())

    result = replay_score_smoke(score_dir=score_dir, harness_root=tmp_path / "harness", output_dir=output)

    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert summary["correct"] == 2
    assert summary["accuracy"] == 1.0
    assert summary["reader_calls"] == 0
    assert summary["judge_calls"] == 0
    results = [json.loads(line) for line in result.results_path.read_text(encoding="utf-8").splitlines()]
    assert [row["score_mode"] for row in results] == ["deterministic_replay", "llm_judge_replay"]
    assert all("reference_answer" not in row for row in results)


def test_replay_score_refuses_to_overwrite_before_loading_inputs(tmp_path: Path) -> None:
    output = tmp_path / "replay"
    output.mkdir()

    with pytest.raises(ReplayScoreError, match="Refusing to overwrite"):
        replay_score_smoke(score_dir=tmp_path / "missing", harness_root=tmp_path / "harness", output_dir=output)
