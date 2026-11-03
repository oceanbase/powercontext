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

"""Real Git checks for task-image history sanitization."""

import subprocess
from pathlib import Path

import pytest

from powercontext_eval_swebench_pro.powercontext_sut import UnsafeSutConfiguration, trim_future_history
from powercontext_eval_swebench_pro.process import ProcessRunner


def git(workspace: Path, *argv: str) -> str:
    return subprocess.run(
        ("git", "-c", "user.name=fixture", "-c", "user.email=fixture@example.test", *argv),
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def create_history(workspace: Path) -> str:
    workspace.mkdir()
    git(workspace, "init", "--quiet", "--initial-branch=main")
    (workspace / "module.py").write_text("value = 1\n")
    git(workspace, "add", "module.py")
    git(workspace, "commit", "--quiet", "-m", "base")
    git(workspace, "switch", "--quiet", "-c", "future")
    (workspace / "module.py").write_text("value = 2\n")
    git(workspace, "commit", "--quiet", "-am", "reference fix")
    fix = git(workspace, "rev-parse", "HEAD")
    git(workspace, "switch", "--quiet", "main")
    return fix


@pytest.mark.parametrize("retention", ["keep", "promisor"])
def test_workspace_history_rejects_retained_future_commit_objects(tmp_path: Path, retention: str) -> None:
    workspace = tmp_path / "workspace"
    fix = create_history(workspace)
    git(workspace, "repack", "-ad")
    pack = next((workspace / ".git/objects/pack").glob("*.pack"))
    pack.with_suffix(f".{retention}").touch()

    with pytest.raises(UnsafeSutConfiguration, match="retains commits outside HEAD ancestry"):
        trim_future_history(ProcessRunner(), workspace)

    # The ref-only postcondition passes even though the agent could still read the reference fix by its SHA.
    assert git(workspace, "rev-list", "--all", "--not", "HEAD") == ""
    assert "value = 2" in git(workspace, "show", f"{fix}:module.py")


def test_workspace_history_rejects_alternate_object_store_before_mutation(tmp_path: Path) -> None:
    source = tmp_path / "source"
    fix = create_history(source)
    workspace = tmp_path / "workspace"
    git(tmp_path, "clone", "--quiet", "--shared", str(source), str(workspace))
    original_refs = git(workspace, "show-ref")
    source_refs = git(source, "show-ref")
    original_index = (workspace / ".git/index").read_bytes()

    with pytest.raises(UnsafeSutConfiguration, match="alternate object stores are unsupported"):
        trim_future_history(ProcessRunner(), workspace)

    assert git(workspace, "show-ref") == original_refs
    assert git(source, "show-ref") == source_refs
    assert (workspace / ".git/index").read_bytes() == original_index
    assert "value = 2" in git(source, "show", f"{fix}:module.py")


@pytest.mark.parametrize("target", ["main", "linked"])
def test_workspace_history_rejects_linked_worktrees_before_mutation(tmp_path: Path, target: str) -> None:
    workspace = tmp_path / "workspace"
    fix = create_history(workspace)
    linked = tmp_path / "linked"
    git(workspace, "worktree", "add", "--quiet", str(linked), "future")
    original_refs = git(workspace, "show-ref")

    with pytest.raises(UnsafeSutConfiguration, match="private git directory|linked worktrees are unsupported"):
        trim_future_history(ProcessRunner(), workspace if target == "main" else linked)

    assert git(workspace, "show-ref") == original_refs
    assert git(linked, "rev-parse", "HEAD") == fix
    assert (linked / "module.py").read_text() == "value = 2\n"
