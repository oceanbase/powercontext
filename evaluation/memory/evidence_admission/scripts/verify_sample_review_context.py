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

"""Run with the original checkout's Python environment, without changing its code."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def verify(session, appendix, queue_file):
    # Absolute imports intentionally resolve in the caller's original locked environment.
    from powercontext.builtin.artifacts.atomic_memory.extraction import (
        AtomicMemoryExtractionInput,
        AtomicMemoryExtractionOutput,
    )

    from powercontext_eval_evidence_admission.models import Support, digest
    from powercontext_eval_evidence_admission.v10.scoring import candidate_key, review_binding
    from powercontext_eval_evidence_admission.v18.core import identity, suite_for

    def load(path):
        return json.loads(path.read_text(encoding="utf-8"))

    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def require(condition):
        if not condition:
            raise ValueError("historical_review_binding_validation_failed")

    marker, index = load(appendix / "COMPLETE.json"), load(appendix / "artifact-index.json")
    require(marker["index_sha256"] == sha(appendix / "artifact-index.json"))
    for name, expected in index["files"].items():
        require(Path(name).name == name and name not in {".", ".."})
        path = appendix / name
        require(not path.is_symlink() and sha(path) == expected["sha256"] and path.stat().st_size == expected["bytes"])
    coverage, declaration, queue = (
        load(appendix / "coverage.json"),
        load(appendix / "declaration.json"),
        load(queue_file),
    )
    require(sha(queue_file) == declaration["basis_queue_sha256"])
    manifest, state = load(session / "preparation.json"), load(session / "state.json")
    require(digest(manifest) == declaration["preparation_hash"] and digest(state) == declaration["state_hash"])
    expected_identity = manifest["identity"]
    require(identity() == expected_identity)
    root = Path.cwd()
    for name, expected in expected_identity["files"].items():
        require(sha(root / name) == expected)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    require(revision == declaration["execution_revision"])
    rows = {r["row_id"]: r for r in manifest["rows"]}
    records = {r["call_id"]: r for r in queue["records"]}
    calls = {r["call_id"] for sample in coverage["records"] for r in sample["possible_records"]}
    cases = {
        dataset: {case.case_id: case for case in suite_for(dataset).cases}
        for dataset in {records[call]["dataset"] for call in calls}
    }
    candidates = 0
    for call_id in sorted(calls):
        require(len(call_id) == 64 and all(c in "0123456789abcdef" for c in call_id))
        record = records[call_id]
        row = rows[record["row_id"]]
        value = AtomicMemoryExtractionInput.model_validate(
            load(session / "inputs" / (record["row_id"] + ".json"))[record["input_index"]]
        )
        output = AtomicMemoryExtractionOutput.model_validate(load(session / "audit" / (call_id + ".output.json")))
        supports = tuple(Support.model_validate(s) for s in row["sample"]["supports"])
        case = cases[row["dataset"]][row["case"]]
        require(digest(value.model_dump(mode="json")) == record["input_hash"])
        require(digest(output.model_dump(mode="json")) == record["review_output_hash"])
        require(review_binding(case, value, supports, output) == record["review_context_hash"])
        for item in record["unresolved_candidates"]:
            candidate = output.candidates[item["candidate_index"]]
            require(candidate_key(candidate, item["candidate_index"]) == item["candidate_key"])
            require(digest(candidate.model_dump(mode="json")) == item["candidate_hash"])
            candidates += 1
    require(not subprocess.check_output(["git", "status", "--porcelain"]).strip())
    return {
        "schema": "evidence-admission.human-sample-frozen-validation.v1",
        "checkout_revision": revision,
        "original_identity_matched": True,
        "frozen_dependency_files_verified": len(expected_identity["files"]),
        "possible_call_bindings_verified": len(calls),
        "unresolved_candidate_bindings_verified": candidates,
        "counts_are_program_verification_not_human_review_coverage": True,
        "current_native_schema_substituted": False,
        "original_case_input_support_output_context_recomputed": True,
        "historical_pipeline_generation_rerun": False,
        "original_scores_recomputed": False,
        "private_session_modified": False,
        "provider_requests": 0,
        "validator_sha256": sha(Path(__file__)),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("session", "appendix", "queue", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.output.resolve().is_relative_to(args.session.resolve()):
            raise ValueError("historical_review_output_overlaps_session")
        result = verify(args.session, args.appendix, args.queue)
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    except (OSError, ValueError, KeyError, IndexError, StopIteration):
        print(json.dumps({"error": "historical_review_binding_validation_failed"}))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
