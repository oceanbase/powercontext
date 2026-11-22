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

"""Freeze the repository and explicitly selected build inputs without collecting the environment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path
from typing import Any

REPOSITORY = Path(__file__).resolve().parents[3]


def git(*args: str) -> bytes:
    executable = shutil.which("git")
    if executable is None:
        raise FileNotFoundError("git")
    return subprocess.check_output([executable, *args], cwd=REPOSITORY)


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--input", type=Path, action="append", default=[])
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(REPOSITORY):
        message = "Snapshot output must be outside the checkout to avoid self-reference"
        raise ValueError(message)
    files = []
    for raw in git("ls-files", "--others", "--exclude-standard", "-z").split(b"\0"):
        if not raw:
            continue
        relative = os.fsdecode(raw)
        path = REPOSITORY / relative
        symlink = path.is_symlink()
        files.append({
            "path": relative,
            "mode": oct(stat.S_IMODE(path.lstat().st_mode)),
            "type": "symlink" if symlink else "file",
            "sha256": sha(os.fsencode(os.readlink(path)) if symlink else path.read_bytes()),
        })
    manifest: dict[str, Any] = {
        "schema": 1,
        "contract": "PC-RUST-2740/2026-11-22.1",
        "repository": "oceanbase/powercontext",
        "head": git("rev-parse", "HEAD").decode().strip(),
        "status": git("status", "--porcelain=v2", "--untracked-files=all").decode(),
        "staged_sha256": sha(git("diff", "--cached", "--binary", "--no-ext-diff", "HEAD")),
        "unstaged_sha256": sha(git("diff", "--binary", "--no-ext-diff")),
        "untracked": sorted(files, key=lambda row: row["path"]),
        "submodule_gitlinks": [
            line for line in git("ls-files", "--stage").decode().splitlines() if line.startswith("160000 ")
        ],
        "submodule_status": git("submodule", "status").decode(),
        "environment": {
            name: os.environ.get(name)
            for name in (
                "UV_CACHE_DIR",
                "UV_PYTHON_INSTALL_DIR",
                "CARGO_HOME",
                "CARGO_TARGET_DIR",
                "PREK_HOME",
                "RUSTFLAGS",
                "CARGO_ENCODED_RUSTFLAGS",
                "PYO3_PYTHON",
                "CARGO_BUILD_TARGET",
                "PYTHONPATH",
            )
        },
        "explicit_inputs": [
            {"path": str(path.resolve()), "sha256": sha(path.read_bytes())} for path in sorted(args.input)
        ],
    }
    data = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    args.output.write_bytes(data)
    print(sha(data))


if __name__ == "__main__":
    main()
