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

import asyncio
import hashlib
import json
from collections import Counter
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from evaluation.performance.input_encoding import judging
from evaluation.performance.input_encoding.dataset import digest
from evaluation.performance.input_encoding.report import judge_report
from powercontext.builtin.runtime.config import InferenceConfig


def reference_fixture(tmp_path):
    instance = SimpleNamespace(
        instance_id="public-case",
        problem_statement="Repair validation.\r\nKeep compatibility.\r\n",
        patch="@@ repair\n+validate(value)\n+preserve_compatibility()\n",
        test_patch="+assert validation_rejects_invalid\n+assert compatibility_retained\n",
    )
    catalog = SimpleNamespace(instance_ids=(instance.instance_id,), require=lambda key: instance)
    reference = {
        "protocol": "source-audited-reference-v1",
        "cases": [
            {
                "instance_id": instance.instance_id,
                "audit_note": "Reviewed against public source; tests not executed.",
                "questions": [
                    {
                        "question": question,
                        "expected_answer": "Validate while preserving compatibility.",
                        "evidence": [{"field": "patch", "start_line": 2, "end_line": 2}],
                    }
                    for question in ("What changes?", "What must be retained?", "What should be checked?")
                ],
            }
        ],
    }
    path = tmp_path / "references.json"
    path.write_text(json.dumps(reference))
    return instance, catalog, reference, path


@pytest.mark.parametrize(
    ("span", "message"),
    [
        ({"field": "patch", "start_line": 0, "end_line": 1}, "invalid schema"),
        ({"field": "patch", "start_line": True, "end_line": 1}, "invalid schema"),
        ({"field": "patch", "start_line": 3, "end_line": 2}, "outside its source"),
        ({"field": "patch", "start_line": 1, "end_line": 4}, "outside its source"),
        ({"field": "invented", "start_line": 1, "end_line": 1}, "invalid schema"),
    ],
)
def test_reference_rejects_invalid_original_source_range(tmp_path, span, message):
    _, catalog, reference, path = reference_fixture(tmp_path)
    reference["cases"][0]["questions"][0]["evidence"] = [span]
    path.write_text(json.dumps(reference))
    with pytest.raises(ValueError, match=message):
        judging.resolve_reference_questions(path, catalog, {"public-case"})


def test_reference_extracts_exact_source_lines_and_requires_all_planned_cases(tmp_path):
    _, catalog, reference, path = reference_fixture(tmp_path)
    reference["cases"][0]["questions"][0]["evidence"] = [{"field": "problem_statement", "start_line": 1, "end_line": 2}]
    path.write_text(json.dumps(reference))
    resolved = judging.resolve_reference_questions(path, catalog, {"public-case"})
    excerpt = resolved["cases"]["public-case"]["questions"][0]["evidence"][0]["source_excerpt"]
    assert excerpt == "Repair validation.\r\nKeep compatibility.\r\n"
    with pytest.raises(ValueError, match="every planned case"):
        judging.resolve_reference_questions(path, catalog, {"public-case", "not-covered"})
    reference["cases"].append(reference["cases"][0])
    path.write_text(json.dumps(reference))
    with pytest.raises(ValueError, match="duplicate case IDs"):
        judging.resolve_reference_questions(path, catalog, {"public-case"})


def test_invalid_reference_stops_before_credentials_or_execution_artifacts(tmp_path, monkeypatch):
    _, catalog, reference, path = reference_fixture(tmp_path)
    reference["cases"][0]["questions"][0]["evidence"][0]["end_line"] = 999
    path.write_text(json.dumps(reference))
    dataset = tmp_path / "dataset"
    dataset.write_text("public fixture")
    job = {"instance_id": "public-case", "mode": "active", "repeat": 0}
    (tmp_path / "manifest.json").write_text(
        json.dumps({
            "jobs": [job],
            "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
        })
    )
    (tmp_path / "attempts").mkdir()
    key = digest(job)[:24]
    (tmp_path / "attempts" / f"{key}.json").write_text(
        json.dumps({
            **job,
            "attempt_id": key,
            "status": "no_proposal",
        })
    )
    monkeypatch.setattr(judging, "load_catalog", lambda path: catalog)
    args = SimpleNamespace(dataset=dataset, output=tmp_path, reference_questions=path)
    with pytest.raises(ValueError, match="outside its source"):
        asyncio.run(judging.judge(args))
    assert not (tmp_path / "judge").exists()
    assert not (tmp_path / "judge-runtime").exists()


