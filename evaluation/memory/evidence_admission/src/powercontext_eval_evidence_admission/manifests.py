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

"""Validate frozen content and configuration before any Source writes or model calls."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .models import ARMS, Case, Experiment, digest

PACKAGE_ROOT = Path(__file__).resolve().parents[2]
if not (PACKAGE_ROOT / "manifests/experiment-v1.json").is_file():
    PACKAGE_ROOT = Path(__file__).resolve().parent / "_data"


class EvaluationError(ValueError):
    """An evaluation contract failed; messages must never contain corpus content."""


LIVE_POLICY_SHA256 = "31cf973ca50fd2902dcdcca0775ad3dbe8574dfce2f82bdf9073e895d88719f5"


def live_policy(root: Path = PACKAGE_ROOT) -> dict:
    path = root / "manifests/live-policy-v1.json"
    if file_hash(path) != LIVE_POLICY_SHA256:
        raise EvaluationError("live_policy_freeze_mismatch")
    return read_json(path)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        raise EvaluationError("invalid_json") from None


def field_value(value: Any, pointer: str):
    for part in pointer.removeprefix("/").split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def case_catalog(case: Case) -> dict:
    sources = {source.source_id: source for source in case.sources}
    if len(sources) != len(case.sources) or not sources:
        raise EvaluationError("invalid_source_identities")
    claims = []
    claim_ids = set()
    for claim in (*case.required, *case.forbidden):
        if claim.claim_id in claim_ids or claim.source_id not in sources:
            raise EvaluationError("invalid_claim_identity")
        claim_ids.add(claim.claim_id)
        body, metadata = sources[claim.source_id].capture()
        material = {"content": {"content": body, "metadata": metadata}}
        try:
            text = field_value(material, claim.field)
        except (KeyError, TypeError, IndexError, ValueError):
            raise EvaluationError("invalid_claim_field") from None
        if (
            not isinstance(text, str)
            or claim.text not in text
            or (claim in case.required and text.count(claim.text) != 1)
        ):
            raise EvaluationError("claim_requires_one_exact_span")
        if any(not literal or literal not in claim.text for literal in claim.exact_literals):
            raise EvaluationError("literal_outside_claim")
        start = text.index(claim.text)
        claims.append(
            {
                "claim_hash": digest(claim.claim_id),
                "source_hash": digest(claim.source_id),
                "field_hash": digest(claim.field),
                "char_start": start,
                "char_end": start + len(claim.text),
                "byte_start": len(text[:start].encode()),
                "byte_end": len(text[: start + len(claim.text)].encode()),
                "required": claim in case.required,
            }
        )
    return {
        "case_id": case.case_id,
        "case_hash": digest(case.model_dump(mode="json")),
        "capture_hashes": [digest(source.capture()) for source in case.sources],
        "claims": claims,
    }


@dataclass(frozen=True)
class Suite:
    cases: tuple[Case, ...]
    experiment: Experiment
    decision: dict
    frozen_hashes: dict[str, str]
    private: bool = False
    root: Path = PACKAGE_ROOT
    dataset: str = "synthetic-v1"


def load_suite(root: Path = PACKAGE_ROOT, *, experiment_version: str = "v1", dataset: str = "synthetic-v1") -> Suite:
    if experiment_version not in {"v1", "v2", "v3"} or dataset not in {
        "synthetic-v1",
        "validation-v2",
        "validation-v3",
    }:
        raise EvaluationError("unsupported_experiment_or_dataset")
    version = experiment_version
    try:
        experiment = Experiment.model_validate(read_json(root / f"manifests/experiment-{version}.json"))
        fixture_path = root / "fixtures/synthetic-v1.json"
        catalog_path = root / "manifests/synthetic-v1.json"
        decision_path = root / "manifests/decision-policy-v1.json"
        hashes = {
            "fixture": file_hash(fixture_path),
            "catalog": file_hash(catalog_path),
            "decision": file_hash(decision_path),
        }
        expected = {
            "fixture": experiment.fixture_sha256,
            "catalog": experiment.catalog_sha256,
            "decision": experiment.decision_sha256,
        }
        if version in {"v2", "v3"} and experiment.v2:
            for name, directory in (("validation_fixture", "fixtures"), ("validation_catalog", "manifests")):
                hashes[name] = file_hash(root / directory / "validation-v2.json")
            expected.update(
                validation_fixture=experiment.v2.validation_fixture_sha256,
                validation_catalog=experiment.v2.validation_catalog_sha256,
            )
        elif version != "v1" or experiment.v2 or dataset != "synthetic-v1":
            raise EvaluationError("invalid_experiment_policy")
        if version == "v3" and experiment.v3:
            hashes["v3_validation_fixture"] = file_hash(root / "fixtures/validation-v3.json")
            hashes["v3_validation_catalog"] = file_hash(root / "manifests/validation-v3.json")
            expected.update(
                v3_validation_fixture=experiment.v3.validation_fixture_sha256,
                v3_validation_catalog=experiment.v3.validation_catalog_sha256,
            )
            if experiment.v3.family_turns != {"content": 2, "metadata": 1, "source_metadata": 1} or (
                experiment.v3.required_datasets != ("synthetic-v1", "validation-v2", "validation-v3")
            ):
                raise EvaluationError("invalid_v3_selection_policy")
        elif version == "v3" or experiment.v3 or dataset == "validation-v3":
            raise EvaluationError("invalid_experiment_policy")
        if hashes != expected:
            raise EvaluationError("frozen_manifest_hash_mismatch")
        if experiment.schema_version != f"evidence-admission.experiment.{version}":
            raise EvaluationError("experiment_version_mismatch")
        if dataset != "synthetic-v1":
            fixture_path = root / f"fixtures/{dataset}.json"
            catalog_path = root / f"manifests/{dataset}.json"
        raw = read_json(fixture_path)
        if raw["schema"] != "evidence-admission.fixtures.v1":
            raise EvaluationError("unsupported_fixture_schema")
        cases = tuple(Case.model_validate(item) for item in raw["cases"])
        catalog = {"schema": "evidence-admission.catalog.v1", "cases": [case_catalog(case) for case in cases]}
        if catalog != read_json(catalog_path) or len({case.case_id for case in cases}) != len(cases):
            raise EvaluationError("fixture_catalog_mismatch")
        arms = (*ARMS, "bounded-fragments-v2") if experiment.v2 else ARMS
        if experiment.v3:
            arms = (*arms, "bounded-fragments-v3")
        if tuple(experiment.arms) != arms or not experiment.budgets or any(b < 1 for b in experiment.budgets):
            raise EvaluationError("invalid_experiment_arms_or_budgets")
        decision = read_json(decision_path)
        if decision != DECISION_POLICY:
            raise EvaluationError("unsupported_decision_policy")
    except (OSError, KeyError, TypeError, ValidationError):
        raise EvaluationError("invalid_frozen_suite") from None
    hashes["experiment"] = file_hash(root / f"manifests/experiment-{version}.json")
    if version == "v3":
        live_policy(root)
        hashes["live_policy"] = file_hash(root / "manifests/live-policy-v1.json")
    return Suite(cases, experiment, decision, hashes, root=root, dataset=dataset)


DECISION_POLICY = {
    "schema": "evidence-admission.decision.v1",
    "required_retention": 1.0,
    "exact_literal_retention": 1.0,
    "critical_unsupported_max": 0,
    "false_attribution_max": 0,
    "invalid_support_max": 0,
    "unresolved_max": 0,
    "require_deterministic_input": True,
    "require_disclosed_omissions": True,
    "require_cancellation_without_publication": True,
    "require_reproducible_one_source_improvement": True,
    "require_live_coverage": True,
    "require_committed_freeze": True,
    "near_duplicate": {"version": "character-bigram-jaccard-v1", "threshold": 0.85},
}


def load_private_suite(path: Path, base: Suite) -> Suite:
    """Private manifests contain env selectors, hashes and labels, never embedded material."""
    manifest = read_json(path)
    if set(manifest) != {"schema", "cases", "decision_sha256"} or manifest["schema"] != "evidence-admission.private.v1":
        raise EvaluationError("invalid_private_manifest")
    if manifest["decision_sha256"] != base.experiment.decision_sha256:
        raise EvaluationError("private_threshold_mismatch")
    cases = []
    try:
        for entry in manifest["cases"]:
            if set(entry) != {"label", "file_env", "sha256"}:
                raise EvaluationError("invalid_private_entry")
            location = Path(os.environ[entry["file_env"]])
            if file_hash(location) != entry["sha256"]:
                raise EvaluationError("private_content_hash_mismatch")
            case = Case.model_validate(read_json(location))
            if case.case_id != entry["label"]:
                raise EvaluationError("private_label_mismatch")
            case_catalog(case)
            cases.append(case)
    except (OSError, KeyError, TypeError, ValidationError):
        raise EvaluationError("private_corpus_unavailable") from None
    if not cases or len({c.case_id for c in cases}) != len(cases):
        raise EvaluationError("invalid_private_case_identities")
    return Suite(
        tuple(cases),
        base.experiment,
        base.decision,
        {**base.frozen_hashes, "private": digest(manifest)},
        True,
        base.root,
        base.dataset,
    )
