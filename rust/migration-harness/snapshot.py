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

"""Deterministic delivery snapshot: identity of the tree a recording or replay belongs to.

The manifest follows the contract's snapshot recipe: repository identity and ``HEAD``, the raw
``git status --porcelain=v2 --untracked-files=all`` text, SHA-256 of the staged and unstaged binary
diffs, per-file type/mode/raw-bytes hashes of untracked files, and the toolchain identity that
affects the result. The manifest is canonical JSON (sorted keys, compact separators, no NaN) and its
own SHA-256 is the ``snapshot_id``. Absolute home directories and secrets are never recorded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import stat
import subprocess
import sys
import unicodedata
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:  # allow `python rust/migration-harness/snapshot.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from codec import SCHEMA_ID

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_VERSION = "2026-11-22.1"

# Resolve git once so the subprocess argv is an absolute path (no PATH-dependent partial path).
GIT = shutil.which("git") or "git"


def git(*arguments: str) -> str:
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell; PATH git by design
        [GIT, *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        message = f"git {' '.join(arguments)} failed: {completed.stderr.decode('utf-8', 'replace').strip()}"
        raise RuntimeError(message)
    return completed.stdout.decode("utf-8", "replace")


def git_bytes(*arguments: str) -> bytes:
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell; PATH git by design
        [GIT, *arguments],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        message = f"git {' '.join(arguments)} failed: {completed.stderr.decode('utf-8', 'replace').strip()}"
        raise RuntimeError(message)
    return completed.stdout


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _exclusion(output: Path | None) -> list[str]:
    """Return the git pathspec that keeps the snapshot output out of its own description.

    Writing the manifest immediately changes the tree it describes, so an output inside the
    repository would make every snapshot self-contradictory: its own untracked hash or tracked diff
    would be captured from the pre-write state. The path is excluded by pathspec rather than being
    filtered afterwards, so the staged, unstaged and untracked views all agree.
    """

    if output is None:
        return []
    try:
        relative = output.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return []
    return [f":(exclude){relative}"]


def untracked_entries(exclude: list[str] | None = None) -> list[dict[str, Any]]:
    """Describe every untracked file by relative path, type, mode and raw-byte hash."""

    listing = git("ls-files", "--others", "--exclude-standard", "-z", "--", ".", *(exclude or []))
    entries: list[dict[str, Any]] = []
    for relative in sorted(item for item in listing.split("\0") if item):
        path = REPO_ROOT / relative
        try:
            info = path.lstat()
        except OSError:
            entries.append({"path": relative, "type": "missing"})
            continue
        if stat.S_ISLNK(info.st_mode):
            entries.append({"path": relative, "type": "symlink", "target": path.readlink().as_posix()})
        elif path.is_file():
            entries.append({
                "path": relative,
                "type": "file",
                "mode": oct(stat.S_IMODE(info.st_mode)),
                "bytes": info.st_size,
                "sha256": sha256(path.read_bytes()),
            })
        else:
            entries.append({"path": relative, "type": "other", "mode": oct(stat.S_IMODE(info.st_mode))})
    return entries


def build(output: Path | None = None) -> dict[str, Any]:
    exclude = _exclusion(output)
    pathspec = ["--", ".", *exclude] if exclude else []
    status = git("status", "--porcelain=v2", "--untracked-files=all", *pathspec)
    staged = git_bytes("diff", "--cached", "--binary", "--no-ext-diff", *pathspec)
    unstaged = git_bytes("diff", "--binary", "--no-ext-diff", *pathspec)
    manifest: dict[str, Any] = {
        "schema": SCHEMA_ID,
        "record": "snapshot",
        "contract_version": CONTRACT_VERSION,
        "repository": {
            "root_name": REPO_ROOT.name,
            "head": git("rev-parse", "HEAD").strip(),
            "branch": git("rev-parse", "--abbrev-ref", "HEAD").strip(),
        },
        "status_porcelain_v2": status,
        "status_sha256": sha256(status.encode("utf-8")),
        "staged_diff_sha256": sha256(staged),
        "unstaged_diff_sha256": sha256(unstaged),
        "excluded_paths": exclude,
        "untracked": untracked_entries(exclude),
        "toolchain": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "unicode": unicodedata.unidata_version,
            "platform": sys.platform,
            "machine": platform.machine(),
        },
    }
    serialised = json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    manifest["snapshot_id"] = sha256(serialised.encode("utf-8"))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Write a deterministic delivery snapshot manifest.")
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    manifest = build(arguments.output)
    arguments.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"snapshot_id={manifest['snapshot_id']} untracked={len(manifest['untracked'])} -> {arguments.output}")


if __name__ == "__main__":
    main()
