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
from hashlib import sha256
from pathlib import Path

from evaluation.memory.atomic_extraction.manifest import load_manifest
from evaluation.memory.atomic_extraction.models import LiveRunManifest
from evaluation.memory.atomic_extraction.runner import prompt_run_identities
from powercontext.builtin.artifacts.memory import MemoryExtractionProfile


def test_committed_manifest_covers_required_languages_boundaries_and_literals() -> None:
    loaded = load_manifest()
    cases = {case.case_id: case for case in loaded.manifest.cases}

    assert len(loaded.sha256) == 64
    assert {case.profile for case in cases.values()} == set(MemoryExtractionProfile)
    assert {case.expected.disposition for case in cases.values()} == {
        "atomic_memory",
        "experience_or_skill",
        "reject",
    }
    assert {
        "english-imperative-preference",
        "chinese-imperative-preference",
        "quoted-malicious-instructions",
        "durable-project-decision",
        "chinese-system-constraint",
        "task-specific-procedure",
        "literal-values-remain-exact",
        "mixed-fact-and-procedure",
        "negative-control-small-talk",
    } <= cases.keys()

    literal_case = cases["literal-values-remain-exact"]
    assert {
        "`uv run pytest -q`",
        '`unicodedata.normalize("NFC", text)`',
        "`config/quality.toml`",
        "`line-length = 120`",
    } <= set(literal_case.expected.required_literals)
    assert all(case.expected.forbidden_output_fragments for case in cases.values())
    assert all(
        case.expected.required_subjects for case in cases.values() if case.expected.disposition == "atomic_memory"
    )


def test_manifest_atomic_cases_require_exact_supplied_evidence_ids() -> None:
    loaded = load_manifest()

    for case in loaded.manifest.cases:
        supplied = {item.evidence_id for item in case.evidence}
        required = set(case.expected.required_evidence_ids)
        if case.expected.disposition == "atomic_memory":
            assert required
            assert required <= supplied
        else:
            assert not required


def test_pinned_reports_match_the_current_dataset_and_prompt_contract() -> None:
    loaded = load_manifest()
    reports = Path(__file__).resolve().parents[1] / "reports"
    pinned = sorted(path for path in reports.iterdir() if (path / "run-manifest.json").is_file())

    assert pinned
    current_prompts = prompt_run_identities()
    qualified_current_contract = False
    for directory in pinned:
        manifest = LiveRunManifest.model_validate_json((directory / "run-manifest.json").read_text(encoding="utf-8"))
        report = json.loads((directory / "report.json").read_text(encoding="utf-8"))

        assert manifest.dataset.manifest_sha256 == loaded.sha256
        assert manifest.dataset.dataset_version == loaded.manifest.dataset_version
        assert manifest.dataset.case_count == len(loaded.manifest.cases)
        assert report["dataset"] == manifest.dataset.model_dump(mode="json")
        assert report["prompts"] == {
            profile.value: identity.model_dump(mode="json") for profile, identity in manifest.prompts.items()
        }
        assert report["status"] == "passed"
        qualified_current_contract |= manifest.prompts == current_prompts

    # Historical evidence keeps its original identities; it cannot qualify a
    # changed contract in place of a complete, passing current live run.
    assert qualified_current_contract


def test_archived_reports_remain_bound_to_their_recorded_run() -> None:
    archive = Path(__file__).resolve().parents[1] / "reports" / "archive"
    assert archive.is_dir()
    pinned = sorted(path for path in archive.iterdir() if (path / "run-manifest.json").is_file())

    assert pinned
    for directory in pinned:
        manifest = LiveRunManifest.model_validate_json((directory / "run-manifest.json").read_text(encoding="utf-8"))
        recorded = manifest.model_dump(mode="json")
        report = json.loads((directory / "report.json").read_text(encoding="utf-8"))

        assert report["run_id"] == manifest.run_id
        assert report["dataset"] == recorded["dataset"]
        assert report["prompts"] == recorded["prompts"]
        assert report["provider"] == recorded["provider"]
        encoded = json.dumps(recorded, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        assert report["run_manifest_sha256"] == sha256(encoded).hexdigest()
