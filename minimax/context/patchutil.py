"""Patch helpers shared by the agent driver and the operations layer.

Two behaviours are easy to get wrong on Windows and are therefore centralised:

* a patch handed to ``git apply`` must end with a newline, otherwise git reports
  ``corrupt patch at line N``; the file is normalised to LF before it is written;
* a proposal from a model may contain several fenced blocks or trailing prose, so the
  diff is extracted from the first ``diff --git`` header up to the closing fence.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

DIFF_HEADER = re.compile(r"^diff --git a/(\S+) b/(\S+)$", re.MULTILINE)


def extract_patch(text: str) -> str:
    """Return the unified diff block of an answer, ignoring prose around it."""
    matches = list(DIFF_HEADER.finditer(text))
    if not matches:
        raise ValueError("the answer contains no 'diff --git' block")
    block = text[matches[0].start():]
    fence = block.find("\n```")
    if fence != -1:
        block = block[:fence]
    block = block.replace("\r\n", "\n").replace("\r", "\n").rstrip("\n")
    return block + "\n"


def files_in(patch: str) -> list[str]:
    return [match.group(2) for match in DIFF_HEADER.finditer(patch)]


def measure(patch: str) -> dict:
    added = sum(1 for line in patch.splitlines()
                if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in patch.splitlines()
                  if line.startswith("-") and not line.startswith("---"))
    return {"files": files_in(patch), "added_lines": added, "removed_lines": removed}


def write_patch(path: Path, patch: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = patch.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.endswith("\n"):
        normalized += "\n"
    path.write_bytes(normalized.encode("utf-8"))
    return path


def git_apply(worktree: Path, patch_path: Path, check: bool = False) -> subprocess.CompletedProcess:
    command = ["git", "apply"]
    if check:
        command.append("--check")
    command.append(str(patch_path))
    return subprocess.run(command, cwd=str(worktree), text=True, capture_output=True)


def touched_files(worktree: Path) -> list[str]:
    result = subprocess.run(["git", "diff", "--name-only"], cwd=str(worktree),
                            text=True, capture_output=True)
    return [line for line in result.stdout.splitlines() if line]


def working_diff(worktree: Path) -> str:
    result = subprocess.run(["git", "diff"], cwd=str(worktree), text=True,
                            capture_output=True)
    return result.stdout


def diffstat(worktree: Path) -> str:
    result = subprocess.run(["git", "diff", "--stat"], cwd=str(worktree), text=True,
                            capture_output=True)
    return result.stdout
