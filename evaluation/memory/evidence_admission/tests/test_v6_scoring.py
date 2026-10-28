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

from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryCandidate

from powercontext_eval_evidence_admission.manifests import case_catalog, load_suite
from powercontext_eval_evidence_admission.models import Sample, Support, digest
from powercontext_eval_evidence_admission.v6.scoring import score_literal_atoms


def example():
    case = next(c for c in load_suite(experiment_version="v5").cases if c.case_id == "exact-literals")
    span = next(s for s in case_catalog(case)["claims"] if s["required"])
    support = Support(
        evidence_id="source:1",
        source_hash=span["source_hash"],
        field_hash=span["field_hash"],
        materialization_hash=digest("m"),
        projection_hash=digest("p"),
        byte_start=span["byte_start"],
        byte_end=span["byte_end"],
        char_start=span["char_start"],
        char_end=span["char_end"],
    )
    sample = Sample(
        sample_id="sample",
        case_hash=digest(case.model_dump(mode="json")),
        arm="bounded-fragments-v3",
        budget=8192,
        repetition=0,
        completeness="bounded",
        status="generated",
        supports=(support,),
    )
    return case, sample


def reviewed(case, texts):
    candidates = tuple(AtomicMemoryCandidate(kind="fact", text=text, evidence_ids=("source:1",)) for text in texts)
    reviews = {digest({"case": case.case_id, "kind": "fact", "text": text}): [i] for i, text in enumerate(texts)}
    return candidates, reviews


def test_independent_facts_keep_full_command_context_and_own_support():
    case, sample = example()
    texts = [
        f"Deployment command is `{case.required[0].exact_literals[0]}`.",
        "Deployment configuration path is `/srv/synthetic/app.toml`.",
        "Deployment job is `batch_A-17`.",
        "Deployment limit is `0.075`.",
    ]
    candidates, reviews = reviewed(case, texts)
    score = score_literal_atoms(case, sample, candidates, reviews)
    assert len(score["retained_atom_ids"]) == 4 and score["composite_candidates"] == 0
    assert score["scope"] == "rescoring_recorded_v5_output_not_new_model_generation"
    wrong = sample.model_copy(
        update={"supports": (sample.supports[0].model_copy(update={"source_hash": digest("different-owner")}),)}
    )
    assert score_literal_atoms(case, wrong, candidates, reviews)["retained_atom_ids"] == []


def test_substring_in_wrong_or_negated_fact_is_never_automatically_approved():
    case, sample = example()
    candidate = AtomicMemoryCandidate(
        kind="fact", text="Other deployment must not use `/srv/synthetic/app.toml`.", evidence_ids=("source:1",)
    )
    score = score_literal_atoms(case, sample, (candidate,), {})
    assert score["retained_atom_ids"] == [] and score["unresolved_candidates"] == 1


def test_duplicate_values_and_normalized_numbers_do_not_replace_missing_identities():
    case, sample = example()
    candidate = AtomicMemoryCandidate(
        kind="fact", text="Deployment command is `deployctl --region cn-2 --retries 3`.", evidence_ids=("source:1",)
    )
    review = {digest({"case": case.case_id, "kind": candidate.kind, "text": candidate.text}): [0]}
    result = score_literal_atoms(case, sample, (candidate, candidate), review)
    assert result["retained_atom_ids"] == [] and result["literal_mismatches"] == 2


def test_combined_candidate_is_reported_as_combined_without_changing_v5_contract():
    case, sample = example()
    candidate = AtomicMemoryCandidate(kind="fact", text=case.required[0].text, evidence_ids=("source:1",))
    result = score_literal_atoms(case, sample, (candidate,), {})
    assert len(result["retained_atom_ids"]) == 4 and result["composite_candidates"] == 1
