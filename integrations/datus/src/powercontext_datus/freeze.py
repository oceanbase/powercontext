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

"""Content snapshots for caller-owned, isolated benchmark inputs.

Snapshots detect drift; they are not a filesystem sandbox or a database snapshot.
"""

# ruff: noqa: TRY003 - bounded validation errors are part of this bridge's diagnostics.

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path


class IntegrityError(ValueError):
    """An input changed or cannot be captured unambiguously."""


def digest_json(value: object) -> str:
    """Hash a finite, deterministic JSON value without implementation-specific reprs."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


def snapshot(root: Path) -> dict[str, str]:
    """Capture regular files and modes; refuse links, special files and unstable reads."""
    if root.is_symlink() or not root.is_dir():
        raise IntegrityError("snapshot root must be a regular directory")
    files: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        before = path.lstat()
        if stat.S_ISDIR(before.st_mode):
            continue
        if not stat.S_ISREG(before.st_mode):
            raise IntegrityError("snapshot contains a link or special file")
        data = path.read_bytes()
        after = path.lstat()
        if (before.st_ino, before.st_mtime_ns, before.st_size, before.st_mode) != (
            after.st_ino,
            after.st_mtime_ns,
            after.st_size,
            after.st_mode,
        ):
            raise IntegrityError("snapshot input changed during capture")
        files[path.relative_to(root).as_posix()] = digest_json({
            "sha256": hashlib.sha256(data).hexdigest(),
            "mode": stat.S_IMODE(after.st_mode),
        })
    return files


def verify_snapshot(root: Path, expected: dict[str, str]) -> None:
    """Reject additions, removals, content changes and permission changes."""
    if snapshot(root) != expected:
        raise IntegrityError("frozen inputs drifted")
