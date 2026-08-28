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

"""Scope-resolution tests for the PowerContext LangGraph adapter.

The adapter inverts the Codex priority: an explicit scope wins, a network Git remote is the fallback, and neither
available is an error rather than a silent shared-local default.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from shutil import which

import pytest

pytest.importorskip("powercontext_langgraph")

from powercontext_langgraph import MissingScopeError, resolve_scope_id
from powercontext_langgraph.scope import normalize_git_remote

from powercontext.limits import MAX_SCOPE_ID_LENGTH


def test_explicit_scope_takes_priority() -> None:
    assert resolve_scope_id("project:acme") == "project:acme"


def test_explicit_scope_over_length_is_hashed() -> None:
    resolved = resolve_scope_id("x" * (MAX_SCOPE_ID_LENGTH + 1))
    assert resolved.startswith("sha256:")
    assert len(resolved) <= MAX_SCOPE_ID_LENGTH


def test_missing_scope_outside_repository_raises(tmp_path: Path) -> None:
    with pytest.raises(MissingScopeError):
        resolve_scope_id(None, cwd=str(tmp_path))


@pytest.mark.skipif(which("git") is None, reason="git is required to derive a scope from a remote")
def test_git_remote_is_derived_and_credentials_dropped(tmp_path: Path) -> None:
    git = which("git")
    assert git is not None
    subprocess.run([git, "init", "--quiet"], cwd=tmp_path, check=True)
    subprocess.run(
        [git, "remote", "add", "origin", "https://user:secret@github.com/oceanbase/powercontext.git"],
        cwd=tmp_path,
        check=True,
    )
    resolved = resolve_scope_id(None, cwd=str(tmp_path))
    assert resolved == "git:github.com/oceanbase/powercontext"
    assert "secret" not in resolved


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("https://github.com/oceanbase/powercontext.git", "github.com/oceanbase/powercontext"),
        ("git@github.com:oceanbase/powercontext.git", "github.com/oceanbase/powercontext"),
        ("ssh://git@example.com:2222/team/repo.git", "example.com:2222/team/repo"),
        ("https://user:token@github.com/oceanbase/powercontext.git", "github.com/oceanbase/powercontext"),
    ],
)
def test_normalize_git_remote_forms(remote: str, expected: str) -> None:
    assert normalize_git_remote(remote) == expected


@pytest.mark.parametrize(
    "remote",
    [
        "",
        "not-a-remote",
        "/local/path/only",
        # Windows drive-letter paths parse as an SCP ``host:path`` whose host is the drive letter. They are local
        # filesystem paths, not network remotes, and must not be turned into a scope.
        r"C:\work\tenant-repo.git",
        "C:/work/tenant-repo.git",
        r"D:\repos\api",
    ],
)
def test_normalize_git_remote_rejects_non_network_remotes(remote: str) -> None:
    assert normalize_git_remote(remote) is None


@pytest.mark.parametrize("remote", [r"C:\work\tenant-repo.git", "C:/work/tenant-repo.git"])
def test_resolve_scope_id_rejects_windows_local_remote(monkeypatch: pytest.MonkeyPatch, remote: str) -> None:
    monkeypatch.setattr("powercontext_langgraph.scope._git_remote", lambda _cwd: remote)
    with pytest.raises(MissingScopeError):
        resolve_scope_id(None, cwd="/irrelevant")
