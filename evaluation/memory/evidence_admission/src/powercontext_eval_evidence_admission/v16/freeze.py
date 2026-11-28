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

"""Content identity and actual HEAD verification, independent of historical protocol decisions."""

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
from ..v13.freeze import frozen_paths as previous_paths
from .codec import INSTRUCTIONS, TemplateOutput

ROOT = Path(__file__).resolve().parent
SUITE = ROOT.parents[2]


def frozen_paths():
    paths = set(previous_paths()) | {
        SUITE / "V16_PROTOCOL.zh.md",
        SUITE / "V15_PROTOCOL.zh.md",
    }
    return sorted(paths)


def identity():
    results = historical_results_root()
    historical = [
        results / "v15-history/summary.json",
        *sorted((results / "engineering-readiness").glob("*.json")),
    ]
    return {
        "contract": read_json(ROOT / "contract.json"),
        "files": {str(p.relative_to(REPOSITORY)): file_hash(p) for p in frozen_paths()},
        "historical_results": {str(p.relative_to(results)): file_hash(p) for p in historical},
        "instructions_hash": digest(INSTRUCTIONS),
        "schema_hash": digest(TemplateOutput.model_json_schema()),
        "sdk_versions": {p: version(p) for p in ("openai", "httpx", "pydantic-ai-slim")},
        "unicode_database_version": unicodedata.unidata_version,
        "production_authorized": False,
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
            raise EvaluationError("v16_dependency_not_committed_at_HEAD")
    return {"revision": revision, "content_identity_hash": digest(identity()), "verified": True}
