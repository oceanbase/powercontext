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

"""Snapshot manifest self-tests: determinism, no absolute home paths, untracked coverage."""

from __future__ import annotations

# ruff: noqa: S101  (pytest assertions are the point of these tests)
import json
import sys
from pathlib import Path

HARNESS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS_ROOT))

import snapshot  # noqa: E402


def test_snapshot_is_deterministic() -> None:
    first = snapshot.build()
    second = snapshot.build()
    assert first["snapshot_id"] == second["snapshot_id"]
    assert first["snapshot_id"] == snapshot.sha256(
        json.dumps(
            {key: value for key, value in first.items() if key != "snapshot_id"},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    )


def test_snapshot_records_repository_identity_without_absolute_paths() -> None:
    manifest = snapshot.build()
    assert len(manifest["repository"]["head"]) == 40
    assert manifest["repository"]["root_name"] == snapshot.REPO_ROOT.name
    serialised = json.dumps(manifest, sort_keys=True)
    assert str(Path.home()) not in serialised
    # Paths are repository-relative, so a reviewer can reproduce them in their own checkout.
    assert all(not Path(entry["path"]).is_absolute() for entry in manifest["untracked"])


def test_snapshot_describes_untracked_files_by_hash() -> None:
    manifest = snapshot.build()
    for entry in manifest["untracked"]:
        if entry["type"] == "file":
            assert len(entry["sha256"]) == 64
            assert entry["bytes"] >= 0
            assert entry["mode"].startswith("0o")
