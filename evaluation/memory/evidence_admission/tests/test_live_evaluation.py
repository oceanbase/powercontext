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
import json
from dataclasses import replace

import pytest
from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryCandidate, AtomicMemoryExtractionOutput
from powercontext.builtin.inference import GenerationResult, InferenceUsage
from typer.testing import CliRunner

from powercontext_eval_evidence_admission import cli, verification
from powercontext_eval_evidence_admission.cli import execute, manifest_for
from powercontext_eval_evidence_admission.live_evaluation import combined_live, live_plan
from powercontext_eval_evidence_admission.manifests import EvaluationError, load_suite
from powercontext_eval_evidence_admission.models import digest
from powercontext_eval_evidence_admission.report import aggregate, load_run, publish


@pytest.fixture(scope="module")
def live_runs(tmp_path_factory):
    directory = tmp_path_factory.mktemp("controlled-live-chain")
    suite = load_suite(experiment_version="v3")
    cases = tuple(c for c in suite.cases if c.case_id in {"small-complete", "single-source-overflow"})
    cases = tuple(
        c.model_copy(
            update={"required": tuple(claim.model_copy(update={"exact_literals": ("two",)}) for claim in c.required)}
        )
        for c in cases
    )
    suite = replace(
        suite,
        cases=cases,
        experiment=suite.experiment.model_copy(update={"budgets": (4096,), "repetitions": 2, "live_repetitions": 2}),
    )
    identity = {**cli.code_identity(suite), "frozen_committed": True}
    provider = {
        "provider": "openai-compatible",
        "model": "controlled-no-network",
        "endpoint_hash": digest("endpoint"),
        "max_requests": 1000,
    }

    class Extractor:
        async def generate(self, value, /):
            return GenerationResult(
                output=AtomicMemoryExtractionOutput(
                    candidates=(
                        AtomicMemoryCandidate(
                            kind="fact",
                            text=cases[0].required[0].text,
                            description=(cases[0].required[0].text)[:200],
                            evidence_ids=tuple(item.evidence_id for item in value.evidence),
                        ),
                    )
                ),
                usage=InferenceUsage(requests=1, input_tokens=11, output_tokens=7),
            )

    preparations, originals, audits = [], [], []
    original_identity, original_selection = cli.code_identity, cli.selected_suite
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(cli, "code_identity", lambda _: identity)
        patch.setattr(verification, "code_identity", lambda _: identity)
        patch.setattr(verification, "load_suite", lambda *args, dataset, **kwargs: replace(suite, dataset=dataset))
        patch.setattr(cli, "selected_suite", lambda root, private, version, dataset: replace(suite, dataset=dataset))
        for dataset in suite.experiment.v3.required_datasets:
            selected = replace(suite, dataset=dataset)
            prep, original, audited = (directory / f"{dataset}-{kind}" for kind in ("prep", "original", "audited"))
            raw = directory / f"{dataset}-private"
            asyncio.run(execute(selected, manifest_for(selected, "prepare"), prep))
            manifest = manifest_for(selected, "live", provider=provider)
            asyncio.run(execute(selected, manifest, original, generator=Extractor(), raw_dir=raw))
            reviews = raw / "reviews.json"
            reviews.write_text(json.dumps({"manifest_hash": digest(manifest), "reviews": {}}))
            result = CliRunner().invoke(
                cli.app,
                [
                    "audit",
                    "--run-dir",
                    str(original),
                    "--output-dir",
                    str(audited),
                    "--private-outputs",
                    str(raw / "outputs.json"),
                    "--reviews",
                    str(reviews),
                    "--experiment-version",
                    "v3",
                    "--dataset",
                    dataset,
                ],
            )
            assert result.exit_code == 0, result.exception
            preparations.append(prep)
            originals.append(original)
            audits.append(audited)
        patch.setattr(cli, "code_identity", original_identity)
        patch.setattr(cli, "selected_suite", original_selection)
        yield suite, tuple(preparations), tuple(originals), tuple(audits)


def test_complete_controlled_live_audit_chain_requires_all_datasets(live_runs):
    suite, prep, original, audited = live_runs
    report = combined_live(suite, prep, original, audited)
    assert report["decision"] == "bounded_projection_follow_up_required"
    assert not report["quality_failure_datasets"] and not report["incomplete_datasets"]
    assert all(
        r["candidate_generated_eligible_claims"] == r["candidate_eligible_required_claims"] == 4
        for r in report["runs"].values()
    )
    for name, paths in (("preparation", prep), ("original", original), ("audited", audited)):
        args = {"preparation_dirs": prep, "original_dirs": original, "audited_dirs": audited}
        args[{"preparation": "preparation_dirs", "original": "original_dirs", "audited": "audited_dirs"}[name]] = paths[
            :-1
        ]
        with pytest.raises(EvaluationError, match="missing_required_dataset"):
            combined_live(suite, **args)


