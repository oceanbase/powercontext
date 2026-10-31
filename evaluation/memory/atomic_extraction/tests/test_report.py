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
from typing import Any

import pytest

from evaluation.memory.atomic_extraction.manifest import LoadedManifest, load_manifest
from evaluation.memory.atomic_extraction.models import CandidateObservation, CaseObservation
from evaluation.memory.atomic_extraction.report import build_report, render_markdown

_SENTINEL = "PRIVATE_GENERATED_CANDIDATE_SENTINEL"
_SETTINGS_SENTINEL = "PRIVATE_PREDICTED_SOURCE_MARKER"


def _run_manifest(loaded: LoadedManifest) -> dict[str, Any]:
    return {
        "schema_version": "powercontext.atomic-memory-extraction-run.v1",
        "run_id": "fixture-run",
        "started_at": "2026-10-19T08:00:00Z",
        "dataset": {
            "schema_version": loaded.manifest.schema_version,
            "dataset_version": loaded.manifest.dataset_version,
            "manifest_sha256": loaded.sha256,
            "case_count": len(loaded.manifest.cases),
        },
        "prompts": {
            "coding": {
                "key": "atomic_memory.extract",
                "definition_version": "powercontext.atomic-memory.extract.v2",
                "builtin_version": "powercontext.atomic-memory.extract.coding.v2",
                "instructions_sha256": "a" * 64,
                "compiled_digest": "c" * 64,
            },
            "conversation": {
                "key": "atomic_memory.extract",
                "definition_version": "powercontext.atomic-memory.extract.v2",
                "builtin_version": "powercontext.atomic-memory.extract.conversation.v2",
                "instructions_sha256": "b" * 64,
                "compiled_digest": "d" * 64,
            },
        },
        "provider": {
            "provider": "fixture",
            "model": "fixture:model",
            "base_url_configured": False,
            "base_url_sha256": None,
            "header_names": [],
            "model_settings": {},
            "model_settings_sha256": "e" * 64,
            "timeout_seconds": 30,
            "max_requests": 2,
        },
    }


def test_all_empty_extractor_cannot_pass() -> None:
    loaded = load_manifest()
    observations = {
        case.case_id: CaseObservation(case_id=case.case_id, status="succeeded") for case in loaded.manifest.cases
    }

    report = build_report(loaded, observations, _run_manifest(loaded))

    atomic_count = sum(case.expected.disposition == "atomic_memory" for case in loaded.manifest.cases)
    assert report["status"] == "failed"
    assert report["factual_retention"] == {
        "passed": 0,
        "failed": atomic_count,
        "unobserved": 0,
        "total": atomic_count,
        "false_rejections": atomic_count,
    }
    assert report["instruction_leakage"]["leaks"] == 0
    assert report["instruction_leakage"]["passed"] == len(loaded.manifest.cases)


def test_report_and_markdown_never_include_private_candidate_content_or_case_ids() -> None:
    loaded = load_manifest()
    observations = {
        case.case_id: CaseObservation(
            case_id=case.case_id,
            status="succeeded",
            candidates=(
                CandidateObservation(kind="fact", text=_SENTINEL, evidence_ids=(case.evidence[0].evidence_id,)),
            ),
        )
        for case in loaded.manifest.cases
    }

    report = build_report(loaded, observations, _run_manifest(loaded))
    serialized = json.dumps(report, ensure_ascii=False)
    markdown = render_markdown(report)

    assert _SENTINEL not in serialized
    assert _SENTINEL not in markdown
    assert all(case.case_id not in serialized for case in loaded.manifest.cases)
    assert report["content_policy"] == "aggregate_only_no_evidence_or_generated_candidate_text"


def test_report_redacts_content_bearing_settings_from_an_old_run_manifest() -> None:
    loaded = load_manifest()
    run_manifest = _run_manifest(loaded)
    provider = run_manifest["provider"]
    assert isinstance(provider, dict)
    provider["model_settings"] = {
        "openai_prediction": {"content": _SETTINGS_SENTINEL},
        "temperature": _SETTINGS_SENTINEL,
    }

    report = build_report(loaded, {}, run_manifest)
    serialized = json.dumps(report, ensure_ascii=False)
    settings = report["provider"]["model_settings"]

    assert _SETTINGS_SENTINEL not in serialized
    assert set(settings["openai_prediction"]) == {"redacted_sha256"}
    assert set(settings["temperature"]) == {"redacted_sha256"}


def test_report_rejects_non_mapping_model_settings() -> None:
    loaded = load_manifest()
    run_manifest = _run_manifest(loaded)
    provider = run_manifest["provider"]
    assert isinstance(provider, dict)
    provider["model_settings"] = _SETTINGS_SENTINEL

    with pytest.raises(ValueError, match="model_settings"):
        build_report(loaded, {}, run_manifest)


def test_generation_errors_make_the_report_incomplete_not_safe() -> None:
    loaded = load_manifest()
    observations = {
        case.case_id: CaseObservation(case_id=case.case_id, status="failed", error_class="ProviderUnavailable")
        for case in loaded.manifest.cases
    }

    report = build_report(loaded, observations, _run_manifest(loaded))

    assert report["status"] == "incomplete"
    assert report["instruction_leakage"]["passed"] == 0
    assert report["instruction_leakage"]["unobserved"] == len(loaded.manifest.cases)
    assert report["factual_retention"]["false_rejections"] == 0
    assert report["factual_retention"]["unobserved"] == sum(
        case.expected.disposition == "atomic_memory" for case in loaded.manifest.cases
    )
    assert report["failure_classes"]["generation_error"] == len(loaded.manifest.cases)
