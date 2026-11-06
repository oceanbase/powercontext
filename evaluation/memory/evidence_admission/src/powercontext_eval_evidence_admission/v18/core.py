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

"""Freeze native generation and prepare content-safe, stage-separated comparisons."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import unicodedata
from collections import Counter, defaultdict
from dataclasses import replace
from importlib.metadata import version
from pathlib import Path

from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryExtractionOutput

from ..admission import token_count
from ..manifests import EvaluationError, file_hash, load_suite, read_json
from ..models import digest
from ..runner import INSTRUCTIONS, run_sample
from ..v6.workflow import InputProbe
from ..v7.fidelity import quote_inventory
from ..v10.scoring import ground_truth, supported_atom
from ..v13.freeze import REPOSITORY

ROOT = Path(__file__).resolve().parent
PACKAGE = ROOT.parents[2]
ARMS = ("complete-projection-v1", "window-reduction-v1", "bounded-fragments-v3")
DATASETS = ("synthetic-v1", "validation-v2", "validation-v3")


def contract():
    return read_json(ROOT / "contract.json")


def suite_for(dataset):
    original = load_suite(experiment_version="v5", dataset=dataset)
    # Keep selection algorithms/parameters; use native instructions and the native output schema.
    # No citation rewriting, copy protocol, unit index, or additional instruction cost protocol.
    experiment = original.experiment.model_copy(
        update={"arms": ARMS, "citation_protocol": None, "selection_cost_protocol": None, "citation_encoding": None}
    )
    return replace(original, experiment=experiment)


def identity():
    paths = set((REPOSITORY / "src/powercontext").rglob("*.py"))
    for directory in (PACKAGE / "src", PACKAGE / "fixtures", PACKAGE / "manifests"):
        paths.update(p for p in directory.rglob("*") if p.suffix in {".py", ".json"})
    paths.update((PACKAGE / "tests").glob("test_v18*.py"))
    paths.update(
        REPOSITORY / name for name in ("pyproject.toml", "uv.lock", "evaluation/pyproject.toml", "evaluation/uv.lock")
    )
    paths.add(PACKAGE / "V18_PROTOCOL.zh.md")
    return {
        "contract": contract(),
        "instructions_hash": digest(INSTRUCTIONS),
        "output_schema_hash": digest(AtomicMemoryExtractionOutput.model_json_schema()),
        "files": {p.relative_to(REPOSITORY).as_posix(): file_hash(p) for p in sorted(paths)},
        "sdk_versions": {name: version(name) for name in ("pydantic-ai-slim", "openai", "httpx", "pydantic")},
        "python": platform.python_version(),
        "unicode_database_version": unicodedata.unidata_version,
    }


def committed_freeze():
    snapshot = identity()
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPOSITORY, text=True).strip()
    algorithm = subprocess.check_output(["git", "rev-parse", "--show-object-format"], cwd=REPOSITORY, text=True).strip()
    listing = subprocess.check_output(["git", "ls-tree", "-r", "-z", revision], cwd=REPOSITORY)
    blobs = {}
    for entry in listing.split(b"\0"):
        if entry:
            metadata, name = entry.split(b"\t", 1)
            blobs[name.decode()] = metadata.split()[2].decode()
    for name in snapshot["files"]:
        data = (REPOSITORY / name).read_bytes()
        actual = hashlib.new(algorithm, b"blob " + str(len(data)).encode() + b"\0" + data, usedforsecurity=False)
        if blobs.get(name) != actual.hexdigest():
            raise EvaluationError("v18_dependency_not_committed_at_HEAD")
    return {"revision": revision, "identity_hash": digest(snapshot), "verified": True}


def dump(path, value, *, new=False):
    """Durable private state; callers explicitly export only the public whitelist."""
    import os

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if new and path.exists():
        raise EvaluationError("v18_output_exists")
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def sample_key(dataset, case, arm, budget, repetition):
    return digest({"dataset": dataset, "case": case.case_id, "arm": arm, "budget": budget, "repetition": repetition})


async def prepare_one(dataset, case, arm, budget, repetition):
    probe = InputProbe()
    sample = await run_sample(suite_for(dataset), case, arm, budget, repetition, generator=probe)
    # The probe is not generation evidence. Preserve partial-window input on budget rejection.
    sample = sample.model_copy(
        update={
            "status": "prepared" if sample.status == "generated" else sample.status,
            "generation": None,
            "output_hashes": (),
            "generation_calls": 0,
            "requests": 0,
            "generation_seconds": 0,
            "provider_input_tokens": None,
            "provider_output_tokens": None,
        }
    )
    if any(token_count(v, INSTRUCTIONS) > budget for v in probe.inputs):
        raise EvaluationError("v18_prepared_input_exceeds_native_estimator_budget")
    if sample.memory_mutations or sample.published_cursor:
        raise EvaluationError("v18_unexpected_production_mutation")
    quotes = tuple(q for value in probe.inputs for q in quote_inventory(value, sample.supports))
    admitted = {a["id"] for a in ground_truth(case) if supported_atom(a, quotes)} if quotes else set()
    row = {
        "row_id": sample_key(dataset, case, arm, budget, repetition),
        "dataset": dataset,
        "case": case.case_id,
        "arm": arm,
        "budget": budget,
        "repetition": repetition,
        "sample": sample.model_dump(mode="json"),
        "required_atom_hashes": sorted(digest(a["id"]) for a in ground_truth(case)),
        "admitted_atom_hashes": sorted(digest(a) for a in admitted),
        "planned_calls": len(probe.inputs),
        "input_estimated_tokens": [token_count(v, INSTRUCTIONS) for v in probe.inputs],
    }
    return row, probe.inputs


def comparable(row):
    sample = row["sample"]
    return {
        k: v
        for k, v in sample.items()
        if k not in {"repetition", "sample_id", "materialization_seconds", "admission_seconds", "cancellation_seconds"}
    }


async def prepare(directory, *, progress=None):
    if directory.exists():
        raise EvaluationError("v18_session_exists")
    frozen = identity()
    directory.mkdir(parents=True, mode=0o700)
    rows = []
    # Interleave arms within each fixture/budget/replay; a budget stop never silently drops a whole control arm.
    for dataset in DATASETS:
        suite = suite_for(dataset)
        for case in suite.cases:
            for budget in suite.experiment.budgets:
                for repetition in range(suite.experiment.repetitions):
                    for arm in ARMS:
                        row, values = await prepare_one(dataset, case, arm, budget, repetition)
                        rows.append(row)
                        dump(
                            directory / "inputs" / (row["row_id"] + ".json"),
                            [v.model_dump(mode="json") for v in values],
                            new=True,
                        )
                    if progress is not None:
                        progress({"prepared": len(rows), "dataset": dataset})
    if frozen != identity():
        raise EvaluationError("v18_identity_changed_during_preparation")
    groups = defaultdict(list)
    for row in rows:
        groups[(row["dataset"], row["case"], row["arm"], row["budget"])].append(row)
    nondeterminism = sum(len({digest(comparable(row)) for row in group}) != 1 for group in groups.values())
    # Source IDs/content/materialization/projection identities must match across paired arms.
    pairs = defaultdict(list)
    for row in rows:
        if row["sample"]["status"] not in {"cancelled", "unavailable"}:
            pairs[(row["dataset"], row["case"], row["budget"], row["repetition"])].append(row)
    identity_mismatches = sum(
        len({digest({k: r["sample"][k] for k in ("source_hashes", "materializations", "projections")}) for r in group})
        != 1
        for group in pairs.values()
    )
    manifest = {
        "schema": "evidence-admission.v18.preparation.v1",
        "identity": frozen,
        "identity_hash": digest(frozen),
        "expected_rows": 972,
        "rows": rows,
        "nondeterministic_groups": nondeterminism,
        "paired_identity_mismatches": identity_mismatches,
        "reliability_passed": len(rows) == 972 and nondeterminism == 0 and identity_mismatches == 0,
        "generation_performed": False,
    }
    dump(directory / "preparation.json", manifest, new=True)
    dump(
        directory / "state.json", {"state": "prepared", "next_row": 0, "records": [], "known_charge_cny": "0"}, new=True
    )
    return offline_summary(manifest)


def offline_summary(manifest):
    counters = defaultdict(Counter)
    for row in manifest["rows"]:
        c = counters[row["arm"]]
        c["rows"] += 1
        c[row["sample"]["status"]] += 1
        c["planned_calls"] += row["planned_calls"]
        c["estimated_input_tokens"] += sum(row["input_estimated_tokens"])
        prep = row["sample"]["preparation"]
        if prep and prep["required_content_fits"]:
            c["eligible_required_claims"] += prep["required_count"]
            c["eligible_admitted_claims"] += len(prep["retained_claim_hashes"])
            c["eligible_required_atoms"] += len(row["required_atom_hashes"])
            c["eligible_admitted_atoms"] += len(row["admitted_atom_hashes"])
    return {
        "schema": "evidence-admission.v18.offline-summary.v1",
        "identity_hash": manifest["identity_hash"],
        "rows": len(manifest["rows"]),
        "arms": dict(counters),
        "reliability_passed": manifest["reliability_passed"],
        "nondeterministic_groups": manifest["nondeterministic_groups"],
        "paired_identity_mismatches": manifest["paired_identity_mismatches"],
        "generation_performed": False,
        "decision": "insufficient_evidence",
    }
