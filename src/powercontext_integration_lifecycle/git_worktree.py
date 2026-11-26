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

"""Find the paths whose workspace bindings apply to a directory, so a linked Git worktree falls back to its checkout.

integrations/lifecycle/git-worktree.ts applies the same rule for the TypeScript and JavaScript hosts.
"""

from __future__ import annotations

import os
import subprocess
from shutil import which

# --path-format applies to the options after it, so the top level stays exactly what --show-toplevel alone prints.
# It needs Git 2.31 or later.
_ARGUMENTS = ("rev-parse", "--show-toplevel", "--path-format=absolute", "--git-common-dir")
# Older Git does not know --path-format and prints it back as an argument.
_UNKNOWN_FORMAT = "\n--path-format=absolute"


def workspace_paths(cwd: str, *, git_root: bool, timeout: float = 2.0) -> list[str]:
    """Return the paths whose workspace bindings apply to ``cwd``, the worktree's own first.

    The first path is the one the host has always keyed: with ``git_root``, the Git top level as Git prints it, or
    ``cwd`` outside a work tree; otherwise ``cwd`` itself. In a linked worktree of a non-bare repository, a second path
    follows from the main worktree: its root with ``git_root``, otherwise the path at ``cwd``'s position in it. Each
    host keys both paths the way it keys the first, so the second matches a binding made in the main checkout, and
    resolves them in this order, so the worktree's own binding wins. Binding and clearing use only the first.

    The main worktree is the parent of Git's common directory when that directory is named ``.git``; bare
    repositories, submodules, and other layouts get no second path.
    """

    top, common = _git_paths(cwd, timeout)
    first = (top or cwd) if git_root else cwd
    main = _main_worktree_path(cwd, top, common, git_root=git_root)
    return [first] if main is None else [first, main]


def _git_paths(cwd: str, timeout: float) -> tuple[str | None, str | None]:
    """Return the top level and the absolute common directory that Git reports for ``cwd``, each when it has one."""

    executable = which("git")
    if executable is None:
        return None, None
    try:
        completed = subprocess.run(  # noqa: S603 - git executable and arguments are integration-owned.
            [executable, *_ARGUMENTS],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    head, _, common = completed.stdout.rstrip("\n").rpartition("\n")
    if head.endswith(_UNKNOWN_FORMAT):
        return head[: -len(_UNKNOWN_FORMAT)].strip() or None, None
    return head.strip() or None, common or None


def _main_worktree_path(cwd: str, top: str | None, common: str | None, *, git_root: bool) -> str | None:
    if top is None or common is None or not os.path.isabs(common):
        return None
    common_directory = os.path.realpath(common)
    if os.path.basename(common_directory) != ".git":
        return None
    main_root = os.path.dirname(common_directory)
    worktree_root = os.path.realpath(top)
    if main_root == worktree_root:
        return None
    if git_root:
        return main_root
    try:
        position = os.path.relpath(os.path.realpath(cwd), worktree_root)
    except ValueError:
        # On Windows, a path on another drive has no relative form.
        return None
    if position == os.pardir or position.startswith(os.pardir + os.sep):
        return None
    return os.path.normpath(os.path.join(main_root, position))