def test_reference_judge_keeps_solver_blind_and_rejects_resume_after_reference_rotation(tmp_path, monkeypatch):
    instance, catalog, reference, reference_path = reference_fixture(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-key")
    dataset = tmp_path / "dataset.jsonl"
    dataset.write_text("fixture public data")
    jobs = [{"instance_id": instance.instance_id, "mode": mode, "repeat": 0} for mode in ("off", "shadow", "active")]
    manifest = {
        "model": "openai-chat:example",
        "jobs": jobs,
        "dataset_sha256": hashlib.sha256(dataset.read_bytes()).hexdigest(),
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "attempts").mkdir()
    for job, status in zip(jobs, ("succeeded", "no_proposal", "failed"), strict=True):
        key = digest(job)[:24]
        (tmp_path / "attempts" / f"{key}.json").write_text(
            json.dumps({
                **job,
                "attempt_id": key,
                "status": status,
                "content": {"lesson": "Validate and retain compatibility."},
            })
        )
    args = SimpleNamespace(
        dataset=dataset,
        output=tmp_path,
        reference_questions=reference_path,
        env_file=tmp_path / "unused-env",
        protection_config=None,
        max_output_tokens=4096,
        timeout=180,
        concurrency=1,
        validate_only=False,
    )
    observed = []

    @contextmanager
    def settings_context(**kwargs):
        yield SimpleNamespace(inference=InferenceConfig(generation_model="openai-chat:example"))

    async def open_model(*args, **kwargs):
        return None, object()

    class Generator:
        def __init__(self, **kwargs):
            self.output_type = kwargs["output_type"]

        async def generate(self, value):
            observed.append((self.output_type, value.model_dump()))
            if self.output_type is judging.TransferAnswers:
                output = judging.TransferAnswers(answers=["Validate and retain compatibility."] * 3)
            elif self.output_type is judging.Assessment:
                output = judging.Assessment(
                    grounding=2,
                    contradiction_free=2,
                    outcome_honesty=2,
                    actionability=2,
                    transfer_correct=[True] * 3,
                    explanation="Supported by source.",
                )
            else:
                raise AssertionError
            return SimpleNamespace(output=output, usage=None)

    monkeypatch.setattr(judging, "load_catalog", lambda path: catalog)
    monkeypatch.setattr(judging, "server_settings_context", settings_context)
    monkeypatch.setattr(judging, "_open_pydantic_ai_model", open_model)
    monkeypatch.setattr(judging, "PydanticAIStructuredGenerator", Generator)
    asyncio.run(judging.judge(args))
    solver = next(value for kind, value in observed if kind is judging.TransferAnswers)
    grader = next(value for kind, value in observed if kind is judging.Assessment)
    assert solver["evidence"] == ""
    assert all(set(question) == {"question"} for question in solver["questions"])
    assert grader["evidence"]["patch"] == instance.patch
    assert grader["evidence"]["test_patch"] == instance.test_patch
    assert "preserve_compatibility" in grader["evidence"]["patch"]  # outside cited source spans, still visible
    saved = json.loads((tmp_path / "judge/manifest.json").read_text())
    assert saved["protocol"].endswith("v4")
    assert saved["reference_questions"]["file_sha256"] == hashlib.sha256(reference_path.read_bytes()).hexdigest()
    question_record = json.loads(next((tmp_path / "judge/questions").glob("*.json")).read_text())
    assert question_record["actual_network_attempts"] == question_record["usage"]["requests"] == 0
    assert question_record["usage"]["total_tokens"] == 0
    assert question_record["source"] == "audited_reference"
    rows = [json.loads(path.read_text()) for path in (tmp_path / "attempts").glob("*.json")]
    report = judge_report(tmp_path, rows, Counter({"off": 1, "shadow": 1, "active": 1}))
    assert report["by_component"]["questions"]["reference_records_without_model_calls"] == 1
    assert report["arms"]["shadow"]["quality_status_counts"]["producer_no_proposal"] == 1
    assert report["arms"]["active"]["quality_status_counts"]["producer_failed"] == 1
    assert report["arms"]["active"]["transfer_questions_all_planned"] == 3

    before = {
        str(path.relative_to(tmp_path / "judge")): path.read_bytes()
        for path in (tmp_path / "judge").rglob("*")
        if path.is_file()
    }
    reference["cases"][0]["questions"][0]["question"] = "What must be validated?"
    reference_path.write_text(json.dumps(reference))
    with pytest.raises(ValueError, match="judge identity changed"):
        asyncio.run(judging.judge(args))
    assert {
        str(path.relative_to(tmp_path / "judge")): path.read_bytes()
        for path in (tmp_path / "judge").rglob("*")
        if path.is_file()
    } == before
