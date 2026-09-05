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

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from powercontext_eval.benchmarks.longmemeval_v2.catalog import (
    RUN_INPUT_MANIFEST_SCHEMA,
    SMOKE_MANIFEST_SCHEMA,
    UPSTREAM_HARNESS_COMMIT,
    LongMemEvalV2Catalog,
    LongMemEvalV2CatalogError,
    SmokeCase,
    load_dataset_lock,
    load_smoke_manifest,
    validate_harness_checkout,
)
from powercontext_eval.benchmarks.longmemeval_v2.smoke import prepare_smoke_run

LOCKS = Path(__file__).parents[2] / "locks"


def _write_jsonl(path: Path, values: list[dict[str, object]]) -> None:
    path.write_text("".join(json.dumps(value) + "\n" for value in values), encoding="utf-8")


def data_root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    (root / "haystacks").mkdir(parents=True)
    _write_jsonl(
        root / "questions.jsonl",
        [
            {"id": "q-static", "domain": "web", "question": "static", "question_type": "static-environment"},
            {"id": "q-dynamic", "domain": "web", "question": "dynamic", "question_type": "dynamic-environment"},
            {"id": "q-workflow", "domain": "enterprise", "question": "workflow", "question_type": "procedure"},
            {"id": "q-gotcha", "domain": "enterprise", "question": "gotcha", "question_type": "errors-gotchas"},
            {"id": "q-premise", "domain": "web", "question": "premise", "question_type": "static-environment-abs"},
        ],
    )
    _write_jsonl(
        root / "trajectories.jsonl",
        [
            {"id": "t-web", "domain": "web"},
            {"id": "t-enterprise", "domain": "enterprise"},
        ],
    )
    (root / "haystacks" / "lme_v2_small.json").write_text(
        json.dumps(
            {
                "q-static": ["t-web"],
                "q-dynamic": ["t-web"],
                "q-workflow": ["t-enterprise"],
                "q-gotcha": ["t-enterprise"],
                "q-premise": ["t-web"],
            }
        ),
        encoding="utf-8",
    )
    return root


