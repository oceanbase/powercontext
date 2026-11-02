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

"""Exact evidence, conservative equality, and replay-comparison contracts."""

from __future__ import annotations

import copy
from dataclasses import replace

import pytest

from powercontext_eval_longmemeval_v2.lineage import (
    ARMS,
    EVIDENCE_VERSION,
    GROUPING_VERSION,
    LEXICAL_THRESHOLD,
    LEXICAL_VERSION,
    MANIFEST_SCHEMA,
    EntryRef,
    LineageEvaluationError,
    Material,
    Observation,
    ensure_comparable_lineage_runs,
    exact_representatives,
    lexical_pairs,
    measure_pool,
)


def material(scope="scope", source="source", digest="a" * 64):
    return Material(scope, "content", source, digest, "content", "1", "b" * 64, "1", "untrusted", "unknown")


def observation(key, text="same fact", *, sources=(), facts=(), scope="scope", artifacts=(), revision=1):
    return Observation(EntryRef(scope, key, revision), "fact", text, sources, artifacts, frozenset(facts))


def test_shared_source_preserves_distinct_facts_and_unknown_trust():
    pool = (
        observation("a", "Atlas deployment port is 8000.", sources=(material(),), facts=("port",)),
        observation("b", "Atlas deployment port is 9000.", sources=(material(),), facts=("historical-port",)),
    )
    metrics = measure_pool(pool)
    assert metrics["same_source_top_k_concentration"] == 1
    assert metrics["unique_fact_count"] == 2
    assert metrics["neutral_or_unknown_declaration_count"] == 2
    assert metrics["lexical_false_relation_pairs"] == 1
    assert exact_representatives(pool) == (0, 1)
    assert lexical_pairs(pool) == ((0, 1),)


def test_equality_projection_is_stable_byte_exact_and_keeps_pool_intact():
    pool = (
        observation("a", sources=(material(),)),
        observation("b", sources=(material(source="other"),)),
        observation("c", "same FACT"),
        observation("d", "same fact "),
    )
    before = copy.deepcopy(pool)
    assert exact_representatives(pool) == (0, 2, 3)
    assert measure_pool(pool)["unique_evidence_count"] == 2
    assert pool == before


def test_equal_material_digest_does_not_merge_source_identities():
    pool = (observation("a", sources=(material(source="one"),)), observation("b", sources=(material(source="two"),)))
    assert measure_pool(pool)["same_source_top_k_count"] == 1
    assert measure_pool(pool)["unique_evidence_count"] == 2


@pytest.mark.parametrize(
    "second",
    [EntryRef("scope", "retained", 2), EntryRef("scope", "retained", 1, "profile")],
)
def test_artifact_only_coverage_preserves_exact_family_and_revision(second):
    first = EntryRef("scope", "retained", 1)
    pool = (observation("a", artifacts=(first, first)), observation("b", artifacts=(second,)))
    assert measure_pool(pool)["unique_evidence_count"] == 2
    assert measure_pool(pool[:1])["unique_evidence_count"] == 1
    assert measure_pool(pool)["unique_source_evidence_count"] == 0
    assert measure_pool(pool)["unique_artifact_evidence_count"] == 2


def test_source_and_artifact_evidence_share_one_deduplicated_coverage_contract():
    ref = EntryRef("scope", "retained", 1)
    pool = (
        observation("a", sources=(material(),), artifacts=(ref,)),
        observation("b", sources=(material(),), artifacts=(ref,)),
    )
    assert measure_pool(pool)["unique_evidence_count"] == 2
    assert measure_pool(pool)["unique_source_evidence_count"] == 1
    assert measure_pool(pool)["unique_artifact_evidence_count"] == 1


def test_unknown_source_is_not_one_shared_lineage():
    metrics = measure_pool((observation("a"), observation("b")))
    assert metrics["unknown_source_evidence_count"] == 2
    assert metrics["same_source_top_k_count"] == 0
    assert metrics["exact_lineage_component_top_k_count"] == 1
    assert measure_pool(())["same_source_top_k_concentration"] is None


def test_missing_relation_oracle_is_unknown_rather_than_a_false_relation():
    pool = (observation("a", "Atlas deployment port is 8000."), observation("b", "Atlas deployment port is 9000."))
    metrics = measure_pool(pool)
    assert metrics["lexical_unknown_relation_pairs"] == 1
    assert metrics["lexical_false_relation_pairs"] == 0
    assert metrics["advisory_unverified_relation_pairs"] == 1


def test_exact_historical_artifact_edges_do_not_use_todays_version():
    predecessor = EntryRef("scope", "predecessor", 1)
    first = observation("a", artifacts=(predecessor,))
    second = observation("b", artifacts=(predecessor,))
    different_version = observation("c", artifacts=(replace(predecessor, revision=2),))
    assert measure_pool((first, second, different_version))["exact_lineage_component_top_k_count"] == 2


@pytest.mark.parametrize(
    "pool",
    [
        (observation("a"), observation("b", scope="foreign")),
        (observation("a", sources=(material(scope="foreign"),)),),
        (observation("a", artifacts=(EntryRef("foreign", "a", 1),)),),
        (observation("a"), observation("a")),
        tuple(observation(str(index)) for index in range(17)),
    ],
)
def test_unbounded_ambiguous_or_cross_scope_evidence_is_rejected(pool):
    with pytest.raises(LineageEvaluationError):
        measure_pool(pool)


def manifest():
    return {
        "schema": MANIFEST_SCHEMA,
        "experiment_arm": ARMS[0],
        "fixture_version": "fixture-v1",
        "fixture_sha256": "a" * 64,
        "runtime_revision": "b" * 40,
        "runtime_source_sha256": "c" * 64,
        "evaluation_source_sha256": "d" * 64,
        "backend": "sqlite",
        "capabilities": {"fts": True},
        "dependency_state": "unmerged",
        "grouping_version": GROUPING_VERSION,
        "evidence_version": EVIDENCE_VERSION,
        "lexical_version": LEXICAL_VERSION,
        "lexical_threshold": LEXICAL_THRESHOLD,
        "candidate_limit": 16,
        "top_k": 8,
        "model": None,
        "advisory_jev": {"status": "not_provided"},
        "environment": {"python": "3.11"},
    }


def test_comparison_allows_only_registered_arm_difference():
    first = manifest()
    ensure_comparable_lineage_runs(first, {**first, "experiment_arm": ARMS[1]})
    for key, value in first.items():
        if key == "experiment_arm":
            continue
        with pytest.raises(LineageEvaluationError):
            ensure_comparable_lineage_runs(first, {**first, key: str(value) + "changed"})
    with pytest.raises(LineageEvaluationError):
        ensure_comparable_lineage_runs(first, {**first, "new_unpinned_condition": True})
    with pytest.raises(LineageEvaluationError):
        ensure_comparable_lineage_runs(first, {**first, "experiment_arm": "unregistered"})


def test_comparison_refuses_two_equally_incomplete_manifests():
    incomplete = {"schema": MANIFEST_SCHEMA, "experiment_arm": ARMS[0]}
    with pytest.raises(LineageEvaluationError):
        ensure_comparable_lineage_runs(incomplete, incomplete)


def test_comparison_refuses_source_only_accounting_manifests():
    source_only = manifest()
    source_only.pop("evidence_version")
    with pytest.raises(LineageEvaluationError):
        ensure_comparable_lineage_runs(source_only, source_only)
