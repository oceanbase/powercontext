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

"""Pin the packaged ZCode Skill's immutable Git bytes; detect checkout drift."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

PROJECT = Path(__file__).resolve().parent
REPOSITORY = PROJECT.parents[1]
SOURCE = "integrations/zcode/plugins/powercontext/skills/powercontext-project-context"
LOCK = PROJECT / "skill-lock.json"


def hashes(files: dict[str, bytes]) -> dict[str, str]:
    return {name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())}


def check() -> dict:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    source = REPOSITORY / SOURCE
    files = {
        path.relative_to(source).as_posix(): path.read_bytes().replace(b"\r\n", b"\n")
        for path in source.rglob("*")
        if path.is_file()
    }
    if lock.get("source_path") != SOURCE or hashes(files) != lock.get("files"):
        raise ValueError("skill_pin_mismatch")
    return lock


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--revision", help="Explicit committed Skill revision; a changed pin needs a new model run.")
    args = parser.parse_args()
    if args.check:
        lock = check()
    else:

        def git(*arguments: str) -> bytes:
            return subprocess.check_output(["git", "-C", str(REPOSITORY), *arguments])  # noqa: S603,S607

        revision = git("rev-parse", "--verify", f"{args.revision}^{{commit}}").decode().strip()
        paths = git("ls-tree", "-r", "--name-only", revision, "--", SOURCE).decode().splitlines()
        files = {name[len(SOURCE) + 1 :]: git("cat-file", "blob", f"{revision}:{name}") for name in paths}
        if "SKILL.md" not in files:
            raise ValueError("skill_missing_at_revision")
        lock = {"schema_version": 1, "source_path": SOURCE, "source_commit": revision, "files": hashes(files)}
        LOCK.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")
        check()
    print(f"ZCode Skill pin verified: {lock['source_commit']}")


if __name__ == "__main__":
    main()