def smoke_manifest(path: Path) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema": SMOKE_MANIFEST_SCHEMA,
                "tier": "small",
                "cases": [
                    {"question_id": "q-static", "ability": "static_state"},
                    {"question_id": "q-dynamic", "ability": "dynamic_state"},
                    {"question_id": "q-workflow", "ability": "workflow_knowledge"},
                    {"question_id": "q-gotcha", "ability": "environment_gotchas"},
                    {"question_id": "q-premise", "ability": "premise_awareness"},
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def dataset_lock(path: Path, root: Path) -> Path:
    files = {
        "questions.jsonl": root / "questions.jsonl",
        "trajectories.jsonl": root / "trajectories.jsonl",
        "haystacks/lme_v2_small.json": root / "haystacks" / "lme_v2_small.json",
    }
    path.write_text(
        json.dumps(
            {
                "schema": "powercontext.longmemeval-v2-dataset-lock.v1",
                "upstream": {
                    "repository": "https://github.com/xiaowu0162/LongMemEval-V2",
                    "harness_commit": UPSTREAM_HARNESS_COMMIT,
                },
                "dataset_revision": "fixture-data-revision",
                "tier": "small",
                "files": {name: hashlib.sha256(value.read_bytes()).hexdigest() for name, value in files.items()},
            }
        ),
        encoding="utf-8",
    )
    return path


def test_catalog_validates_all_upstream_input_relationships(tmp_path: Path) -> None:
    catalog = LongMemEvalV2Catalog.load(data_root(tmp_path), tier="small")

    assert catalog.source_question_ids == ("q-static", "q-dynamic", "q-workflow", "q-gotcha", "q-premise")
    assert catalog.questions["q-workflow"].ability == "workflow_knowledge"
    assert set(catalog.input_digests) == {
        "questions.jsonl",
        "trajectories.jsonl",
        "haystacks/lme_v2_small.json",
    }


def test_catalog_rejects_a_cross_domain_haystack(tmp_path: Path) -> None:
    root = data_root(tmp_path)
    (root / "haystacks" / "lme_v2_small.json").write_text(
        json.dumps(
            {
                "q-static": ["t-enterprise"],
                "q-dynamic": ["t-web"],
                "q-workflow": ["t-enterprise"],
                "q-gotcha": ["t-enterprise"],
                "q-premise": ["t-web"],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(LongMemEvalV2CatalogError, match="crosses domains"):
        LongMemEvalV2Catalog.load(root, tier="small")


def test_smoke_selection_requires_all_published_abilities(tmp_path: Path) -> None:
    catalog = LongMemEvalV2Catalog.load(data_root(tmp_path), tier="small")

    with pytest.raises(LongMemEvalV2CatalogError, match="missing published abilities"):
        catalog.select_smoke((SmokeCase(question_id="q-static", ability="static_state"),))


def test_smoke_selection_requires_upstream_question_order(tmp_path: Path) -> None:
    catalog = LongMemEvalV2Catalog.load(data_root(tmp_path), tier="small")
    cases = load_smoke_manifest(smoke_manifest(tmp_path / "smoke.json")).cases

    with pytest.raises(LongMemEvalV2CatalogError, match="source order"):
        catalog.select_smoke((cases[1], cases[0], *cases[2:]))


def test_catalog_skips_blank_jsonl_rows_like_the_upstream_loader(tmp_path: Path) -> None:
    root = data_root(tmp_path)
    questions = root / "questions.jsonl"
    questions.write_text("\n" + questions.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    catalog = LongMemEvalV2Catalog.load(root, tier="small")

    assert len(catalog.questions) == 5


def test_catalog_streams_input_files_without_reading_them_all_at_once(monkeypatch, tmp_path: Path) -> None:
    root = data_root(tmp_path)

    def read_bytes(*_args: object, **_kwargs: object) -> bytes:
        raise AssertionError("catalog must stream large input files")

    monkeypatch.setattr(Path, "read_bytes", read_bytes)

    catalog = LongMemEvalV2Catalog.load(root, tier="small")

    assert len(catalog.questions) == 5


def test_smoke_manifest_is_fixed_and_validated_against_the_catalog(tmp_path: Path) -> None:
    root = data_root(tmp_path)
    manifest = smoke_manifest(tmp_path / "smoke.json")
    selection = load_smoke_manifest(manifest)
    catalog = LongMemEvalV2Catalog.load(root, tier=selection.tier)

    assert catalog.select_smoke(selection.cases).as_json() == json.loads(manifest.read_text(encoding="utf-8"))


def test_dataset_lock_pins_exact_file_digests(tmp_path: Path) -> None:
    root = data_root(tmp_path)
    lock = dataset_lock(tmp_path / "dataset-lock.json", root)
    loaded = load_dataset_lock(lock)

    LongMemEvalV2Catalog.load(root, tier=loaded.tier, expected_digests=loaded.file_digests)
    (root / "questions.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(LongMemEvalV2CatalogError, match="SHA-256 mismatch"):
        LongMemEvalV2Catalog.load(root, tier=loaded.tier, expected_digests=loaded.file_digests)


def test_checked_in_small_smoke_contract_is_valid() -> None:
    lock = load_dataset_lock(LOCKS / "longmemeval-v2-small-v1.dataset-lock.json")
    smoke = load_smoke_manifest(LOCKS / "longmemeval-v2-small-v1.smoke.json")

    assert lock.tier == "small"
    assert smoke.tier == lock.tier
    assert len(smoke.cases) == 10
    assert {case.ability for case in smoke.cases} == {
        "dynamic_state",
        "environment_gotchas",
        "premise_awareness",
        "static_state",
        "workflow_knowledge",
    }


def test_harness_checkout_requires_the_exact_pinned_revision(monkeypatch, tmp_path: Path) -> None:
    harness = tmp_path / "harness"
    (harness / "evaluation").mkdir(parents=True)
    (harness / "evaluation" / "harness.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(
        "powercontext_eval.benchmarks.longmemeval_v2.catalog.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, f"{UPSTREAM_HARNESS_COMMIT}\n", ""),
    )

    validate_harness_checkout(harness)


def test_harness_checkout_rejects_a_different_revision(monkeypatch, tmp_path: Path) -> None:
    harness = tmp_path / "harness"
    (harness / "evaluation").mkdir(parents=True)
    (harness / "evaluation" / "harness.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(
        "powercontext_eval.benchmarks.longmemeval_v2.catalog.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "0" * 40 + "\n", ""),
    )

    with pytest.raises(LongMemEvalV2CatalogError, match="harness checkout must be"):
        validate_harness_checkout(harness)


def test_prepare_smoke_run_writes_non_overwritable_provenance(tmp_path: Path) -> None:
    root = data_root(tmp_path)
    smoke = smoke_manifest(tmp_path / "smoke.json")
    lock = dataset_lock(tmp_path / "dataset-lock.json", root)
    harness = tmp_path / "harness"
    (harness / "evaluation").mkdir(parents=True)
    (harness / "evaluation" / "harness.py").write_text("", encoding="utf-8")
    output = tmp_path / "run"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        "powercontext_eval.benchmarks.longmemeval_v2.catalog.subprocess.run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, f"{UPSTREAM_HARNESS_COMMIT}\n", ""),
    )

    try:
        prepare_smoke_run(
            data_root=root,
            dataset_lock=lock,
            harness_root=harness,
            smoke_manifest=smoke,
            output_dir=output,
        )
        manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["schema"] == RUN_INPUT_MANIFEST_SCHEMA
        assert manifest["classification"] == "smoke-subset"
        assert manifest["upstream"]["harness_commit"] == UPSTREAM_HARNESS_COMMIT
        assert manifest["dataset"]["revision"] == "fixture-data-revision"
        assert manifest["dataset_lock"]["content_sha256"] == hashlib.sha256(lock.read_bytes()).hexdigest()
        assert manifest["smoke_manifest"]["content_sha256"] == hashlib.sha256(smoke.read_bytes()).hexdigest()
        assert "path_sha256" not in manifest["smoke_manifest"]
        with pytest.raises(LongMemEvalV2CatalogError, match="Refusing to overwrite"):
            prepare_smoke_run(
                data_root=root,
                dataset_lock=lock,
                harness_root=harness,
                smoke_manifest=smoke,
                output_dir=output,
            )
    finally:
        monkeypatch.undo()
