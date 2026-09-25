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

import dataclasses
from typing import cast

import pytest

from powercontext_eval.benchmarks.longmemeval_v2.arms import (
    CURRENT_MEMORY_FTS,
    CURRENT_MEMORY_HYBRID,
    DEFAULT_EXPERIMENT_ARM_ID,
    EXPERIMENT_ARMS,
    QUERY_TIME_COMPACT,
    TASK_LENSED_SELECTION,
    WRITE_TIME_L0_L1,
    ExperimentArm,
    ExperimentArmError,
    arm_manifest_record,
    ensure_comparable_experiment_runs,
    get_experiment_arm,
    resolve_experiment_arm,
    supported_experiment_arm_ids,
)


def test_registry_contains_exactly_the_implemented_arms() -> None:
    assert EXPERIMENT_ARMS == (
        CURRENT_MEMORY_FTS,
        CURRENT_MEMORY_HYBRID,
        QUERY_TIME_COMPACT,
        WRITE_TIME_L0_L1,
        TASK_LENSED_SELECTION,
    )
    assert supported_experiment_arm_ids() == (
        "current-memory-fts-v1",
        "current-memory-hybrid-v1",
        "query-time-compact-v1",
        "write-time-l0-l1-v1",
        "task-lensed-selection-v1",
    )
    assert DEFAULT_EXPERIMENT_ARM_ID == "current-memory-fts-v1"


def test_the_two_registered_arms_differ_only_in_identity_and_search_mode() -> None:
    """A fair comparison requires every behavioural knob outside the arm to be equal."""

    fts_fields = dataclasses.asdict(CURRENT_MEMORY_FTS)
    hybrid_fields = dataclasses.asdict(CURRENT_MEMORY_HYBRID)
    assert fts_fields.pop("arm_id") != hybrid_fields.pop("arm_id")
    assert fts_fields.pop("search_mode") == "fts"
    assert hybrid_fields.pop("search_mode") == "hybrid"
    assert fts_fields == hybrid_fields


def test_get_experiment_arm_returns_the_registered_arm() -> None:
    assert get_experiment_arm("current-memory-fts-v1") is CURRENT_MEMORY_FTS
    assert get_experiment_arm("current-memory-hybrid-v1") is CURRENT_MEMORY_HYBRID
    assert get_experiment_arm("query-time-compact-v1") is QUERY_TIME_COMPACT
    assert get_experiment_arm("write-time-l0-l1-v1") is WRITE_TIME_L0_L1
    assert get_experiment_arm("task-lensed-selection-v1") is TASK_LENSED_SELECTION


@pytest.mark.parametrize("arm_id", ["", "   ", "l0-persistent-v1", "temporal-recency-v1", "task-lens-v1"])
def test_get_experiment_arm_rejects_unknown_ids_with_the_supported_list(arm_id: str) -> None:
    with pytest.raises(ExperimentArmError, match="supported arms"):
        get_experiment_arm(arm_id)


def test_resolve_experiment_arm_defaults_to_the_fts_arm() -> None:
    assert resolve_experiment_arm(None) is CURRENT_MEMORY_FTS
    assert resolve_experiment_arm("current-memory-hybrid-v1") is CURRENT_MEMORY_HYBRID
    assert resolve_experiment_arm(CURRENT_MEMORY_HYBRID) is CURRENT_MEMORY_HYBRID


def test_arm_manifest_record_matches_the_documented_shape() -> None:
    assert arm_manifest_record(CURRENT_MEMORY_HYBRID) == {
        "id": "current-memory-hybrid-v1",
        "retrieval_strategy": "memory-search",
        "search_mode": "hybrid",
        "memory_projection": "deterministic-compact-v1",
        "query_projection": "question-text-v1",
        "prepared_context_max_bytes": None,
        "temporal_filter": None,
        "task_lens": None,
    }


def test_query_time_compact_arm_uses_prepared_context_without_changing_ingestion() -> None:
    assert arm_manifest_record(QUERY_TIME_COMPACT) == {
        "id": "query-time-compact-v1",
        "retrieval_strategy": "prepared-context",
        "search_mode": None,
        "memory_projection": "deterministic-compact-v1",
        "query_projection": "powercontext-prepared-context-v1",
        "prepared_context_max_bytes": 8_000,
        "temporal_filter": None,
        "task_lens": None,
    }


def test_write_time_l0_l1_arm_changes_only_the_declared_memory_projection() -> None:
    record = arm_manifest_record(WRITE_TIME_L0_L1)
    assert record["id"] == "write-time-l0-l1-v1"
    assert record["retrieval_strategy"] == "memory-search"
    assert record["search_mode"] == "fts"
    assert record["memory_projection"] == "deterministic-l0-l1-v1"
    assert record["query_projection"] == "question-text-v1"


def test_task_lensed_arm_declares_its_deterministic_query_projection() -> None:
    record = arm_manifest_record(TASK_LENSED_SELECTION)
    assert record["id"] == "task-lensed-selection-v1"
    assert record["search_mode"] == "fts"
    assert record["memory_projection"] == "deterministic-compact-v1"
    assert record["query_projection"] == "deterministic-keyword-lens-v1"
    assert record["task_lens"] == "question-keywords-v1"


