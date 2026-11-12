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

"""Replay recorded v14 templates through its real Pipeline, without new provider calls."""

from __future__ import annotations

from powercontext.builtin.inference import GenerationResult

from ..manifests import EvaluationError, file_hash, load_suite, read_json
from ..models import digest
from ..runner import run_sample
from ..v6.workflow import InputProbe
from ..v7.workflow import write_result
from ..v14.codec import TemplateOutput
from ..v14.live import bind, pipeline
from ..v14.run_live import score_literals
from .freeze import identity


class Recorded:
    def __init__(self, output):
        self.output = output

    async def generate(self, value, /):
        return GenerationResult(output=self.output)


async def replay_history(history_root, output_dir):
    if output_dir.exists():
        raise EvaluationError("v15_replay_output_already_exists")
    frozen = identity()
    suite = load_suite(experiment_version="v5", dataset="synthetic-v1")
    cases = {c.case_id: c for c in suite.cases}
    rows = []
    for label in ("v14-live-probe-2", "v14-live-unicode"):
        path = history_root / label / "result.json"
        record = read_json(path)
        template = TemplateOutput.model_validate(record["raw_template"])
        if digest(template.model_dump(mode="json")) != record["raw_template_hash"]:
            raise EvaluationError("v15_historical_template_changed")
        case = cases[record["case"]]
        probe = InputProbe()
        sample = await run_sample(suite, case, record["arm"], record["budget"], record["repetition"], generator=probe)
        if len(probe.inputs) != 1:
            raise EvaluationError("v15_historical_input_count_changed")
        request = bind(probe.inputs[0], sample.supports)
        if request.binding_hash != record["binding_hash"]:
            raise EvaluationError("v15_historical_request_binding_changed")
        candidates = await pipeline(request, Recorded(template)).extract(request.encoded.value)
        from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryExtractionOutput

        output = AtomicMemoryExtractionOutput(candidates=candidates)
        score = score_literals(case, output)
        if score != record["score"] or [c.text for c in candidates] != record["rendered_candidates"]:
            raise EvaluationError("v15_historical_replay_changed_output_or_score")
        rows.append(
            {
                "artifact_label": label,
                "source_sha256": file_hash(path),
                "request_binding_hash": request.binding_hash,
                "raw_template_hash": record["raw_template_hash"],
                "rendered_output_hash": digest(output.model_dump(mode="json")),
                "literal_required": score["all_required_literals"],
                "literal_retained": score["retained_required_literals"],
                "candidate_count": len(candidates),
                "historical_estimated_input_tokens": record["estimated_input_tokens"],
                "declared_budget": record["budget"],
                "historical_estimated_budget_passed": record["estimated_input_tokens"] <= record["budget"],
                "semantic_review": "not_newly_scored",
            }
        )
    if identity() != frozen:
        raise EvaluationError("v15_identity_changed_during_replay")
    result = {
        "schema": "evidence-admission.v15.historical-replay.v1",
        "identity_hash": digest(frozen),
        "rows": rows,
        "historical_unicode_failure_preserved": rows[1]["literal_retained"] == 1,
        "earliest_probe_replay": "unavailable_without_raw_template",
        "provider_requests": 0,
        "mode": "recorded_templates_through_historical_pipeline",
        "new_protocol_model_improvement": "unmeasured",
        "production_authorized": False,
    }
    write_result(output_dir / "result.json", result)
    return result
