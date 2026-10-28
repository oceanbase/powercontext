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

"""Re-execute the changed window control and inherit unchanged, hash-pinned arms."""

from __future__ import annotations

import asyncio
import json
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace

from ..manifests import EvaluationError, file_hash, read_json
from ..models import digest
from ..replay import replay_case
from ..runner import run_sample
from ..v6.workflow import INPUT_KEYS, InputProbe
from ..v7.workflow import DATASETS, reference_run, write_result
from ..v8.workflow import coverage, selection_manifest
from ..v9.codec import restore
from ..v10.protocol import identity as v10_identity
from .lineage import content_identity, historical_references
from .window import identity, prepare


def baseline(directory):
    expected = identity()["contract"]
    if file_hash(directory / "comparison.json") != expected["baseline_file_sha256"]:
        raise EvaluationError("v11_baseline_file_changed")
    marker = read_json(directory / "COMPLETE.json")
    for name, checksum in marker["files"].items():
        if Path(name).name != name or file_hash(directory / name) != checksum:
            raise EvaluationError("v11_baseline_bundle_changed")
    result = read_json(directory / "comparison.json")
    ids = [r["sample_id"] for r in result["samples"]]
    if (
        content_identity(result["identity"]) != content_identity(v10_identity())
        or len(ids) != len(set(ids))
        or len(ids) != expected["expected_total_identities"]
    ):
        raise EvaluationError("v11_baseline_identity_or_sample_set_changed")
    return result


async def replay_window(suite, case, reference, previous, limits):
    if case.fault != "none" or case.cancel_phase != "none":
        observed = await run_sample(
            suite, case, reference.arm, reference.budget, reference.repetition, generator=InputProbe()
        )
        if any(getattr(observed, k) != getattr(reference, k) for k in INPUT_KEYS):
            raise EvaluationError("v11_fault_or_cancellation_boundary_changed")
        if observed.memory_mutations or observed.published_cursor:
            raise EvaluationError("v11_fault_or_cancel_published_mutation")
        return {
            **previous,
            "measurement_mode": "unchanged_fault_boundary_reexecuted",
            "v11_status": observed.status,
            "completeness": observed.completeness,
            "full_window_admission_complete": False,
            "memory_mutations": observed.memory_mutations,
            "published_cursor": observed.published_cursor,
        }
    async with replay_case(case) as replay:
        if (
            replay.materializations != reference.materializations
            or tuple(digest(e.model_dump(mode="json")) for e in replay.value.evidence) != reference.projections
        ):
            raise EvaluationError("v11_replayed_source_identity_changed")
        plan = await prepare(replay, reference.budget, suite.experiment.fragment_bytes, limits)
        for request in plan.requests:
            encoded = request.encoded
            actual = restore(encoded, json.loads(json.dumps(encoded.payload)), request_hash=encoded.binding_hash)
            if actual != encoded.value:
                raise EvaluationError("v11_wire_roundtrip_changed")
        encoded = [r.encoded for r in plan.requests]
        covered = coverage(case, encoded)
        losses = {k: sorted(set(previous["coverage"][k]) - set(covered[k])) for k in covered}
        previous_executable = previous["coverage"] if previous["plan_accepted"] else {k: [] for k in covered}
        cost_losses = {k: sorted(set(previous_executable[k]) - set(covered[k])) for k in covered}
        values = [r.value for r in encoded]
        supports = tuple(s for r in encoded for s in r.supports)
        inventory_hash = digest(selection_manifest(values, supports))
        memories, cursor = await replay.mutations()
        if memories or cursor:
            raise EvaluationError("v11_preparation_published_mutation")
        return {
            **previous,
            "measurement_mode": "protocol_aware_window_reexecuted",
            "v10_plan_accepted": previous["plan_accepted"],
            "v10_coverage": previous["coverage"],
            "v10_calls": previous["executable_calls"],
            "v10_estimated_input_tokens": previous["estimated_input_tokens"],
            "v11_status": plan.status,
            "completeness": plan.admission.completeness,
            "reason": plan.reason,
            "full_window_admission_complete": plan.admission.accepted,
            "consumed_through": plan.consumed_through,
            "attempts": list(plan.attempts),
            "native_metrics": plan.admission.metrics.model_dump(mode="json"),
            "omissions": [o.model_dump(mode="json") for o in plan.admission.omissions],
            "selected_inventory_hash": inventory_hash,
            "selection_preserved": inventory_hash == previous["selected_inventory_hash"],
            "coverage": covered,
            "regressions": losses,
            "cost_accepted_regressions": cost_losses,
            "plan_accepted": True,
            "request_plan_semantics": "accepted_prefix_only_not_full_sample_success",
            "budget_overflow": {"per_call": False, "cumulative": False, "call_count": False},
            "planned_calls": len(plan.requests),
            "executable_calls": len(plan.requests),
            "estimated_input_tokens": sum(r.cost["single_estimated_input_tokens"] for r in plan.requests),
            "requests": [{"binding_hash": r.binding_hash, "cost": r.cost} for r in plan.requests],
            "memory_mutations": memories,
            "published_cursor": cursor,
        }


