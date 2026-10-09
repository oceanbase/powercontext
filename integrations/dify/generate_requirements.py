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

"""Generate or check the daemon's runtime requirements against the isolated uv lock."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMMAND = "uv run --project integrations/dify python integrations/dify/generate_requirements.py"
HEADER = f"# Generated from integrations/dify/uv.lock.\n# Regenerate with: {COMMAND}\n"


def render():
    exported = subprocess.run(
        [
            "uv",
            "export",
            "--locked",
            "--no-dev",
            "--no-hashes",
            "--no-emit-project",
            "--format",
            "requirements-txt",
            "--no-header",
        ],
        cwd=HERE,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    return HEADER + exported.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    path = HERE / "plugin/requirements.txt"
    content = render()
    if args.check:
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            raise SystemExit(f"Dify requirements require regeneration: {COMMAND}")
    else:
        path.write_text(content, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