def comparable_manifest(arm: ExperimentArm, **overrides: object) -> dict[str, object]:
    manifest: dict[str, object] = {
        "schema": "powercontext.longmemeval-v2-smoke-run.v1",
        "classification": "smoke-subset",
        "run_id": "run-a",
        "started_at": "2026-09-20T00:00:00+00:00",
        "experiment_arm": arm_manifest_record(arm),
        "inputs": {
            "data_root": "D:/data",
            "dataset_lock": {"path": "D:/locks/a.json", "content_sha256": "lock-digest"},
            "smoke_manifest": {"path": "D:/locks/b.json", "content_sha256": "smoke-digest"},
            "harness": {"root": "D:/harness", "commit": "2cc8c540", "python": "D:/python"},
        },
        "processor": {"model": "Qwen/Qwen3.5-9B", "revision": "processor-sha"},
        "memory_context_max_tokens": 200_000,
        "powercontext": {
            "base_url": "http://127.0.0.1:18765",
            "token_env": "POWERCONTEXT_TOKEN",
            "search_mode": arm.search_mode,
            "search_limit": 10,
            "timeout_seconds": 30.0,
        },
        "reader": None,
        "judge": None,
        "revisions": {"powercontext": "pc-sha", "integration": "integration-sha"},
    }
    manifest.update(overrides)
    return manifest


def test_runs_that_differ_only_by_arm_are_comparable() -> None:
    ensure_comparable_experiment_runs(
        comparable_manifest(CURRENT_MEMORY_FTS),
        comparable_manifest(CURRENT_MEMORY_HYBRID),
    )


def test_runs_with_missing_comparison_fields_are_refused() -> None:
    manifest_a = comparable_manifest(CURRENT_MEMORY_FTS)
    base = comparable_manifest(CURRENT_MEMORY_HYBRID)
    inputs = dict(cast("dict[str, object]", base["inputs"]))
    del inputs["dataset_lock"]
    manifest_b = {**base, "inputs": inputs}

    with pytest.raises(ExperimentArmError, match=r"inputs\.dataset_lock\.content_sha256 is missing"):
        ensure_comparable_experiment_runs(manifest_a, manifest_b)

    revisions = dict(cast("dict[str, object]", base["revisions"]))
    del revisions["integration"]
    manifest_c = {**base, "revisions": revisions}
    with pytest.raises(ExperimentArmError, match=r"revisions\.integration is missing"):
        ensure_comparable_experiment_runs(manifest_a, manifest_c)


def test_runs_without_a_registered_arm_record_are_refused() -> None:
    manifest_a = comparable_manifest(CURRENT_MEMORY_FTS)

    without_arm = {
        key: value for key, value in comparable_manifest(CURRENT_MEMORY_HYBRID).items() if key != "experiment_arm"
    }
    with pytest.raises(ExperimentArmError, match="has no experiment_arm record"):
        ensure_comparable_experiment_runs(manifest_a, without_arm)

    tampered_arm = dict(cast("dict[str, object]", comparable_manifest(CURRENT_MEMORY_HYBRID)["experiment_arm"]))
    tampered_arm["search_mode"] = "vector"
    manifest_b = {**comparable_manifest(CURRENT_MEMORY_HYBRID), "experiment_arm": tampered_arm}
    with pytest.raises(ExperimentArmError, match="unregistered experiment_arm record"):
        ensure_comparable_experiment_runs(manifest_a, manifest_b)


def test_runs_that_differ_beyond_the_arm_are_refused() -> None:
    manifest_a = comparable_manifest(CURRENT_MEMORY_FTS)
    manifest_b = comparable_manifest(CURRENT_MEMORY_HYBRID)
    manifest_b["inputs"] = {
        "data_root": "D:/data",
        "dataset_lock": {"path": "D:/locks/other.json", "content_sha256": "other-lock-digest"},
        "smoke_manifest": {"path": "D:/locks/b.json", "content_sha256": "smoke-digest"},
        "harness": {"root": "D:/harness", "commit": "2cc8c540", "python": "D:/python"},
    }
    with pytest.raises(ExperimentArmError, match=r"inputs\.dataset_lock\.content_sha256"):
        ensure_comparable_experiment_runs(manifest_a, manifest_b)


def test_a_different_search_limit_or_processor_refuses_the_comparison() -> None:
    manifest_a = comparable_manifest(CURRENT_MEMORY_FTS)

    smaller_budget = comparable_manifest(CURRENT_MEMORY_HYBRID, memory_context_max_tokens=100_000)
    with pytest.raises(ExperimentArmError, match="memory_context_max_tokens"):
        ensure_comparable_experiment_runs(manifest_a, smaller_budget)

    other_limit = comparable_manifest(CURRENT_MEMORY_HYBRID)
    other_limit["powercontext"] = {
        "base_url": "http://127.0.0.1:18765",
        "token_env": "POWERCONTEXT_TOKEN",
        "search_mode": "hybrid",
        "search_limit": 5,
        "timeout_seconds": 30.0,
    }
    with pytest.raises(ExperimentArmError, match=r"powercontext\.search_limit"):
        ensure_comparable_experiment_runs(manifest_a, other_limit)

    other_reader = comparable_manifest(
        CURRENT_MEMORY_HYBRID, reader={"provider": "deepseek-openai", "model": "deepseek-chat"}
    )
    with pytest.raises(ExperimentArmError, match="reader"):
        ensure_comparable_experiment_runs(manifest_a, other_reader)

    other_powercontext_revision = comparable_manifest(
        CURRENT_MEMORY_HYBRID, revisions={"powercontext": "other-sha", "integration": "integration-sha"}
    )
    with pytest.raises(ExperimentArmError, match=r"revisions\.powercontext"):
        ensure_comparable_experiment_runs(manifest_a, other_powercontext_revision)
