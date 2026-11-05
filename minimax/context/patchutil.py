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


# Header lines a model may put between `diff --git` and the first hunk.
_GIT_METADATA = (
    "index ", "new file mode ", "deleted file mode ", "old mode ", "new mode ",
    "similarity index ", "dissimilarity index ", "rename from ", "rename to ",
    "copy from ", "copy to ",
)


def normalize(patch: str) -> str:
    """Add the ``---``/``+++`` file headers git wants when a model omits them.

    Several models emit a correct `diff --git` line and correct hunks but skip the
    two header lines, which makes `git apply` fail with "git diff header lacks
    filename information". The paths are already in the `diff --git` line, so the
    headers are derived from it rather than guessed.
    """
    lines = patch.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        out.append(line)
        match = DIFF_HEADER.match(line)
        if match:
            look = index + 1
            while look < len(lines) and lines[look].startswith(_GIT_METADATA):
                out.append(lines[look])
                look += 1
            if look < len(lines) and not lines[look].startswith("--- "):
                out.append(f"--- a/{match.group(1)}")
                out.append(f"+++ b/{match.group(2)}")
            index = look
            continue
        index += 1
    text = "\n".join(out)
    return text if text.endswith("\n") else text + "\n"


def measure(patch: str) -> dict:
    added = sum(1 for line in patch.splitlines()
                if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in patch.splitlines()
                  if line.startswith("-") and not line.startswith("---"))
    return {"files": files_in(patch), "added_lines": added, "removed_lines": removed}


# Where a model proposal is allowed to touch files. Everything else is rejected before
# `git apply` runs: an answer that invents `placeholder` must not create it in the tree.
ALLOWED_PREFIXES = (
    "src/sql/",
    "src/query/",
    "tools/deploy/mysql_test/test_suite/array/",
)


def validate_paths(patch: str, allowed: tuple[str, ...] = ALLOWED_PREFIXES) -> tuple[bool, list[str]]:
    """Return (ok, offending paths) for the files a patch would create or modify."""
    offenders = [path for path in files_in(patch) if not path.startswith(allowed)]
    return (not offenders), offenders


def reject_reason(patch: str, worktree: Path | None = None) -> str:
    """Explain why a proposal is unusable, or return '' when it looks acceptable.

    Checks, in order: the patch names files, every file is inside an allowed subtree,
    and existing files it modifies are actually present in the checkout.
    """
    ok, offenders = validate_paths(patch)
    if not ok:
        return (
            "the proposal touches paths outside the task scope: "
            + ", ".join(offenders)
            + "; allowed prefixes are " + ", ".join(ALLOWED_PREFIXES)
        )
    if worktree is not None:
        missing = [path for path in files_in(patch) if not (worktree / path).is_file()]
        if missing:
            return f"the proposal modifies files that do not exist in the checkout: {missing}"
    return ""


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