def test_live_plan_counts_all_arms_and_keeps_smoke_separate(live_runs):
    suite, prep, _, _ = live_runs
    plan = live_plan(suite, prep)
    assert plan["formal_provider_request_cap"] == 48
    assert plan["formal_maximum_output_tokens"] == 48 * 2048
    assert plan["smoke"]["samples"] == 10 and plan["smoke"]["provider_request_cap"] == 8
    assert all(len(d["arms"]) == 5 for d in plan["datasets"].values())
    assert plan["currency_cost"] is plan["actual_provider_usage"] is None


def altered_runs(live_runs, tmp_path, transform, *, audit_only=False, manifest_update=None):
    suite, prep, original, audited = live_runs
    parent, samples, _ = load_run(original[-1])
    rows = tuple(transform(s) for s in samples)
    new_parent = {**parent, **(manifest_update or {})}
    new_original = tmp_path / "original"
    selected = replace(suite, dataset=parent["dataset"])
    if not audit_only:
        publish(new_original, new_parent, rows, aggregate(selected, rows, new_parent))
        original = (*original[:-1], new_original)
    else:
        new_parent = parent
    audit = {
        **new_parent,
        "mode": "audited_live",
        "parent_manifest_hash": digest(new_parent),
        "review_hash": digest({"manifest_hash": digest(new_parent), "reviews": {}}),
    }
    new_audit = tmp_path / "audited"
    publish(new_audit, audit, rows, aggregate(selected, rows, audit))
    return suite, prep, original, (*audited[:-1], new_audit)


@pytest.mark.parametrize(
    "failure", ["fact_loss", "literal_loss", "unresolved", "missing_control_output", "usage_missing", "uncommitted"]
)
def test_live_gate_distinguishes_quality_loss_and_missing_evidence(live_runs, tmp_path, failure):
    def change(sample):
        if failure == "missing_control_output" and sample.arm == "complete-projection-v1" and sample.generation:
            return sample.model_copy(update={"generation": None, "status": "failed", "usage_complete": False})
        if sample.arm != "bounded-fragments-v3" or not sample.generation:
            return sample
        if failure == "fact_loss":
            score = sample.generation.model_copy(
                update={
                    "retained_claim_hashes": (),
                    "retained_literal_hashes": (),
                    "candidates": 0,
                    "candidate_hashes": (),
                    "support": (),
                }
            )
            return sample.model_copy(update={"generation": score})
        if failure == "literal_loss":
            return sample.model_copy(
                update={"generation": sample.generation.model_copy(update={"retained_literal_hashes": ()})}
            )
        if failure == "unresolved":
            return sample.model_copy(update={"generation": sample.generation.model_copy(update={"unresolved": 1})})
        if failure == "usage_missing":
            return sample.model_copy(update={"usage_complete": False, "provider_input_tokens": None})
        return sample

    update = None
    if failure == "uncommitted":
        manifest = load_run(live_runs[2][-1])[0]
        update = {"code": {**manifest["code"], "frozen_committed": False}}
    report = combined_live(*altered_runs(live_runs, tmp_path, change, manifest_update=update))
    assert report["decision"] == (
        "no_production_change" if failure in {"fact_loss", "literal_loss"} else "insufficient_evidence"
    )
    if failure == "fact_loss":
        assert report["runs"]["validation-v3"]["generation_retention_failures"]
        assert report["runs"]["validation-v3"]["candidate_generated_eligible_claims"] == 0
        assert report["runs"]["validation-v3"]["candidate_eligible_required_claims"] == 4


@pytest.mark.parametrize("field", ["model", "endpoint_hash"])
def test_mixing_provider_identity_is_rejected(live_runs, tmp_path, field):
    manifest = load_run(live_runs[2][-1])[0]
    value = "another-model" if field == "model" else digest("another-endpoint")
    args = altered_runs(
        live_runs, tmp_path, lambda s: s, manifest_update={"provider": {**manifest["provider"], field: value}}
    )
    with pytest.raises(EvaluationError, match="provider_model_or_endpoint_mismatch"):
        combined_live(*args)


def test_audit_cannot_replace_usage_or_candidate_evidence(live_runs, tmp_path):
    def change(s):
        return s.model_copy(update={"provider_input_tokens": 999}) if s.generation else s

    with pytest.raises(EvaluationError, match="audit_changed_execution_or_usage"):
        combined_live(*altered_runs(live_runs, tmp_path, change, audit_only=True))


def test_per_dataset_request_caps_can_differ_but_cannot_be_exceeded(live_runs, tmp_path):
    manifest = load_run(live_runs[2][-1])[0]
    args = altered_runs(
        live_runs, tmp_path, lambda s: s, manifest_update={"provider": {**manifest["provider"], "max_requests": 2000}}
    )
    assert combined_live(*args)["decision"] == "bounded_projection_follow_up_required"
    args = altered_runs(
        live_runs,
        tmp_path / "low-cap",
        lambda s: s,
        manifest_update={"provider": {**manifest["provider"], "max_requests": 1}},
    )
    report = combined_live(*args)
    assert report["decision"] == "insufficient_evidence"
    assert report["runs"]["validation-v3"]["request_cap_breached"]


