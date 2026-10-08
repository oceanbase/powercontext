#!/usr/bin/env python3
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

"""Vendor the Claude Code Skill from immutable Git blobs, or verify its pin."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
REPOSITORY = PROJECT.parents[1]
SOURCE = "integrations/claude-code/plugins/powercontext/skills/powercontext-project-context"
VENDOR = PROJECT / "vendor/powercontext-project-context"
LOCK = PROJECT / "skill-lock.json"


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(REPOSITORY), *args])  # noqa: S603,S607


def file_hashes(files: dict[str, bytes]) -> dict[str, str]:
    return {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}


def content_hash(hashes: dict[str, str]) -> str:
    manifest = "".join(f"{digest}  {name}\n" for name, digest in sorted(hashes.items()))
    return hashlib.sha256(manifest.encode()).hexdigest()


def skill_files(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def check() -> dict:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    hashes = file_hashes(skill_files(VENDOR))
    if lock["source_path"] != SOURCE or hashes != lock["files"] or content_hash(hashes) != lock["content_sha256"]:
        raise SystemExit("Skill vendor/pin mismatch; run sync_skill.py --revision <commit> and review the changes")
    # Git checkouts on Windows may use CRLF; the pin always hashes canonical Git LF bytes.
    current = {name: data.replace(b"\r\n", b"\n") for name, data in skill_files(REPOSITORY / SOURCE).items()}
    if file_hashes(current) != hashes:
        raise SystemExit("Packaged Skill changed since the pin; commit it, then run sync_skill.py --revision <commit>")
    return lock


def sync(revision: str) -> None:
    commit = git("rev-parse", "--verify", f"{revision}^{{commit}}").decode().strip()
    paths = git("ls-tree", "-r", "--name-only", commit, "--", SOURCE).decode().splitlines()
    files = {name[len(SOURCE) + 1 :]: git("cat-file", "blob", f"{commit}:{name}") for name in paths}
    if "SKILL.md" not in files:
        raise SystemExit("Revision does not contain the packaged Claude Code Skill")
    # Remove only obsolete files in this script-owned vendor directory.
    for old in VENDOR.rglob("*"):
        if old.is_file() and old.relative_to(VENDOR).as_posix() not in files:
            old.unlink()
    for name, data in files.items():
        target = VENDOR / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    hashes = file_hashes(files)
    lock = {
        "schema_version": 1,
        "source_path": SOURCE,
        "source_commit": commit,
        "files": hashes,
        "content_sha256": content_hash(hashes),
    }
    LOCK.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Pinned {len(files)} Skill files at {commit}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="verify vendor hashes and detect packaged Skill drift")
    mode.add_argument("--revision", help="explicit Git revision to vendor; default replays the existing pin")
    args = parser.parse_args()
    if args.check:
        lock = check()
        print(f"Skill pin verified: {lock['source_commit']} ({lock['content_sha256']})")
    else:
        revision = args.revision or json.loads(LOCK.read_text(encoding="utf-8"))["source_commit"]
        sync(revision)


if __name__ == "__main__":
    main()
