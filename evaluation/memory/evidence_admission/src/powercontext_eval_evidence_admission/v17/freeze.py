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

"""Authenticate unchanged offline policy separately from the new execution protocol."""

from __future__ import annotations

import hashlib
import subprocess
import unicodedata
from importlib.metadata import version
from pathlib import Path

from ..historical_artifacts import historical_results_root
from ..manifests import EvaluationError, file_hash, read_json
from ..models import digest
from ..v13.freeze import REPOSITORY

ROOT = Path(__file__).resolve().parent
SUITE = ROOT.parents[2]


def contract():
    return read_json(ROOT / "contract.json")


def inherited():
    """Never recompute historical identity with the new files, or overwrite old outcomes."""
    policy = contract()
    results = historical_results_root() / "v16-offline"
    for name, key in (
        ("freeze", "baseline_freeze_sha256"),
        ("result", "baseline_result_sha256"),
        ("summary", "baseline_summary_sha256"),
    ):
        if file_hash(results / f"{name}.json") != policy[key]:
            raise EvaluationError("v17_baseline_receipt_changed")
    frozen = read_json(results / "freeze.json")["identity"]
    if digest(frozen) != policy["baseline_identity"]:
        raise EvaluationError("v17_baseline_identity_changed")
    for name, expected in frozen["files"].items():
        if file_hash(REPOSITORY / name) != expected:
            raise EvaluationError("v17_inherited_dependency_changed")
    if frozen["sdk_versions"] != {name: version(name) for name in frozen["sdk_versions"]}:
        raise EvaluationError("v17_sdk_versions_changed")
    if frozen["unicode_database_version"] != unicodedata.unidata_version:
        raise EvaluationError("v17_unicode_database_changed")
    result = read_json(results / "result.json")
    summary = read_json(results / "summary.json")
    if (
        not result["offline_gate_passed"]
        or result["minimal_manifest_gate_passed"]
        or not summary["offline_gate_passed"]
    ):
        raise EvaluationError("v17_historical_gates_changed")
    return frozen


def identity():
    baseline = inherited()
    paths = {REPOSITORY / name for name in baseline["files"]}
    paths.update(p for p in ROOT.iterdir() if p.suffix in {".py", ".json"})
    paths.update({SUITE / "V17_PROTOCOL.zh.md", SUITE / "tests/test_v17_live.py"})
    results = historical_results_root()
    return {
        "contract": contract(),
        "baseline_identity": digest(baseline),
        "files": {p.relative_to(REPOSITORY).as_posix(): file_hash(p) for p in sorted(paths)},
        "historical_results": {
            str(p.relative_to(results)): file_hash(p) for p in sorted((results / "v16-offline").glob("*.json"))
        },
        "sdk_versions": baseline["sdk_versions"],
        "production_authorized": False,
    }


def committed_freeze():
    snapshot = identity()
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPOSITORY, text=True).strip()
    algorithm = subprocess.check_output(["git", "rev-parse", "--show-object-format"], cwd=REPOSITORY, text=True).strip()
    listing = subprocess.check_output(["git", "ls-tree", "-r", "-z", revision], cwd=REPOSITORY)
    tree = {}
    for entry in listing.split(b"\0"):
        if entry:
            metadata, name = entry.split(b"\t", 1)
            tree[name.decode()] = metadata.split()[2].decode()
    for name in snapshot["files"]:
        data = (REPOSITORY / name).read_bytes()
        actual = hashlib.new(algorithm, b"blob " + str(len(data)).encode() + b"\0" + data, usedforsecurity=False)
        if tree.get(name) != actual.hexdigest():
            raise EvaluationError("v17_dependency_not_committed_at_HEAD")
    return {"revision": revision, "identity_hash": digest(snapshot), "verified": True}


def verify_comparisons(run_root):
    inherited()
    results_root = historical_results_root() / "v16-offline"
    results = read_json(results_root / "result.json")
    for label, receipt in zip(("primary", "validation", "additional"), results["artifact_integrity"], strict=True):
        directory = run_root / f"comparison-{label}"
        comparison = read_json(directory / "comparison.json")
        if (
            file_hash(directory / "comparison.json") != receipt["sha256"]
            or digest(comparison) != receipt["result_hash"]
            or read_json(directory / "COMPLETE.json")["result_hash"] != receipt["result_hash"]
            or comparison["dataset"] != receipt["dataset"]
            or len(comparison["samples"]) != receipt["samples"]
            or digest(comparison["v16_identity"]) != contract()["baseline_identity"]
        ):
            raise EvaluationError("v17_baseline_comparison_changed")
    return read_json(results_root / "minimal-readiness.json")
