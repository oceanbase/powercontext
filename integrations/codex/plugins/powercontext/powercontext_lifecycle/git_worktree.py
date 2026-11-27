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

# Generated from src/powercontext_integration_lifecycle/git_worktree.py; do not edit.
# Edit the source and run `make integration-lifecycle` instead.

"""Find the paths whose workspace bindings apply to a directory, so a linked Git worktree falls back to its checkout.

integrations/lifecycle/git-worktree.ts applies the same rule for the TypeScript and JavaScript hosts.
"""

from __future__ import annotations

import os
import subprocess
import time
from shutil import which

# --path-format applies to the options after it, so the top level stays exactly what --show-toplevel alone prints.
# It needs Git 2.31 or later.
_ARGUMENTS = ("rev-parse", "--show-toplevel", "--path-format=absolute", "--git-dir", "--git-common-dir")
# Older Git does not know --path-format and prints it back as an argument.
_UNKNOWN_FORMAT = "\n--path-format=absolute"
# Asked with --git-dir set to the common directory, because a linked worktree always reports itself as non-bare.
_BARE_ARGUMENTS = ("rev-parse", "--is-bare-repository")


def workspace_paths(cwd: str, *, git_root: bool, timeout: float = 2.0) -> list[str]:
    """Return the paths whose workspace bindings apply to ``cwd``, the worktree's own first.

    The first path is the one the host has always keyed: with ``git_root``, the Git top level as Git prints it, or
    ``cwd`` outside a work tree; otherwise ``cwd`` itself. In a linked worktree of a non-bare repository, a second path
    follows from the main worktree: its root with ``git_root``, otherwise the path at ``cwd``'s position in it. Each
    host keys both paths the way it keys the first, so the second matches a binding made in the main checkout, and
    resolves them in this order, so the worktree's own binding wins. Binding and clearing use only the first.

    A linked worktree's Git directory differs from the common directory, and its main worktree is the parent of the
    common directory when that directory is named ``.git`` and Git confirms that the repository is not bare. A main
    checkout, including one whose Git directory is separate, gets no second path, and neither do bare repositories,
    including one in a directory named ``.git``, and submodules. A linked worktree of a checkout with a separate Git
    directory falls back to the parent of that directory, since Git records no other main worktree. A second path that
    is the same directory as ``cwd`` is left out. Both Git calls together stay within ``timeout``.
    """

    deadline = time.monotonic() + timeout
    top, git_dir, common = _git_paths(_git_output(cwd, _ARGUMENTS, timeout))
    first = (top or cwd) if git_root else cwd
    main = _main_worktree_path(cwd, top, git_dir, common, git_root=git_root)
    if main is None or common is None:
        return [first]
    # An unknown answer leaves the second path out, as for a bare repository.
    bare = _git_output(cwd, ("--git-dir", common, *_BARE_ARGUMENTS), deadline - time.monotonic())
    return [first, main] if bare.strip() == "false" else [first]


def _git_output(cwd: str, arguments: tuple[str, ...], timeout: float) -> str:
    """Return what Git prints for ``arguments`` in ``cwd``, or an empty string when it fails or time is up."""

    executable = which("git")
    if executable is None or timeout <= 0:
        return ""
    try:
        completed = subprocess.run(  # noqa: S603 - git executable and arguments are integration-owned.
            [executable, *arguments],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return completed.stdout


def _git_paths(stdout: str) -> tuple[str | None, str | None, str | None]:
    """Return the top level and the absolute Git and common directories in Git's output, when known."""

    rest, _, common = stdout.rstrip("\n").rpartition("\n")
    head, _, git_dir = rest.rpartition("\n")
    if head.endswith(_UNKNOWN_FORMAT):
        return head[: -len(_UNKNOWN_FORMAT)].strip() or None, None, None
    return head.strip() or None, git_dir or None, common or None


def _main_worktree_path(
    cwd: str, top: str | None, git_dir: str | None, common: str | None, *, git_root: bool
) -> str | None:
    if top is None or git_dir is None or common is None or not (os.path.isabs(git_dir) and os.path.isabs(common)):
        return None
    common_directory = os.path.realpath(common)
    if os.path.realpath(git_dir) == common_directory or os.path.basename(common_directory) != ".git":
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
    main = os.path.normpath(os.path.join(main_root, position))
    # A symbolic link in the main checkout can lead back to the working directory itself.
    return None if os.path.realpath(main) == os.path.realpath(cwd) else main
