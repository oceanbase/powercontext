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

"""The Python and TypeScript worktree helpers find the same workspace paths for every Git layout."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from powercontext_integration_lifecycle.git_worktree import workspace_paths
from tests.git_worktree_support import GitLayout, fake_old_git, make_git_layout

ROOT = Path(__file__).resolve().parents[1]
# The JavaScript copy is the TypeScript source with its types removed, so it runs the shared rule without a build.
JAVASCRIPT_HELPER = ROOT / "integrations/kimi-code/plugins/powercontext/shared/git-worktree.mjs"
_JAVASCRIPT = """
const [helper, directory, gitRoot] = process.argv.slice(1)
const { workspacePaths, workspacePathsSync } = await import(helper)
const paths = await workspacePaths(directory, gitRoot === 'true', 2000)
const sync = workspacePathsSync(directory, gitRoot === 'true', 2000)
if (JSON.stringify(paths) !== JSON.stringify(sync)) throw new Error('the sync and async paths differ')
console.log(JSON.stringify(paths))
"""

Helper = Callable[[Path, bool], list[str]]


def _python(directory: Path, git_root: bool) -> list[str]:
    return workspace_paths(str(directory), git_root=git_root)


def _javascript(directory: Path, git_root: bool) -> list[str]:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed")
    completed = subprocess.run(
        [
            node,
            "--input-type=module",
            "-e",
            _JAVASCRIPT,
            JAVASCRIPT_HELPER.as_uri(),
            str(directory),
            str(git_root).lower(),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return json.loads(completed.stdout)


@pytest.fixture(params=[_python, _javascript], ids=["python", "javascript"])
def helper(request: pytest.FixtureRequest) -> Helper:
    run: Helper = request.param
    # Git prints a Windows path with forward slashes, and every host resolves a path before keying it.
    return lambda directory, git_root: [os.path.normpath(path) for path in run(directory, git_root)]


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> GitLayout:
    return make_git_layout(tmp_path, monkeypatch)


def test_a_linked_worktree_adds_its_main_checkouts_root(helper: Helper, layout: GitLayout) -> None:
    assert helper(layout.worktree, True) == [str(layout.worktree), str(layout.checkout)]
    assert helper(layout.worktree_sub, True) == [str(layout.worktree), str(layout.checkout)]


def test_a_linked_worktree_keeps_the_working_directorys_position_in_the_main_checkout(
    helper: Helper, layout: GitLayout
) -> None:
    assert helper(layout.worktree, False) == [str(layout.worktree), str(layout.checkout)]
    assert helper(layout.worktree_sub, False) == [str(layout.worktree_sub), str(layout.checkout_sub)]


def test_the_main_checkout_gets_no_second_path(helper: Helper, layout: GitLayout) -> None:
    assert helper(layout.checkout, True) == [str(layout.checkout)]
    assert helper(layout.checkout_sub, True) == [str(layout.checkout)]
    assert helper(layout.checkout_sub, False) == [str(layout.checkout_sub)]


def test_a_worktree_of_a_bare_repository_gets_no_second_path(helper: Helper, layout: GitLayout) -> None:
    assert helper(layout.bare_worktree, True) == [str(layout.bare_worktree)]
    assert helper(layout.bare_worktree, False) == [str(layout.bare_worktree)]


def test_a_worktree_of_a_bare_repository_in_a_directory_named_git_gets_no_second_path(
    helper: Helper, layout: GitLayout
) -> None:
    assert helper(layout.dot_git_bare_worktree, True) == [str(layout.dot_git_bare_worktree)]
    assert helper(layout.dot_git_bare_worktree, False) == [str(layout.dot_git_bare_worktree)]


def test_a_checkout_with_a_separate_git_directory_gets_no_second_path(helper: Helper, layout: GitLayout) -> None:
    assert helper(layout.separate_checkout, True) == [str(layout.separate_checkout)]
    assert helper(layout.separate_checkout, False) == [str(layout.separate_checkout)]


def test_a_linked_worktree_of_a_checkout_with_a_separate_git_directory_falls_back_to_that_directorys_parent(
    helper: Helper, layout: GitLayout
) -> None:
    # Git records the parent of the separate Git directory as the main worktree, so that is the only path it offers.
    assert helper(layout.separate_worktree, True) == [str(layout.separate_worktree), str(layout.separate_store)]


@pytest.mark.skipif(os.name == "nt", reason="creating a symbolic link needs extra privileges on Windows")
def test_a_main_checkout_path_that_links_back_to_the_working_directory_is_left_out(
    helper: Helper, layout: GitLayout
) -> None:
    linked = layout.worktree / "linked"
    linked.mkdir()
    (layout.checkout / "linked").symlink_to(linked, target_is_directory=True)

    assert helper(linked, False) == [str(linked)]


def test_a_directory_outside_git_keeps_its_own_path(helper: Helper, layout: GitLayout) -> None:
    assert helper(layout.outside, True) == [str(layout.outside)]
    assert helper(layout.outside, False) == [str(layout.outside)]
    missing = layout.outside / "missing"
    assert helper(missing, True) == [str(missing)]


@pytest.mark.skipif(os.name == "nt", reason="the fake Git is a POSIX shell script")
def test_git_without_path_format_keeps_the_top_level_and_adds_nothing(
    helper: Helper, layout: GitLayout, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    common = layout.checkout / ".git"
    fake_old_git(tmp_path / "bin", layout.worktree, common / "worktrees" / layout.worktree.name, common, monkeypatch)

    assert helper(layout.worktree_sub, True) == [str(layout.worktree)]
    assert helper(layout.worktree_sub, False) == [str(layout.worktree_sub)]
