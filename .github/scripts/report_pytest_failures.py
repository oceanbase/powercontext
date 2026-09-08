#!/usr/bin/env python3
"""Expose pytest JUnit failures as public GitHub Actions annotations."""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def _escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main() -> int:
    report = Path(sys.argv[1])
    if not report.is_file():
        print(f"::error title=pytest report missing::{_escape(str(report))}")
        return 0

    root = ET.parse(report).getroot()  # noqa: S314 - parses a local pytest report
    for case in root.iter("testcase"):
        problem = next((case.find(kind) for kind in ("failure", "error") if case.find(kind) is not None), None)
        if problem is None:
            continue
        identity = ".".join(filter(None, (case.get("classname"), case.get("name"))))
        summary = (problem.get("message") or "pytest failed").strip()
        traceback = (problem.text or "").strip()
        detail = f"{summary}\n{traceback}" if traceback else summary
        print(f"::error title={_escape(identity)}::{_escape(detail[:8000])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
