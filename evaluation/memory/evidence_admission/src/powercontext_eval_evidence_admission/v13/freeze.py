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

"""Content fingerprints and actual Git-blob verification before any credential access."""

from __future__ import annotations

import hashlib
import subprocess
from importlib.metadata import version
from pathlib import Path

import powercontext

from ..manifests import EvaluationError, file_hash, read_json
from ..models import digest
from ..v11.window import identity as window_identity

ROOT = Path(__file__).resolve().parent
REPOSITORY = next(p for p in ROOT.parents if (p / ".git").exists())


def frozen_paths():
    package = ROOT.parent
    suite = package.parents[1]
    paths = [p for p in package.rglob("*") if p.suffix in {".py", ".json"}]
    paths += list(Path(powercontext.__file__).resolve().parent.rglob("*.py"))
    paths += [p for folder in ("manifests", "fixtures") for p in (suite / folder).glob("*.json")]
    paths += [REPOSITORY / n for n in ("pyproject.toml", "uv.lock", "evaluation/pyproject.toml", "evaluation/uv.lock")]
    paths += [
        suite / n for n in ("V10_PROTOCOL.zh.md", "V11_PROTOCOL.zh.md", "V12_PROTOCOL.zh.md", "V13_PROTOCOL.zh.md")
    ]
    paths += [suite / "ruff.toml", suite / "results/v12-live/summary.json", suite.parent / "longmemeval_v2/ruff.toml"]
    paths += list((suite / "tests").glob("*.py"))
    return sorted(set(paths))


def identity():
    contract = read_json(ROOT / "contract.json")
    previous = ROOT.parents[2] / "results/v12-live/summary.json"
    if (
        file_hash(previous) != contract["historical_v12_result_sha256"]
        or contract["thinking"] != {"type": "disabled"}
        or contract["model_settings"] != {"temperature": 0, "extra_body": {"thinking": {"type": "disabled"}}}
        or contract["budget_carry_forward"]["source_sha256"] != file_hash(previous)
        or contract["budget_carry_forward"]["prior_known_upper_charge_cny"]
        != read_json(previous)["conditional_budget"]["combined_known_upper_charge_cny"]
        or contract["budget_carry_forward"]["remaining_conditional_cny"]
        != read_json(previous)["conditional_budget"]["remaining_conditional_budget_cny"]
    ):
        raise EvaluationError("v13_contract_or_budget_carry_forward_changed")
    return {
        "contract": read_json(ROOT / "contract.json"),
        "window_dependency": window_identity(),
        "files": {str(p.relative_to(REPOSITORY)): file_hash(p) for p in frozen_paths()},
        "sdk_versions": {p: version(p) for p in ("openai", "httpx", "pydantic-ai-slim")},
    }


def committed_freeze():
    """The index is insufficient: every dependency must equal a blob at actual HEAD."""
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPOSITORY, text=True).strip()
    object_format = subprocess.check_output(
        ["git", "rev-parse", "--show-object-format"], cwd=REPOSITORY, text=True
    ).strip()
    entries = subprocess.check_output(["git", "ls-tree", "-r", "-z", revision], cwd=REPOSITORY).split(b"\0")
    tree = {}
    for entry in entries:
        if entry:
            metadata, name = entry.split(b"\t", 1)
            tree[name.decode()] = metadata.split()[2].decode()
    for path in frozen_paths():
        raw = path.read_bytes()
        blob = hashlib.new(object_format, b"blob " + str(len(raw)).encode() + b"\0" + raw, usedforsecurity=False)
        if tree.get(path.relative_to(REPOSITORY).as_posix()) != blob.hexdigest():
            raise EvaluationError("v13_dependency_not_committed_at_HEAD")
    return {"revision": revision, "content_identity_hash": digest(identity()), "verified": True}
