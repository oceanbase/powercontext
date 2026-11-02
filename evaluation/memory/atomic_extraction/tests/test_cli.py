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

import json
from pathlib import Path

import pytest

from evaluation.memory.atomic_extraction.cli import main
from evaluation.memory.atomic_extraction.manifest import load_manifest
from evaluation.memory.atomic_extraction.models import CandidateObservation, CaseObservation
from evaluation.memory.atomic_extraction.runner import prompt_run_identities

_SETTINGS_SENTINEL = "PRIVATE_PREDICTED_SOURCE_MARKER"


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")


def test_validate_reports_manifest_identity_without_case_content(capsys) -> None:
    assert main(["validate"]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["case_count"] >= 9
    assert len(output["manifest_sha256"]) == 64
    assert set(output["dispositions"]) == {"atomic_memory", "experience_or_skill", "reject"}


def test_pin_copies_only_safe_artifacts(tmp_path: Path) -> None:
    run = tmp_path / "private-run"
    run.mkdir()
    loaded = load_manifest()
    _write(
        run / "run-manifest.json",
        {
            "schema_version": "powercontext.atomic-memory-extraction-run.v1",
            "run_id": "run-1",
            "started_at": "2026-10-19T08:00:00Z",
            "dataset": {
                "schema_version": loaded.manifest.schema_version,
                "dataset_version": loaded.manifest.dataset_version,
                "manifest_sha256": loaded.sha256,
                "case_count": len(loaded.manifest.cases),
            },
            "prompts": {
                profile.value: identity.model_dump(mode="json") for profile, identity in prompt_run_identities().items()
            },
            "provider": {
                "provider": "fixture",
                "model": "fixture:model",
                "base_url_configured": False,
                "base_url_sha256": None,
                "header_names": [],
                "model_settings": {
                    "openai_prediction": {"content": _SETTINGS_SENTINEL},
                    "temperature": _SETTINGS_SENTINEL,
                },
                "model_settings_sha256": "c" * 64,
                "timeout_seconds": 30,
                "max_requests": 2,
            },
        },
    )
    observations = []
    for case in loaded.manifest.cases:
        candidates = ()
        if case.expected.disposition == "atomic_memory":
            candidates = (
                CandidateObservation(
                    kind=case.expected.required_kinds[0],
                    text=" ".join((*case.expected.required_subjects, *case.expected.required_literals, "durable fact")),
                    evidence_ids=case.expected.required_evidence_ids,
                ),
            )
        observations.append(
            CaseObservation(case_id=case.case_id, status="succeeded", candidates=candidates).model_dump_json()
        )
    (run / "observations.private.jsonl").write_text("\n".join(observations) + "\n", encoding="utf-8")
    destination = tmp_path / "pinned"

    assert main(["pin", "--run-directory", str(run), "--destination", str(destination)]) == 0

    assert {path.name for path in destination.iterdir()} == {"run-manifest.json", "report.json", "report.md"}
    artifact_text = "".join(path.read_text(encoding="utf-8") for path in destination.iterdir())
    assert _SETTINGS_SENTINEL not in artifact_text
    assert all(case.case_id not in artifact_text for case in loaded.manifest.cases)
    pinned_manifest = json.loads((destination / "run-manifest.json").read_text(encoding="utf-8"))
    pinned_report = json.loads((destination / "report.json").read_text(encoding="utf-8"))
    for artifact in (pinned_manifest, pinned_report):
        settings = artifact["provider"]["model_settings"]
        assert set(settings["openai_prediction"]) == {"redacted_sha256"}
        assert set(settings["temperature"]) == {"redacted_sha256"}


def test_pin_rejects_a_run_from_a_different_prompt_contract(tmp_path: Path) -> None:
    run = tmp_path / "private-run"
    run.mkdir()
    loaded = load_manifest()
    prompts = {profile.value: identity.model_dump(mode="json") for profile, identity in prompt_run_identities().items()}
    prompts["coding"]["builtin_version"] = "powercontext.atomic-memory.extract.coding.v1"
    _write(
        run / "run-manifest.json",
        {
            "schema_version": "powercontext.atomic-memory-extraction-run.v1",
            "run_id": "stale-run",
            "started_at": "2026-10-19T08:00:00Z",
            "dataset": {
                "schema_version": loaded.manifest.schema_version,
                "dataset_version": loaded.manifest.dataset_version,
                "manifest_sha256": loaded.sha256,
                "case_count": len(loaded.manifest.cases),
            },
            "prompts": prompts,
            "provider": {
                "provider": "fixture",
                "model": "fixture:model",
                "base_url_configured": False,
                "base_url_sha256": None,
                "header_names": [],
                "model_settings": {},
                "model_settings_sha256": "c" * 64,
                "timeout_seconds": 30,
                "max_requests": 2,
            },
        },
    )
    (run / "observations.private.jsonl").write_text("", encoding="utf-8")

    with pytest.raises(SystemExit) as error:
        main(["pin", "--run-directory", str(run), "--destination", str(tmp_path / "pinned")])

    assert error.value.code == 2
