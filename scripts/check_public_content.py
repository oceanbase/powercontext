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

"""Reject corporate intranet document URLs in public repository content.

The written rules live in ``CONTRIBUTING.md``, ``AGENTS.md`` and
``docs/{en,zh}/rfcs/README.md``. This checker covers the part that can be
detected mechanically: a URL whose host belongs to a corporate intranet
domain. External customer names cannot be matched generically, so the pull
request template keeps a manual checklist for them.

Only exact intranet hostnames are rejected. Generic suffixes such as
``.internal`` are deliberately not matched: this repository uses hosts like
``*.internal.example`` and ``host.docker.internal`` in legitimate examples.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

INTERNAL_HOST_SUFFIXES = (
    "antfin.com",
    "alibaba-inc.com",
    "alipay-inc.com",
    "aliyun-inc.com",
)
MAX_FILE_BYTES = 4 * 1024 * 1024
# Only URL-authority characters belong in the host capture. A permissive
# "anything but ASCII punctuation" class also swallows CJK punctuation,
# full-width brackets or quotes, and Markdown backticks, which commonly follow
# a bare URL in Chinese docs and inline code. Those characters would become
# part of the host and defeat the suffix check, silently accepting the link.
URL_RE = re.compile(r"https?://([A-Za-z0-9._:@-]+)", re.IGNORECASE)


def is_internal_host(host: str) -> bool:
    """Return whether a URL authority points at a corporate intranet domain."""
    hostname = host.rsplit("@", 1)[-1].split(":", 1)[0].lower().rstrip(".")
    return any(hostname == suffix or hostname.endswith("." + suffix) for suffix in INTERNAL_HOST_SUFFIXES)


def iter_lines(path: Path) -> Iterator[tuple[int, str]]:
    """Yield numbered lines from a text file, skipping binaries and large blobs."""
    try:
        raw = path.read_bytes()
    except OSError:
        return
    if len(raw) > MAX_FILE_BYTES or b"\0" in raw[:8192]:
        return
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return
    yield from enumerate(text.splitlines(), start=1)


def tracked_files() -> list[Path]:
    """List every file git tracks, using NUL separators for safe path handling."""
    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git executable not found on PATH")  # noqa: TRY003 -- actionable operator diagnostic
    result = subprocess.run(  # noqa: S603 -- fixed argv resolved from PATH, no shell
        [git, "ls-files", "-z"],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(detail or f"git ls-files exited with {result.returncode}")
    return [Path(item) for item in result.stdout.decode("utf-8").split("\0") if item]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="files to check, as passed by pre-commit")
    parser.add_argument("--all", action="store_true", help="check every tracked file in the repository")
    args = parser.parse_args(argv)
    if args.all and args.paths:
        parser.error("pass either --all or explicit file paths, not both")

    try:
        files = tracked_files() if args.all else [Path(item) for item in args.paths]
    except RuntimeError as exc:
        print(f"cannot list tracked files: {exc}", file=sys.stderr)
        return 2

    findings: list[str] = []
    checked = 0
    for path in sorted(set(files)):
        if not path.is_file():
            continue
        checked += 1
        for number, line in iter_lines(path):
            for match in URL_RE.finditer(line):
                if is_internal_host(match.group(1)):
                    findings.append(
                        f"{path.as_posix()}:{number}: internal document URL is not allowed in public "
                        f"content: {match.group(0)}"
                    )

    if findings:
        for finding in findings:
            print(finding, file=sys.stderr)
        print(
            f"Found {len(findings)} internal document URL(s) in {checked} file(s). Reference a public "
            "GitHub issue, PR, RFC, or upstream documentation instead.",
            file=sys.stderr,
        )
        return 1
    print(f"OK: checked {checked} file(s), no internal document URLs found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
