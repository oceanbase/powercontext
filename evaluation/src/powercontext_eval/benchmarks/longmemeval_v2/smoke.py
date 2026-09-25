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

"""Create the bounded, model-free input artifacts for a LongMemEval-V2 smoke run."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from powercontext_eval.benchmarks.longmemeval_v2.catalog import (
    RUN_INPUT_MANIFEST_SCHEMA,
    UPSTREAM_HARNESS_COMMIT,
    UPSTREAM_REPOSITORY,
    LongMemEvalV2Catalog,
    LongMemEvalV2EnvironmentError,
    LongMemEvalV2InputError,
    load_dataset_lock,
    load_smoke_manifest,
    validate_harness_checkout,
)


@dataclass(frozen=True)
class PreparedSmokeRun:
    """The inspectable artifacts emitted before model-backed evaluation begins."""

    output_dir: Path
    manifest_path: Path
    subset_path: Path


def prepare_smoke_run(
    *,
    data_root: Path,
    dataset_lock: Path,
    harness_root: Path,
    smoke_manifest: Path,
    output_dir: Path,
) -> PreparedSmokeRun:
    """Validate one fixed subset and create non-overwritable smoke input artifacts."""

    lock = load_dataset_lock(dataset_lock)
    selection = load_smoke_manifest(smoke_manifest)
    if selection.tier != lock.tier:
        raise LongMemEvalV2InputError("Smoke manifest tier does not match the dataset lock")
    validate_harness_checkout(harness_root)
    catalog = LongMemEvalV2Catalog.load(data_root, tier=selection.tier, expected_digests=lock.file_digests)
    validated = catalog.select_smoke(selection.cases)
    lock_content_digest = _content_digest(dataset_lock, "dataset lock")
    smoke_content_digest = _content_digest(smoke_manifest, "smoke manifest")
    try:
        output_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise LongMemEvalV2EnvironmentError(f"Refusing to overwrite smoke artifacts: {output_dir}") from error
    except OSError as error:
        raise LongMemEvalV2EnvironmentError(f"Cannot create smoke artifact directory: {output_dir}") from error

    subset_path = output_dir / "subset.json"
    manifest_path = output_dir / "manifest.json"
    subset = validated.as_run_artifact_json()
    _write_json(subset_path, subset)
    _write_json(
        manifest_path,
        {
            "schema": RUN_INPUT_MANIFEST_SCHEMA,
            "classification": "smoke-subset",
            "upstream": {
                "harness_commit": UPSTREAM_HARNESS_COMMIT,
                "repository": UPSTREAM_REPOSITORY,
            },
            "dataset": {
                "files": dict(catalog.input_digests),
                "revision": lock.dataset_revision,
                "tier": catalog.tier,
            },
            "dataset_lock": {"content_sha256": lock_content_digest},
            "smoke_manifest": {
                "content_sha256": smoke_content_digest,
                "subset_sha256": _content_digest(subset_path, "subset artifact"),
            },
        },
    )
    return PreparedSmokeRun(output_dir=output_dir, manifest_path=manifest_path, subset_path=subset_path)


def _write_json(path: Path, value: object) -> None:
    try:
        path.write_text(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError as error:
        raise LongMemEvalV2EnvironmentError(f"Cannot write smoke artifact: {path}") from error


def _content_digest(path: Path, label: str) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise LongMemEvalV2EnvironmentError(f"Cannot read LongMemEval-V2 {label}: {path}") from error