def test_live_plan_and_combined_cli_publish_reviewable_json(live_runs, tmp_path, monkeypatch):
    suite, prep, original, audited = live_runs
    monkeypatch.setattr(cli, "load_suite", lambda *args, **kwargs: suite)
    for command, extra in (
        ("plan-live", ()),
        ("compare-live", (("--original-run-dir", original), ("--audited-run-dir", audited))),
    ):
        path = tmp_path / f"{command}.json"
        arguments = [command, "--output-file", str(path)]
        for p in prep:
            arguments.extend(["--preparation-run-dir", str(p)])
        for flag, paths in extra:
            for p in paths:
                arguments.extend([flag, str(p)])
        result = CliRunner().invoke(cli.app, arguments)
        assert result.exit_code == 0, result.exception
        assert path.is_file()
        assert CliRunner().invoke(cli.app, arguments).exit_code != 0


def test_smoke_scope_uses_real_pipeline_journal_and_never_counts_as_formal(tmp_path, monkeypatch):
    suite = load_suite(experiment_version="v3")
    identity = {**cli.code_identity(suite), "frozen_committed": True}
    monkeypatch.setattr(cli, "code_identity", lambda _: identity)
    from contextlib import asynccontextmanager

    class Generator:
        async def generate(self, value, /):
            return GenerationResult(
                output=AtomicMemoryExtractionOutput(
                    candidates=(
                        AtomicMemoryCandidate(
                            kind="fact",
                            text=suite.cases[0].required[0].text,
                            description=(suite.cases[0].required[0].text)[:200],
                            evidence_ids=tuple(e.evidence_id for e in value.evidence),
                        ),
                    )
                ),
                usage=InferenceUsage(requests=1, input_tokens=11, output_tokens=7),
            )

    @asynccontextmanager
    async def provider(*args, **kwargs):
        yield Generator()

    monkeypatch.setattr(cli, "live_generator", provider)
    output, raw = tmp_path / "smoke", tmp_path / "private"
    result = CliRunner().invoke(
        cli.app,
        [
            "run-smoke",
            "--output-dir",
            str(output),
            "--audit-dir",
            str(raw),
            "--provider",
            "openai-compatible",
            "--model",
            "controlled-no-network",
            "--token-env",
            "UNUSED",
            "--base-url",
            "https://example.invalid/v1",
            "--max-requests",
            "8",
        ],
    )
    assert result.exit_code == 0, result.exception
    manifest, samples, report = load_run(output)
    assert manifest["mode"] == "smoke" and manifest["experiment"] == suite.experiment.model_dump(
        mode="json", exclude_none=True
    )
    assert manifest["repetitions"] == 1 and len(samples) == 10
    assert report["smoke"]["execution_complete"] and report["smoke"]["provider_requests"] == 8
    assert not report["coverage_complete"] and report["decision"] == "insufficient_evidence"
    journal = json.loads((raw / "outputs.json").read_text())
    assert len(journal["outputs"]) == 8 and json.loads((raw / "STATE.json").read_text())["status"] == "complete"
    assert (raw.stat().st_mode & 0o777) == 0o700
    assert all((p.stat().st_mode & 0o777) == 0o600 for p in raw.iterdir())
    result = CliRunner().invoke(cli.app, ["report", "--experiment-version", "v3", "--run-dir", str(output)])
    assert result.exit_code == 0, result.exception
    assert json.loads(result.output) == report


@pytest.mark.parametrize("command", ["run-live", "run-smoke"])
def test_uncommitted_freeze_blocks_provider_construction(tmp_path, monkeypatch, command):
    identity = {**cli.code_identity(load_suite(experiment_version="v3")), "frozen_committed": False}
    monkeypatch.setattr(cli, "code_identity", lambda _: identity)
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(True)
        raise AssertionError("provider must not be initialized")

    monkeypatch.setattr(cli, "live_generator", forbidden)
    arguments = [
        command,
        "--output-dir",
        str(tmp_path / "run"),
        "--audit-dir",
        str(tmp_path / "private"),
        "--provider",
        "openai-compatible",
        "--model",
        "not-run",
        "--token-env",
        "NOT_CONFIGURED",
        "--base-url",
        "https://example.invalid/v1",
        "--max-requests",
        "8",
    ]
    if command == "run-live":
        arguments.extend(["--experiment-version", "v3"])
    result = CliRunner().invoke(cli.app, arguments)
    assert result.exit_code != 0 and "committed" in str(result.exception)
    assert not calls and not (tmp_path / "run").exists() and not (tmp_path / "private").exists()


def test_audit_cannot_expand_candidate_support(live_runs, tmp_path):
    def change(sample):
        if sample.arm != "bounded-fragments-v3" or not sample.generation or not sample.generation.support:
            return sample
        supports = sample.generation.support
        first = supports[0][0].model_copy(update={"byte_end": supports[0][0].byte_end + 1})
        changed = ((first, *supports[0][1:]), *supports[1:])
        return sample.model_copy(update={"generation": sample.generation.model_copy(update={"support": changed})})

    with pytest.raises(EvaluationError, match="audit_changed_candidates_or_support"):
        combined_live(*altered_runs(live_runs, tmp_path, change, audit_only=True))
