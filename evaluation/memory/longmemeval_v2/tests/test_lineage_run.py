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

"""Native SQLite acceptance requires the exact Source-evidence prerequisite."""

from __future__ import annotations

import asyncio
import json

import pytest
from typer.testing import CliRunner

from powercontext_eval_longmemeval_v2.cli import app
from powercontext_eval_longmemeval_v2.lineage import ARMS, LineageEvaluationError
from powercontext_eval_longmemeval_v2.lineage_fixtures import fixture_cases
from powercontext_eval_longmemeval_v2.lineage_run import run_lineage_fixture


def test_native_fixture_refuses_missing_evidence_prerequisite(tmp_path):
    runtime = pytest.importorskip("powercontext.builtin.runtime.atomic_memory")
    if hasattr(runtime.ScopedAtomicMemory, "evidence"):
        pytest.skip("this runtime provides the prerequisite; covered by native acceptance")
    output = tmp_path / "report"
    with pytest.raises(LineageEvaluationError, match="#1975"):
        asyncio.run(run_lineage_fixture(output_dir=output, backend="sqlite", runtime_revision="a" * 40))
    assert not output.exists()


def test_native_sqlite_replay_exports_only_aggregates_and_preserves_shared_facts(tmp_path):
    runtime = pytest.importorskip("powercontext.builtin.runtime.atomic_memory")
    if not hasattr(runtime.ScopedAtomicMemory, "evidence"):
        pytest.skip("requires the exact #1975 evidence-read contract")
    output = tmp_path / "report"
    asyncio.run(
        run_lineage_fixture(
            output_dir=output,
            backend="sqlite",
            runtime_revision="a" * 40,
            dependency_state="unmerged",
        )
    )
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["cost"]["model_calls"] == 0
    assert report["recommendation"] == "no_product_change"
    assert report["backend_matrix"][0]["capabilities"]["authoritative_records_unchanged"]
    assert report["backend_matrix"][1]["status"] == "not_run"
    for row in report["aggregates"]:
        for section in ("pool", "delivered"):
            assert row[section]["unique_evidence_count"] == (
                row[section]["unique_source_evidence_count"] + row[section]["unique_artifact_evidence_count"]
            )
        assert row["comparison"]["lost_baseline_evidence"] == (
            row["comparison"]["lost_baseline_source_evidence"] + row["comparison"]["lost_baseline_artifact_evidence"]
        )
        if row["case_category"] == "artifact_only_evidence":
            assert row["pool"]["candidate_count"] == 2
            assert row["pool"]["unique_evidence_count"] == 2
            assert row["pool"]["unique_source_evidence_count"] == 0
            assert row["pool"]["unique_artifact_evidence_count"] == 2
            collapsed = row["experiment_arm"] == ARMS[1]
            assert row["delivered"]["candidate_count"] == (1 if collapsed else 2)
            assert row["delivered"]["unique_evidence_count"] == (1 if collapsed else 2)
            assert row["comparison"]["lost_baseline_evidence"] == int(collapsed)
            assert row["comparison"]["lost_baseline_source_evidence"] == 0
            assert row["comparison"]["lost_baseline_artifact_evidence"] == int(collapsed)
        if row["case_category"] in {"legitimate_shared_source", "held_out_shared_source"}:
            assert row["outcome"]["task_success"] == row["samples"]
            assert row["comparison"]["false_suppressed_baseline_facts"] == 0
        if row["case_category"] == "held_out_byte_budget":
            assert row["outcome"]["retrieval_missed_facts"] == 0
            assert row["outcome"]["post_retrieval_omitted_facts"] > 0
            assert row["omissions"]["truncated_items"] + row["omissions"]["dropped_items"] > 0
    published = "\n".join(path.read_text(encoding="utf-8") for path in output.iterdir())
    for case in fixture_cases():
        for entry in case.entries:
            assert entry.text not in published
            for fact in entry.facts:
                assert fact not in published
        assert f"lineage-fixture-{case.category}" not in published
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "lineage-compare",
            "--baseline",
            str(output / f"{ARMS[0]}.manifest.json"),
            "--alternative",
            str(output / f"{ARMS[1]}.manifest.json"),
        ],
    )
    assert result.exit_code == 0, result.output
    with pytest.raises(LineageEvaluationError, match="overwrite"):
        asyncio.run(run_lineage_fixture(output_dir=output, backend="sqlite", runtime_revision="a" * 40))


def test_invalid_comparison_fails_without_echoing_manifest_content(tmp_path):
    path = tmp_path / "invalid.json"
    path.write_text('{"secret-content": "never-echo-this"}', encoding="utf-8")
    result = CliRunner().invoke(app, ["lineage-compare", "--baseline", str(path), "--alternative", str(path)])
    assert result.exit_code == 1
    assert "never-echo-this" not in result.output


def test_backend_configuration_error_does_not_echo_connection_values(tmp_path):
    pytest.importorskip("powercontext.builtin.persistence.oceanbase")
    result = CliRunner().invoke(
        app,
        [
            "lineage-fixture",
            "--backend",
            "oceanbase",
            "--runtime-revision",
            "a" * 40,
            "--output-dir",
            str(tmp_path / "report"),
        ],
        env={"POWERCONTEXT_TEST_OCEANBASE_URL": "private-invalid-connection-value"},
    )
    assert result.exit_code == 1
    assert "private-invalid-connection-value" not in result.output
    assert not (tmp_path / "report").exists()
