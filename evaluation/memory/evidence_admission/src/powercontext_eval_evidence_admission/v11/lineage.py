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

"""Validate immutable historical content independently of execution Git metadata."""

from __future__ import annotations

from pathlib import Path

from ..manifests import EvaluationError, read_json
from ..models import digest
from ..v7.contract import identity as v7_identity
from ..v7.workflow import DATASETS, reference_run
from ..v8.contract import identity as v8_identity
from ..v9.contract import identity as v9_identity


def content_identity(value):
    """Only code-identity records have non-content revision/commit-certification fields."""
    if isinstance(value, list):
        return [content_identity(v) for v in value]
    if not isinstance(value, dict):
        return value
    metadata = {"revision", "frozen_committed"} if {"suite_code_hash", "runtime_code_hash"} <= value.keys() else set()
    return {k: content_identity(v) for k, v in value.items() if k not in metadata}


def historical_references(directory, baseline):
    results = {}
    for label, dataset in DATASETS.items():
        parent = directory / f"comparison-{label}"
        result = read_json(parent / "comparison.json")
        if (
            digest(result) != read_json(parent / "COMPLETE.json")["result_hash"]
            or digest(result) != baseline["baseline_comparison_hashes"][dataset]
            or result["dataset"] != dataset
            or content_identity(result["v9_identity"]) != content_identity(v9_identity())
        ):
            raise EvaluationError("v11_historical_content_or_identity_changed")
        _, manifest, references = reference_run(dataset, Path(result["reference_dir"]))
        ids = {r.sample_id for r in references}
        records = result["samples"]
        if (
            len(records) != len(ids)
            or {r["sample_id"] for r in records} != ids
            or result["reference_manifest_hash"] != digest(manifest)
        ):
            raise EvaluationError("v11_historical_sample_set_changed")
        for version, filename, key, current in (
            ("v7", "cost-comparison.json", "v7_identity", v7_identity),
            ("v8", "comparison.json", "v8_identity", v8_identity),
        ):
            ancestor_dir = Path(result["baseline_dirs"][version])
            ancestor = read_json(ancestor_dir / filename)
            if (
                digest(ancestor) != result["baseline_hashes"][version]
                or digest(ancestor) != read_json(ancestor_dir / "COMPLETE.json")["result_hash"]
                or content_identity(ancestor[key]) != content_identity(current())
                or ancestor["dataset"] != dataset
                or ancestor["reference_manifest_hash"] != digest(manifest)
                or len(ancestor["samples"]) != len(ids)
                or {r["sample_id"] for r in ancestor["samples"]} != ids
            ):
                raise EvaluationError("v11_historical_ancestor_changed")
        results[dataset] = result
    return results
