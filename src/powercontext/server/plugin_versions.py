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

"""Read-only version snapshots of plugin directories.

Deployers may change a plugin directory at any time, so no process imports from it.
Once a directory's content digest is stable, core copies it to
``<root>/<plugin>/<digest>/`` and every check, host process, and Worker imports from
that read-only copy, so one call never mixes two versions of the code.
"""

from __future__ import annotations

import shutil
import stat
from pathlib import Path
from uuid import uuid4

from powercontext.builtin.plugins.manifest import code_fingerprint

_FILE_MODE = 0o444
_DIRECTORY_MODE = 0o555


def content_digest(directory: Path) -> str:
    return code_fingerprint(directory)


def make_snapshot(source: Path, root: Path, name: str, digest: str) -> Path | None:
    """Copy ``source`` into a read-only snapshot named by ``digest``.

    Returns None when the directory changed while it was copied; the next scan retries.
    """

    target = root / name / digest
    if target.is_dir():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".tmp-{uuid4().hex}"
    # Symbolic links stay links: page serving still refuses those that lead outside the plugin.
    shutil.copytree(source, staging, ignore=shutil.ignore_patterns("__pycache__"), symlinks=True)
    if code_fingerprint(staging) != digest:
        remove_snapshot(staging)
        return None
    for path in sorted(staging.rglob("*"), reverse=True):
        # chmod follows links, which may lead outside the snapshot.
        if not path.is_symlink():
            path.chmod(_DIRECTORY_MODE if path.is_dir() else _FILE_MODE)
    staging.chmod(_DIRECTORY_MODE)
    try:
        staging.rename(target)
    except OSError:
        # Another Server process on this machine made the same snapshot first.
        remove_snapshot(staging)
        if not target.is_dir():
            raise
    return target


def remove_snapshot(path: Path) -> None:
    if not path.exists():
        return
    # Deleting an entry needs a writable parent; read-only files themselves can be unlinked.
    for directory in (path, *(item for item in path.rglob("*") if item.is_dir() and not item.is_symlink())):
        directory.chmod(stat.S_IRWXU)
    shutil.rmtree(path)


def remove_stale_snapshots(root: Path, *, older_than: float, keep: frozenset[Path]) -> None:
    """At startup, remove snapshots and leftovers older than this process that are no current version."""

    if not root.is_dir():
        return
    for plugin in root.iterdir():
        if not plugin.is_dir():
            continue
        for snapshot in plugin.iterdir():
            if snapshot.resolve() in keep:
                continue
            try:
                if snapshot.stat().st_mtime < older_than:
                    remove_snapshot(snapshot)
            except OSError:
                continue
