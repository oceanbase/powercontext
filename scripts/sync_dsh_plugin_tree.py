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

"""Copy the standalone DSH plugin sources into integrations/dsh."""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "integrations" / "dsh" / "plugins" / "powercontext"
COPY_NAMES = (
    "src",
    "scripts",
    "tests",
    "package.json",
    "pnpm-lock.yaml",
    "tsconfig.json",
    "tsdown.config.ts",
    "vitest.config.ts",
    "cordis.patch.yml",
    "LICENSE",
)


def sync(source: Path) -> Path:
    if not source.is_dir():
        raise SystemExit(  # noqa: TRY003
            "Pass --source or set POWERCONTEXT_DSH_SOURCE to a plugin checkout."
        )
    DEST.mkdir(parents=True, exist_ok=True)
    for name in COPY_NAMES:
        origin = source / name
        if not origin.exists():
            continue
        target = DEST / name
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        if origin.is_dir():
            shutil.copytree(origin, target, ignore=shutil.ignore_patterns("node_modules", ".repro-*"))
        else:
            shutil.copy2(origin, target)
    (DEST / ".gitignore").write_text("node_modules/\n*.tgz\n", encoding="utf-8")
    return DEST


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(os.environ.get("POWERCONTEXT_DSH_SOURCE", "")),
    )
    args = parser.parse_args()
    print(sync(args.source))
