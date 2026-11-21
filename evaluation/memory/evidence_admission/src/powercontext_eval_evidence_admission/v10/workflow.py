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

"""Offline historical failure replay and three-dataset input regression comparison."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryExtractionOutput
from powercontext.builtin.inference import GenerationResult

from ..manifests import EvaluationError, file_hash, read_json
from ..models import Case, Support, digest
from ..runner import pipeline_for, run_sample
from ..v6.workflow import INPUT_KEYS, InputProbe
from ..v7.workflow import DATASETS, reference_run, write_result
from ..v8.workflow import coverage, selection_manifest
from ..v9.codec import restore
from ..v9.contract import identity as v9_identity
from ..v9.workflow import verified_comparisons
from ..v9_live.plan import load_session
from ..v9_live.runner import outputs_for, verified_state
from ..v9_live.scoring import candidate_rows, score_pair
from .protocol import identity, pack
from .scoring import candidate_key, review_binding, score


class RecordedOutput:
    def __init__(self, output):
        self.output = AtomicMemoryExtractionOutput.model_validate(output)

    async def generate(self, value, /):
        return GenerationResult(output=self.output)


async def replay_historical(canary_dir, output_dir):
    """Read only the completed first pair; keep old scores and the compound candidate intact."""
    frozen = identity()
    public, data, private = load_session(canary_dir / "session")
    state = verified_state(public, data, private)
    pair = data["pairs"][0]
    if Case.model_validate(pair["case"]).case_id != "exact-literals":
        raise EvaluationError("v10_expected_literal_failure_pair")
    outputs = outputs_for(pair, state, private)
    review = read_json(canary_dir / "semantic-review.json")
    old = score_pair(pair, outputs, review, reviewer=public["reviewer"], full=data["full"]["exact-literals"])
    from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryExtractionInput

    case = Case.model_validate(pair["case"])
    scores = {}
    rows = list(candidate_rows(pair, outputs))
    for protocol in ("v5", "v9"):
        calls = [c for c in pair["calls"] if c["protocol"] == protocol]
        if len(calls) != 1:
            raise EvaluationError("v10_minimal_replay_requires_single_call")
        call = calls[0]
        output = outputs[call["call_id"]]
        value = AtomicMemoryExtractionInput.model_validate(call["value"])
        candidates = await pipeline_for(RecordedOutput(output)).extract(value)
        replayed = AtomicMemoryExtractionOutput(candidates=candidates)
        if replayed.model_dump(mode="json") != output:
            raise EvaluationError("v10_historical_output_changed")
        judgements = {}
        for index, (old_key, _, candidate) in enumerate(r for r in rows if r[1]["call_id"] == call["call_id"]):
            judgements[candidate_key(candidate, index)] = {
                **review["candidates"][old_key],
                "false_attribution": False,
            }
        supports = tuple(Support.model_validate(s) for s in call["supports"])
        scores[protocol] = score(
            case,
            value,
            supports,
            replayed,
            {
                "reviewer": review["reviewer"],
                "reviewer_kind": "assistant",
                "output_hash": digest(output),
                "context_hash": review_binding(case, value, supports, replayed),
                "judgements": judgements,
            },
        )
    if identity() != frozen:
        raise EvaluationError("v10_code_changed_during_replay")
    result = {
        "schema": "evidence-admission.v10.historical-replay.v1",
        "identity": frozen,
        "mode": "recorded_output_replay_through_real_pipeline_not_live_v10_generation",
        "historical_contract_scores": old,
        "new_contract_diagnostics": scores,
        "historical_output_hashes": sorted(digest(o) for o in outputs.values()),
        "historical_review_hash": file_hash(canary_dir / "semantic-review.json"),
        "new_provider_requests": 0,
        "historical_failure_preserved": not old["candidate_gate_passed"],
        "new_protocol_model_improvement": "unmeasured",
        "production_authorized": False,
    }
    write_result(output_dir / "replay.json", result)
    write_result(output_dir / "COMPLETE.json", {"result_hash": digest(result)})
    return {"scores": scores, "historical_failure_preserved": result["historical_failure_preserved"]}


async def compare(v9_root: Path, output_dir: Path):
    if output_dir.exists():
        raise EvaluationError("v10_comparison_output_already_exists")
    frozen = identity()
    baseline = verified_comparisons(v9_root, v9_identity())
    rows, totals, repeat = [], defaultdict(Counter), defaultdict(set)
    for dataset in DATASETS.values():
        old = baseline[dataset]
        suite, _, references = reference_run(dataset, Path(old["reference_dir"]))
        cases = {digest(c.model_dump(mode="json")): c for c in suite.cases}
        old_rows = {r["sample_id"]: r for r in old["samples"]}
        for index, reference in enumerate(references):
            case = cases[reference.case_hash]
            probe = InputProbe()
            sample = await run_sample(
                suite, case, reference.arm, reference.budget, reference.repetition, generator=probe
            )
            if any(getattr(reference, key) != getattr(sample, key) for key in INPUT_KEYS):
                raise EvaluationError("v10_frozen_selection_changed")
            policy = suite.experiment.policy_for(reference.arm) or SimpleNamespace(
                max_calls_per_sample=frozen["contract"]["max_calls_per_sample"],
                total_input_budget_multiplier=frozen["contract"]["total_input_budget_multiplier"],
            )
            plan = pack(
                probe.inputs,
                sample.supports,
                sample.budget,
                policy,
                allow_repacking=sample.arm.startswith("bounded-fragments-"),
            )
            decoded = []
            for request in plan.requests:
                encoded = request.encoded
                value = restore(encoded, json.loads(json.dumps(encoded.payload)), request_hash=encoded.binding_hash)
                if value != encoded.value:
                    raise EvaluationError("v10_decoded_wire_changed")
                decoded.append(SimpleNamespace(value=value, supports=encoded.supports))
            before = selection_manifest(probe.inputs, sample.supports)
            after = selection_manifest([r.value for r in decoded], tuple(s for r in decoded for s in r.supports))
            if before != after or digest(before) != old_rows[sample.sample_id]["selected_inventory_hash"]:
                raise EvaluationError("v10_selected_inventory_changed")
            covered = coverage(case, decoded)
            previous = old_rows[sample.sample_id]
            losses = {k: sorted(set(previous["coverage"][k]) - set(covered[k])) for k in covered}
            executable_losses = {
                k: sorted(set(previous["coverage"][k]) - (set(covered[k]) if plan.accepted else set())) for k in covered
            }
            row = {
                "dataset": dataset,
                "sample_id": sample.sample_id,
                "case": case.case_id,
                "arm": sample.arm,
                "budget": sample.budget,
                "repetition": sample.repetition,
                "reference_status": reference.status,
                "reference_completeness": reference.completeness,
                "reference_required_content_fits": previous["reference_required_content_fits"],
                "plan_accepted": plan.accepted,
                "budget_overflow": plan.overflow,
                "selected_inventory_hash": digest(before),
                "selection_preserved": True,
                "coverage": covered,
                "regressions": losses,
                "cost_accepted_regressions": executable_losses,
                "v9_calls": previous["executable_calls"],
                "v9_estimated_input_tokens": previous["executable_estimated_input_tokens"],
                "planned_calls": len(plan.requests),
                "executable_calls": len(plan.requests) if plan.accepted else 0,
                "estimated_input_tokens": sum(r.cost["single_estimated_input_tokens"] for r in plan.requests),
                "requests": [{"binding_hash": r.binding_hash, "cost": r.cost} for r in plan.requests],
            }
            rows.append(row)
            count = totals[sample.arm]
            count["samples"] += 1
            count["protocol_budget_rejections"] += not plan.accepted
            count["original_budget_rejected_samples"] += reference.status == "budget_rejected"
            count["planned_calls"] += row["planned_calls"]
            count["estimated_input_tokens"] += row["estimated_input_tokens"]
            count["v9_calls"] += row["v9_calls"]
            count["v9_estimated_input_tokens"] += row["v9_estimated_input_tokens"]
            for key, lost in losses.items():
                count["regressed_" + key] += len(lost)
                count["cost_regressed_" + key] += len(executable_losses[key])
            if previous["reference_required_content_fits"]:
                count["required_parent_claims"] += previous["reference_required_claims"]
                count["cost_accepted_parent_claims"] += (
                    len(covered["same_request_claim_hashes"]) if plan.accepted else 0
                )
            repeat[(dataset, case.case_id, sample.arm, sample.budget)].add(
                digest(
                    {
                        "selection": before,
                        "requests": row["requests"],
                        "status": reference.status,
                        "completeness": reference.completeness,
                        "accepted": plan.accepted,
                    }
                )
            )
            if (index + 1) % 90 == 0:
                print(json.dumps({"dataset": dataset, "completed": index + 1, "total": len(references)}), flush=True)
    if identity() != frozen:
        raise EvaluationError("v10_code_changed_during_comparison")
    candidate = totals["bounded-fragments-v3"]
    failures = [r for r in rows if not r["plan_accepted"] or any(r["cost_accepted_regressions"].values())]
    nondeterministic = [digest(k) for k, signatures in repeat.items() if len(signatures) != 1]
    gate = (
        candidate["required_parent_claims"]
        == candidate["cost_accepted_parent_claims"]
        == frozen["contract"]["candidate_required_parent_claims"]
        and not nondeterministic
        and not any(r["arm"] == "bounded-fragments-v3" for r in failures)
    )
    result = {
        "schema": "evidence-admission.v10.offline-comparison.v1",
        "identity": frozen,
        "samples": rows,
        "arms": dict(totals),
        "nondeterministic_groups": nondeterministic,
        "candidate_input_gate_passed": gate,
        "failures": failures,
        "baseline_comparison_hashes": {d: digest(r) for d, r in baseline.items()},
        "formal_controls": "original_windows_without_protocol_repacking",
        "decision": "insufficient_evidence",
        "provider_requests": 0,
        "model_generation": "not_run",
        "live_authorized": False,
        "production_authorized": False,
    }
    write_result(output_dir / "comparison.json", result)
    write_result(output_dir / "COMPLETE.json", {"result_hash": digest(result)})
    summary = {k: result[k] for k in ("arms", "nondeterministic_groups", "candidate_input_gate_passed", "decision")}
    summary.update(samples=len(rows), failures=len(failures), provider_requests=0)
    write_result(output_dir / "summary.json", summary)
    return summary