async def compare(baseline_dir: Path, v9_root: Path, output_dir: Path, *, minimal=False):
    if output_dir.exists():
        raise EvaluationError("v11_output_already_exists")
    frozen = identity()
    revision_args = ["git", "rev-parse", "HEAD"]
    revision = (await asyncio.to_thread(subprocess.check_output, revision_args, text=True)).strip()
    old = baseline(baseline_dir)
    historical = historical_references(v9_root, old)
    if {d: digest(r) for d, r in historical.items()} != old["baseline_comparison_hashes"]:
        raise EvaluationError("v11_reference_dependency_changed")
    old_rows = {r["sample_id"]: r for r in old["samples"]}
    failed = {r["sample_id"] for r in old["failures"]}
    if len(failed) != 6 or any(old_rows[k]["arm"] != "window-reduction-v1" for k in failed):
        raise EvaluationError("v11_unexpected_historical_failures")
    rows, repeat = [], defaultdict(set)
    completed = 0
    for dataset in DATASETS.values():
        suite, _, references = reference_run(dataset, Path(historical[dataset]["reference_dir"]))
        cases = {digest(c.model_dump(mode="json")): c for c in suite.cases}
        for reference in references:
            previous = old_rows.get(reference.sample_id)
            if previous is None or previous["dataset"] != dataset:
                raise EvaluationError("v11_baseline_reference_sample_mismatch")
            if minimal and reference.sample_id not in failed:
                continue
            if reference.arm != "window-reduction-v1":
                rows.append(previous)
                continue
            row = await replay_window(
                suite, cases[reference.case_hash], reference, previous, SimpleNamespace(**frozen["contract"]["caps"])
            )
            rows.append(row)
            repeat[(dataset, row["case"], row["budget"])].add(
                digest(
                    {
                        "requests": row["requests"],
                        "status": row["v11_status"],
                        "coverage": row["coverage"],
                        "attempts": row.get("attempts", []),
                        "completeness": row["completeness"],
                    }
                )
            )
            completed += 1
            if minimal or completed % 36 == 0:
                print(json.dumps({"window_samples": completed, "mode": "minimal" if minimal else "full"}), flush=True)
    expected = 6 if minimal else frozen["contract"]["expected_total_identities"]
    if len(rows) != expected or len({r["sample_id"] for r in rows}) != expected:
        raise EvaluationError("v11_final_sample_set_mismatch")
    if not minimal and completed != frozen["contract"]["expected_window_identities"]:
        raise EvaluationError("v11_window_sample_set_mismatch")
    final_revision = (await asyncio.to_thread(subprocess.check_output, revision_args, text=True)).strip()
    if identity() != frozen or final_revision != revision:
        raise EvaluationError("v11_code_changed_during_comparison")
    window = [r for r in rows if r["arm"] == "window-reduction-v1"]
    inherited = [r for r in rows if r["arm"] != "window-reduction-v1"]
    if any(r != old_rows[r["sample_id"]] for r in inherited):
        raise EvaluationError("v11_unchanged_arm_was_modified")
    recovered = [
        r["sample_id"]
        for r in window
        if r["sample_id"] in failed and r["full_window_admission_complete"] and not any(r["regressions"].values())
    ]
    regressions = [
        r["sample_id"]
        for r in window
        if any(r["regressions"].values())
        or any(r["cost_accepted_regressions"].values())
        or not r["selection_preserved"]
    ]
    nondeterministic = [digest(k) for k, signatures in repeat.items() if len(signatures) != 1]
    counts = Counter(r["measurement_mode"] for r in window)
    summary = {
        "samples": len(rows),
        "reexecuted_window_samples": len(window),
        "inherited_unchanged_samples": len(inherited),
        "measurement_modes": dict(counts),
        "recovered_historical_failures": len(recovered),
        "regressed_window_samples": regressions,
        "nondeterministic_groups": nondeterministic,
        "window_offline_gate_passed": len(recovered) == 6 and not regressions and not nondeterministic,
        "window_statuses": dict(Counter(r["v11_status"] for r in window)),
        "window_cost_accepted_eligible_claims": sum(
            len(r["coverage"]["same_request_claim_hashes"]) for r in window if r["reference_required_content_fits"]
        ),
        "window_executable_calls": sum(r["executable_calls"] for r in window),
        "window_estimated_input_tokens": sum(r["estimated_input_tokens"] for r in window),
        "provider_requests": 0,
        "decision": "insufficient_evidence",
        "production_authorized": False,
    }
    result = {
        "schema": "evidence-admission.v11.offline-comparison.v1",
        "identity": frozen,
        "execution_revision": revision,
        "committed_freeze_verified": False,
        "mode": "minimal_replay" if minimal else "changed_arm_replay_with_hash_pinned_unchanged_arms",
        "baseline_comparison_hash": digest(old),
        "samples": rows,
        "summary": summary,
        "recovered_sample_ids": sorted(recovered),
        "provider_generation": "not_run",
        "formal_acceptance": False,
        "live_authorized": False,
        "production_authorized": False,
    }
    write_result(output_dir / "comparison.json", result)
    write_result(output_dir / "summary.json", summary)
    write_result(output_dir / "COMPLETE.json", {"result_hash": digest(result)})
    return summary
