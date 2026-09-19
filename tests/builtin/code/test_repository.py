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

"""Capture behavior against real Git repositories and filesystem changes."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path
from time import monotonic

import pytest

from powercontext.builtin.code import repository as repository_module
from powercontext.builtin.code.config import CodeConfig, CodeLimits
from powercontext.builtin.code.errors import CodeUnavailableError
from powercontext.builtin.code.repository import capture_repository

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def git(root: Path, *arguments: str) -> str:
    executable = shutil.which("git")
    assert executable is not None
    return (
        subprocess
        .run((executable, "-C", str(root), *arguments), check=True, capture_output=True)
        .stdout.decode()
        .strip()
    )


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    root.mkdir()
    git(root, "init", "--quiet")
    git(root, "config", "user.name", "Code acceptance")
    git(root, "config", "user.email", "code-acceptance@example.invalid")
    (root / "core.py").write_text("def calculate():\n    return 1\n")
    (root / ".gitignore").write_text("ignored.py\n.env*\n")
    git(root, "add", "--", "core.py", ".gitignore")
    git(root, "commit", "--quiet", "-m", "initial fixture")
    return root


async def test_capture_uses_disk_bytes_for_staged_and_unstaged_edits(repository: Path, tmp_path: Path) -> None:
    code = repository / "core.py"
    code.write_text("def calculate():\n    return 2\n")
    git(repository, "add", "--", "core.py")
    current = b"def calculate():\n    return 3\n"
    code.write_bytes(current)
    destination = tmp_path / "capture"
    destination.mkdir()
    captured = await capture_repository(repository, CodeConfig(), deadline=monotonic() + 5, destination=destination)
    assert captured.dirty
    assert captured.files[0].sha256 == hashlib.sha256(current).hexdigest()
    assert (destination / "core.py").read_bytes() == current
    code.write_text("def calculate():\n    return 4\n")
    changed = await capture_repository(repository, CodeConfig(), deadline=monotonic() + 5)
    assert changed.content_digest() != captured.content_digest()
    assert (destination / "core.py").read_bytes() == current


async def test_untracked_files_require_opt_in_and_still_obey_gitignore(repository: Path) -> None:
    (repository / "new.py").write_text("VALUE = 1\n")
    (repository / "ignored.py").write_text("VALUE = 2\n")
    default = await capture_repository(repository, CodeConfig(), deadline=monotonic() + 5)
    included = await capture_repository(repository, CodeConfig(include_untracked=True), deadline=monotonic() + 5)
    assert {file.path for file in default.files} == {"core.py"}
    assert {file.path for file in included.files} == {"core.py", "new.py"}
    assert not default.dirty
    assert included.dirty


async def test_tracked_symlinks_and_credentials_are_not_captured(repository: Path, tmp_path: Path) -> None:
    external = tmp_path / "outside.py"
    external.write_text("EXTERNAL = 'not repository content'\n")
    (repository / "linked.py").symlink_to(external)
    (repository / ".env.private").write_text("TOKEN=fixture-value\n")
    git(repository, "add", "-f", "--", "linked.py", ".env.private")
    captured = await capture_repository(repository, CodeConfig(), deadline=monotonic() + 5)
    assert {file.path for file in captured.files} == {"core.py"}
    assert captured.omissions["symlink_or_submodule"] == 1
    assert captured.omissions["excluded"] == 1
    assert not captured.dirty


@pytest.mark.parametrize("link_target", ["internal", "external", "missing"])
async def test_untracked_symlinks_are_omitted_without_losing_regular_files(
    repository: Path, tmp_path: Path, link_target: str
) -> None:
    external = tmp_path / "outside.py"
    external.write_text("EXTERNAL = 'not repository content'\n")
    target = {"internal": repository / "core.py", "external": external, "missing": tmp_path / "missing.py"}
    (repository / "linked.py").symlink_to(target[link_target])
    (repository / "new.py").write_text("VALUE = 1\n")
    destination = tmp_path / "capture"
    destination.mkdir()
    captured = await capture_repository(
        repository, CodeConfig(include_untracked=True), deadline=monotonic() + 5, destination=destination
    )
    assert {file.path for file in captured.files} == {"core.py", "new.py"}
    assert captured.omissions["symlink_or_submodule"] == 1
    assert captured.dirty
    assert not (destination / "linked.py").exists()


async def test_aggregate_limit_fails_the_build_instead_of_selecting_a_prefix(repository: Path) -> None:
    (repository / "another.py").write_text("SECOND = 2\n")
    git(repository, "add", "--", "another.py")
    with pytest.raises(CodeUnavailableError, match="code_input_limit"):
        await capture_repository(repository, CodeConfig(limits=CodeLimits(max_files=1)), deadline=monotonic() + 5)


async def test_deleted_file_does_not_reuse_cached_content(repository: Path) -> None:
    captured = await capture_repository(repository, CodeConfig(), deadline=monotonic() + 5)
    assert captured.files
    (repository / "core.py").unlink()
    changed = await capture_repository(repository, CodeConfig(), deadline=monotonic() + 5)
    assert not changed.files
    assert changed.dirty
    assert changed.content_digest() != captured.content_digest()


async def test_capture_does_not_execute_repository_git_filters(repository: Path) -> None:
    (repository / ".gitattributes").write_text("*.py filter=probe\n")
    git(repository, "config", "filter.probe.clean", "touch filter-ran; cat")
    (repository / "core.py").write_text("def calculate():\n    return 2\n")
    captured = await capture_repository(repository, CodeConfig(), deadline=monotonic() + 5)
    assert captured.dirty
    assert not (repository / "filter-ran").exists()


async def test_capture_retries_a_branch_switch_without_mixing_commit_and_bytes(repository: Path, monkeypatch) -> None:
    (repository / "removed.py").write_text("OLD = True\n")
    git(repository, "add", "removed.py")
    git(repository, "commit", "--quiet", "-m", "old file")
    git(repository, "checkout", "--quiet", "-b", "other")
    git(repository, "rm", "--quiet", "removed.py")
    changed = b"def calculate():\n    return 20\n"
    (repository / "core.py").write_bytes(changed)
    git(repository, "add", "core.py")
    git(repository, "commit", "--quiet", "-m", "other branch")
    git(repository, "checkout", "--quiet", "-")
    original = repository_module._capture_files
    switched = False

    def switch_after_capture(*args, **kwargs):
        nonlocal switched
        result = original(*args, **kwargs)
        if not switched:
            switched = True
            git(repository, "checkout", "--quiet", "other")
        return result

    monkeypatch.setattr(repository_module, "_capture_files", switch_after_capture)
    destination = repository.parent / "capture"
    destination.mkdir()
    captured = await capture_repository(repository, CodeConfig(), deadline=monotonic() + 5, destination=destination)
    assert not captured.dirty
    assert captured.files[0].sha256 == hashlib.sha256(changed).hexdigest()
    assert captured.commit == git(repository, "rev-parse", "HEAD")
    assert not (destination / "removed.py").exists()


async def test_sha256_git_repository_binds_raw_blob_identity(tmp_path: Path) -> None:
    root = tmp_path / "sha256"
    root.mkdir()
    git(root, "init", "--quiet", "--object-format=sha256")
    git(root, "config", "user.name", "Acceptance")
    git(root, "config", "user.email", "acceptance@example.invalid")
    (root / "core.py").write_text("VALUE = 1\n")
    git(root, "add", "core.py")
    git(root, "commit", "--quiet", "-m", "fixture")
    initial = await capture_repository(root, CodeConfig(), deadline=monotonic() + 5)
    assert initial.git_object_format == "sha256" and len(initial.commit) == 64
    assert not initial.dirty
    (root / "core.py").write_text("VALUE = 2\n")
    changed = await capture_repository(root, CodeConfig(), deadline=monotonic() + 5)
    assert changed.dirty and changed.content_digest() != initial.content_digest()
