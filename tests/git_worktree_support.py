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

"""Real Git checkouts for the tests of workspace bindings in linked worktrees."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

from powercontext.http import ScopeBindingKey


@dataclass(frozen=True)
class GitLayout:
    """A checkout with a linked worktree, a linked worktree of a bare repository, and a directory outside Git.

    Each path is real, so it equals what Git reports. ``checkout_sub`` and ``worktree_sub`` are the same subdirectory
    in the checkout and in its linked worktree. ``separate_checkout`` keeps its Git directory at
    ``separate_store / ".git"``, and ``separate_worktree`` is a linked worktree of it. ``dot_git_bare_worktree`` is a
    linked worktree of a bare repository whose directory is named ``.git``.
    """

    checkout: Path
    checkout_sub: Path
    worktree: Path
    worktree_sub: Path
    bare_worktree: Path
    outside: Path
    separate_checkout: Path
    separate_store: Path
    separate_worktree: Path
    dot_git_bare_worktree: Path


def _git(executable: str, *arguments: str | Path) -> None:
    subprocess.run(
        [executable, "-c", "user.name=test", "-c", "user.email=test@example.com", *map(str, arguments)],
        check=True,
        capture_output=True,
    )


def make_git_layout(root: Path, monkeypatch: pytest.MonkeyPatch) -> GitLayout:
    """Create the layout under ``root``, without letting the test runner's own Git environment leak in."""

    executable = shutil.which("git")
    if executable is None:
        pytest.skip("Git is not installed")
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE", "GIT_CEILING_DIRECTORIES"):
        monkeypatch.delenv(name, raising=False)
    root = root.resolve()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(root))
    checkout, worktree, bare, bare_worktree = (root / name for name in ("checkout", "worktree", "bare.git", "bare-wt"))
    _git(executable, "init", "-q", checkout)
    _git(executable, "-C", checkout, "commit", "-q", "--allow-empty", "-m", "init")
    _git(executable, "-C", checkout, "worktree", "add", "-q", worktree)
    _git(executable, "init", "-q", "--bare", bare)
    _git(executable, "-C", checkout, "push", "-q", bare, "HEAD:refs/heads/main")
    _git(executable, "-C", bare, "worktree", "add", "-q", bare_worktree, "main")
    separate_checkout, separate_store, separate_worktree = (
        root / name for name in ("separate", "store", "separate-wt")
    )
    separate_store.mkdir()
    _git(executable, "init", "-q", "--separate-git-dir", separate_store / ".git", separate_checkout)
    _git(executable, "-C", separate_checkout, "commit", "-q", "--allow-empty", "-m", "init")
    _git(executable, "-C", separate_checkout, "worktree", "add", "-q", separate_worktree)
    dot_git_bare, dot_git_bare_worktree = root / "dot-git-bare" / ".git", root / "dot-git-bare-wt"
    _git(executable, "init", "-q", "--bare", dot_git_bare)
    _git(executable, "-C", checkout, "push", "-q", dot_git_bare, "HEAD:refs/heads/main")
    _git(executable, "-C", dot_git_bare, "worktree", "add", "-q", dot_git_bare_worktree, "main")
    for directory in (checkout / "sub", worktree / "sub", root / "outside"):
        directory.mkdir()
    return GitLayout(
        checkout=checkout,
        checkout_sub=checkout / "sub",
        worktree=worktree,
        worktree_sub=worktree / "sub",
        bare_worktree=bare_worktree,
        outside=root / "outside",
        separate_checkout=separate_checkout,
        separate_store=separate_store,
        separate_worktree=separate_worktree,
        dot_git_bare_worktree=dot_git_bare_worktree,
    )


def fake_old_git(directory: Path, top: Path, git_dir: Path, common: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Put a Git that predates --path-format first on PATH; it prints that option back, as Git before 2.31 does."""

    directory.mkdir()
    script = directory / "git"
    script.write_text(
        f"#!/bin/sh\nprintf '%s\\n' '{top}' '--path-format=absolute' '{git_dir}' '{common}'\n", encoding="utf-8"
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")


BindingKey = Mapping[str, str] | ScopeBindingKey


class ScopeBindings:
    """Stands in for the Scope service's binding store: resolution takes the first bound key, else the default."""

    def __init__(self) -> None:
        self.scopes: dict[tuple[str, str, str], str] = {}

    @staticmethod
    def _identity(key: BindingKey) -> tuple[str, str, str]:
        if isinstance(key, ScopeBindingKey):
            return key.integration, key.kind, key.external_id
        return key["integration"], key["kind"], key["external_id"]

    def resolve(self, keys: Iterable[BindingKey]) -> str:
        return next((self.scopes[self._identity(key)] for key in keys if self._identity(key) in self.scopes), "default")

    def set(self, key: BindingKey, scope_id: str) -> None:
        self.scopes[self._identity(key)] = scope_id

    def clear(self, key: BindingKey) -> bool:
        return self.scopes.pop(self._identity(key), None) is not None


def check_worktree_fallback(
    *,
    checkout: Path,
    worktree: Path,
    bare_worktree: Path,
    resolve: Callable[[Path], str],
    bind: Callable[[Path, str], object],
    clear: Callable[[Path], object],
) -> None:
    """Check one host's resolution, binding, and clearing in a checkout and in the same place in its linked worktree.

    ``resolve`` returns the Scope that the host resolves in a directory, and ``bind`` and ``clear`` change the host's
    workspace binding there.
    """

    bind(checkout, "checkout-scope")
    assert resolve(worktree) == "checkout-scope"
    assert resolve(bare_worktree) == "default"

    bind(worktree, "worktree-scope")
    assert resolve(worktree) == "worktree-scope"
    assert resolve(checkout) == "checkout-scope"

    clear(worktree)
    assert resolve(worktree) == "checkout-scope"
    assert resolve(checkout) == "checkout-scope"
