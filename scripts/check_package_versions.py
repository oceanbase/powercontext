#!/usr/bin/env python3
"""Check that powermem and powermem-mcp are released as one version pair."""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROOT_PYPROJECT = ROOT / "pyproject.toml"
MCP_PYPROJECT = ROOT / "packages" / "powermem-mcp" / "pyproject.toml"


def read_project_version(path: Path) -> str:
    match = re.search(r'^version = "([^"]+)"', path.read_text(), re.MULTILINE)
    if not match:
        raise RuntimeError(f"Could not find project version in {path}")
    return match.group(1)


def main() -> int:
    root_version = read_project_version(ROOT_PYPROJECT)
    mcp_text = MCP_PYPROJECT.read_text()
    mcp_version = read_project_version(MCP_PYPROJECT)
    expected_dependency = f"powermem[mcp,seekdb]=={root_version}"

    errors: list[str] = []
    if mcp_version != root_version:
        errors.append(
            f"powermem-mcp version {mcp_version} does not match powermem {root_version}"
        )
    if expected_dependency not in mcp_text:
        errors.append(
            "powermem-mcp dependency must be pinned to "
            f"{expected_dependency!r}"
        )

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(f"Package versions aligned: powermem == powermem-mcp == {root_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
