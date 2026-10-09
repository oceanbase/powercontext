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

"""Synchronize release references; distribution versions still come from Git tags."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_PATTERN = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:(?:a|b|rc)(?:0|[1-9][0-9]*))?"
# Capture the whole token before validating it, including unsupported suffixes.
# A prose sentence's trailing period is not part of the token.
VERSION_CAPTURE = r"(?P<version>[0-9][\w+-]*(?:\.[\w+-]+)*)"
INSTALL_REFERENCES = (r"powercontext\[[^\]\n]+\]==", r"powercontext-v")

# Match current-release contexts only: migration thresholds, release history,
# independent plugin versions and third-party dependencies must retain their meaning.
REFERENCE_PREFIXES = {
    "openapi/powercontext.yaml": (r"^  version: ",),
    "docs/en/docs/get-started/install-and-run.md": (
        *INSTALL_REFERENCES,
        r"PowerContext ",
        r"package `",
        r"--version ",
    ),
    "docs/zh/docs/get-started/install-and-run.md": (
        *INSTALL_REFERENCES,
        r"PowerContext ",
        r"Python 包版本为 `",
        r"--version ",
    ),
    "integrations/dsh/plugins/powercontext/README.md": INSTALL_REFERENCES,
}


def normalize_version(value: str) -> str:
    """Accept the same tag prefixes and release suffixes as the release workflow."""
    version = value.removeprefix("powercontext-v") if value.startswith("powercontext-v") else value.removeprefix("v")
    if not re.fullmatch(VERSION_PATTERN, version):
        message = "VERSION must be X.Y.Z with optional aN, bN, or rcN suffix and optional v or powercontext-v prefix"
        raise ValueError(message)
    return version


def planned_updates(root: Path, version: str) -> dict[Path, str]:
    """Validate every configured reference before returning any pending writes."""

    def replace(match: re.Match[str]) -> str:
        normalize_version(match["version"])
        return match[0][: match.start("version") - match.start()] + version

    updates: dict[Path, str] = {}
    for relative_path, prefixes in REFERENCE_PREFIXES.items():
        path = root / relative_path
        original = path.read_text(encoding="utf-8")
        updated = original
        for prefix in prefixes:
            pattern = re.compile(prefix + VERSION_CAPTURE, re.MULTILINE)
            updated, count = pattern.subn(replace, updated)
            if count == 0:
                message = f"{relative_path}: no release reference matching {prefix!r}; update the release inventory"
                raise ValueError(message)
        if updated != original:
            updates[path] = updated
    return updates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="update source references (Make regenerates API code)")
    mode.add_argument("--check", action="store_true", help="fail if source references disagree")
    parser.add_argument(
        "--version", default=os.environ.get("VERSION"), help="target version or tag; defaults to VERSION"
    )
    args = parser.parse_args()
    try:
        if args.version:
            version = normalize_version(args.version)
        elif args.write:
            parser.error("VERSION is required; use make version-bump VERSION=X.Y.Z")
        else:
            contract = (ROOT / "openapi/powercontext.yaml").read_text(encoding="utf-8")
            match = re.search(rf"^  version: ({VERSION_PATTERN})$", contract, re.MULTILINE)
            if match is None:
                parser.error("cannot read the release version from openapi/powercontext.yaml")
            version = normalize_version(match[1])
        updates = planned_updates(ROOT, version)
    except (OSError, ValueError) as error:
        print(f"Release version error: {error}", file=sys.stderr)
        return 2

    for path, content in updates.items():
        relative_path = path.relative_to(ROOT)
        if args.write:
            path.write_text(content, encoding="utf-8")
            print(f"Updated {relative_path}")
        else:
            print(f"Release version mismatch: {relative_path} (expected {version})", file=sys.stderr)
    if args.check and updates:
        print(f"Run make version-bump VERSION={version} to synchronize release references.", file=sys.stderr)
        return 1
    print(f"Release references match {version}; distribution version is derived from Git tag powercontext-v{version}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
