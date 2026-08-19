from __future__ import annotations

import errno
import fcntl
import multiprocessing
import os
import platform
import re
import threading
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from multiprocessing.connection import Connection
from pathlib import Path
from typing import BinaryIO

import pytest

from powercontext_eval import git_source as git_source_module
from powercontext_eval.errors import CommandError, CommandFailed, GitSourceError
from powercontext_eval.git_source import GitSource, ResolvedGitSource
from powercontext_eval.models import PowerContextRef
from powercontext_eval.process import CommandResult, ProcessRunner

from .helpers import GitFixture, create_git_fixture, git


@pytest.fixture
def git_fixture(tmp_path: Path) -> GitFixture:
    return create_git_fixture(tmp_path)


@pytest.fixture
def source(tmp_path: Path) -> GitSource:
    return GitSource(cache_root=tmp_path / "cache")


@pytest.mark.parametrize(
    ("requested", "expected_attribute"),
    [
        (PowerContextRef(kind="branch", value="feature"), "feature_sha"),
        (PowerContextRef(kind="tag", value="v1"), "initial_sha"),
        (PowerContextRef(kind="tag", value="annotated-v1"), "initial_sha"),
    ],
)
def test_resolve_exact_branch_and_tags(
    source: GitSource,
    git_fixture: GitFixture,
    requested: PowerContextRef,
    expected_attribute: str,
) -> None:
    resolved = source.resolve(git_fixture.remote, requested)

    assert resolved.source == str(git_fixture.remote.resolve())
    assert resolved.requested == requested
    assert resolved.sha == getattr(git_fixture, expected_attribute)
    assert re.fullmatch(r"[0-9a-f]{40}", resolved.sha)
    assert resolved.cache_path.parent.name == "cache"
    assert resolved.cache_path.is_dir()


def test_resolve_full_commit_and_lowercases_sha(source: GitSource, git_fixture: GitFixture) -> None:
    requested = PowerContextRef(kind="commit", value=git_fixture.feature_sha.upper())

    resolved = source.resolve(git_fixture.remote, requested)

    assert resolved.sha == git_fixture.feature_sha


def test_resolve_rejects_exact_tag_that_points_to_blob(source: GitSource, git_fixture: GitFixture) -> None:
    blob_sha = git(git_fixture.work, "hash-object", "README.md").stdout.strip()
    git(git_fixture.work, "update-ref", "refs/tags/blob-only", blob_sha)
    git(git_fixture.work, "push", "origin", "refs/tags/blob-only")

    with pytest.raises(GitSourceError, match="could not resolve to a commit"):
        source.resolve(git_fixture.remote, PowerContextRef(kind="tag", value="blob-only"))


def test_resolve_latest_uses_clean_local_head(source: GitSource, git_fixture: GitFixture) -> None:
    resolved = source.resolve(git_fixture.work, PowerContextRef(kind="latest"))

    assert resolved.sha == git_fixture.initial_sha


def test_resolve_latest_uses_head_of_local_bare_remote(source: GitSource, git_fixture: GitFixture) -> None:
    resolved = source.resolve(git_fixture.remote, PowerContextRef(kind="latest"))

    assert resolved.sha == git_fixture.initial_sha


def test_resolve_latest_rejects_dirty_local_checkout(source: GitSource, git_fixture: GitFixture) -> None:
    (git_fixture.work / "untracked.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(GitSourceError, match="clean"):
        source.resolve(git_fixture.work, PowerContextRef(kind="latest"))


def test_materialize_same_resolution_twice_at_identical_head(
    source: GitSource,
    git_fixture: GitFixture,
    tmp_path: Path,
) -> None:
    resolved = source.resolve(git_fixture.remote, PowerContextRef(kind="branch", value="feature"))

    first = source.materialize(resolved, tmp_path / "first")
    second = source.materialize(resolved, tmp_path / "second")

    assert first == tmp_path / "first"
    assert second == tmp_path / "second"
    assert git(first, "rev-parse", "HEAD").stdout.strip() == resolved.sha
    assert git(second, "rev-parse", "HEAD").stdout.strip() == resolved.sha


def test_materialize_does_not_reresolve_branch_that_moved_after_resolution(
    source: GitSource,
    git_fixture: GitFixture,
    tmp_path: Path,
) -> None:
    resolved = source.resolve(git_fixture.remote, PowerContextRef(kind="branch", value="feature"))
    moved_sha = git_fixture.commit_to_feature("move feature")
    assert moved_sha != resolved.sha

    target = source.materialize(resolved, tmp_path / "materialized")

    assert git(target, "rev-parse", "HEAD").stdout.strip() == resolved.sha


def test_resolve_pins_commit_so_old_resolution_survives_force_move_and_gc(
    source: GitSource,
    git_fixture: GitFixture,
    tmp_path: Path,
) -> None:
    old = source.resolve(git_fixture.remote, PowerContextRef(kind="branch", value="feature"))
    pin_ref = f"refs/powercontext-eval/pins/{old.sha}"

    assert git(old.cache_path, "rev-parse", pin_ref).stdout.strip() == old.sha
    git(git_fixture.work, "push", "--force", "origin", "main:feature")
    current = source.resolve(git_fixture.remote, PowerContextRef(kind="branch", value="feature"))
    assert current.sha == git_fixture.initial_sha
    git(old.cache_path, "reflog", "expire", "--expire=now", "--all")
    git(old.cache_path, "gc", "--prune=now")

    target = source.materialize(old, tmp_path / "old-materialized")

    assert git(target, "rev-parse", "HEAD").stdout.strip() == old.sha
    assert git(old.cache_path, "rev-parse", pin_ref).stdout.strip() == old.sha


def test_resolve_refreshes_an_existing_mirror(source: GitSource, git_fixture: GitFixture) -> None:
    original = source.resolve(git_fixture.remote, PowerContextRef(kind="branch", value="feature"))
    moved_sha = git_fixture.commit_to_feature("refresh feature")

    refreshed = source.resolve(git_fixture.remote, PowerContextRef(kind="branch", value="feature"))

    assert original.sha != moved_sha
    assert refreshed.sha == moved_sha
    assert refreshed.cache_path == original.cache_path


def test_mirror_write_lock_is_cross_process_and_canonicalizes_cache_aliases(tmp_path: Path) -> None:
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    alias_parent = tmp_path / "alias"
    alias_parent.symlink_to(real_parent, target_is_directory=True)
    cache_root = real_parent / "cache"
    cache_root.mkdir()
    alias_root = alias_parent / "cache"
    bucket = "a" * 64
    cache_path = cache_root / bucket
    alias_path = alias_root / bucket

    lock_path = git_source_module._mirror_lock_path(cache_root, cache_path)
    assert git_source_module._mirror_lock_path(alias_root, alias_path) == lock_path

    process_context = multiprocessing.get_context("fork")
    parent_connection, child_connection = process_context.Pipe()
    process = process_context.Process(target=_probe_mirror_lock, args=(lock_path, child_connection))
    try:
        with git_source_module._mirror_write_lock(cache_root, cache_path):
            process.start()
            child_connection.close()
            assert parent_connection.poll(timeout=5)
            assert parent_connection.recv() == "blocked"

        parent_connection.send("retry")
        assert parent_connection.poll(timeout=5)
        assert parent_connection.recv() == "acquired"
        process.join(timeout=5)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            try:
                parent_connection.send("retry")
            except (BrokenPipeError, EOFError, OSError):
                pass
            process.join(timeout=1)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        parent_connection.close()


def test_resolve_enters_the_cross_process_mirror_write_lock(
    tmp_path: Path,
    git_fixture: GitFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = GitSource(cache_root=tmp_path / "cache")
    real_lock = git_source_module._mirror_write_lock
    calls: list[tuple[Path, Path]] = []

    @contextmanager
    def recording_lock(cache_root: Path, cache_path: Path) -> Iterator[None]:
        calls.append((cache_root, cache_path))
        with real_lock(cache_root, cache_path):
            yield

    monkeypatch.setattr(git_source_module, "_mirror_write_lock", recording_lock)

    resolved = source.resolve(git_fixture.remote, PowerContextRef(kind="branch", value="feature"))

    assert calls == [(resolved.cache_root, resolved.cache_path)]


def test_resolve_prunes_deleted_refs_from_existing_mirror(source: GitSource, git_fixture: GitFixture) -> None:
    source.resolve(git_fixture.remote, PowerContextRef(kind="branch", value="release"))
    git(git_fixture.work, "push", "origin", ":release")

    with pytest.raises(GitSourceError, match="could not resolve"):
        source.resolve(git_fixture.remote, PowerContextRef(kind="branch", value="release"))


def test_resolve_rejects_nonbare_existing_cache(source: GitSource, git_fixture: GitFixture) -> None:
    cache_path = source.cache_path_for(git_fixture.remote)
    cache_path.mkdir(parents=True)
    git(cache_path, "init")

    with pytest.raises(GitSourceError, match="not a bare mirror"):
        source.resolve(git_fixture.remote, PowerContextRef(kind="branch", value="main"))


def test_resolve_rejects_cache_bucket_symlink_before_any_git_command(
    tmp_path: Path,
    git_fixture: GitFixture,
) -> None:
    cache_root = tmp_path / "cache"
    runner = RecordingProcessRunner()
    source = GitSource(cache_root=cache_root, runner=runner)
    expected_bucket = source.cache_path_for(git_fixture.remote)
    cache_root.mkdir()
    expected_bucket.symlink_to(git_fixture.remote, target_is_directory=True)
    config_before = (git_fixture.remote / "config").read_bytes()
    refs_before = git(git_fixture.remote, "show-ref").stdout

    with pytest.raises(GitSourceError, match="symlink"):
        source.resolve(git_fixture.remote, PowerContextRef(kind="branch", value="feature"))

    assert runner.calls == []
    assert (git_fixture.remote / "config").read_bytes() == config_before
    assert git(git_fixture.remote, "show-ref").stdout == refs_before


def test_local_latest_rejects_cache_bucket_symlink_before_inspecting_source(
    tmp_path: Path,
    git_fixture: GitFixture,
) -> None:
    cache_root = tmp_path / "cache"
    runner = RecordingProcessRunner()
    source = GitSource(cache_root=cache_root, runner=runner)
    expected_bucket = source.cache_path_for(git_fixture.work)
    cache_root.mkdir()
    expected_bucket.symlink_to(git_fixture.remote, target_is_directory=True)

    with pytest.raises(GitSourceError, match="symlink"):
        source.resolve(git_fixture.work, PowerContextRef(kind="latest"))

    assert runner.calls == []


def test_resolve_rejects_broken_cache_bucket_symlink_before_any_git_command(
    tmp_path: Path,
    git_fixture: GitFixture,
) -> None:
    cache_root = tmp_path / "cache"
    runner = RecordingProcessRunner()
    source = GitSource(cache_root=cache_root, runner=runner)
    expected_bucket = source.cache_path_for(git_fixture.remote)
    cache_root.mkdir()
    expected_bucket.symlink_to(tmp_path / "missing-bare-repository", target_is_directory=True)

    with pytest.raises(GitSourceError, match="symlink"):
        source.resolve(git_fixture.remote, PowerContextRef(kind="branch", value="feature"))

    assert runner.calls == []


def test_resolve_rejects_cache_root_symlink_before_any_git_command(
    tmp_path: Path,
    git_fixture: GitFixture,
) -> None:
    actual_root = tmp_path / "actual-cache"
    actual_root.mkdir()
    cache_root = tmp_path / "cache-link"
    cache_root.symlink_to(actual_root, target_is_directory=True)
    runner = RecordingProcessRunner()
    source = GitSource(cache_root=cache_root, runner=runner)

    with pytest.raises(GitSourceError, match="root must not be a symlink"):
        source.resolve(git_fixture.remote, PowerContextRef(kind="branch", value="feature"))

    assert runner.calls == []
    assert list(actual_root.iterdir()) == []


def test_materialize_requires_nonexistent_target(
    source: GitSource,
    git_fixture: GitFixture,
    tmp_path: Path,
) -> None:
    resolved = source.resolve(git_fixture.remote, PowerContextRef(kind="branch", value="feature"))
    target = tmp_path / "exists"
    target.mkdir()

    with pytest.raises(GitSourceError, match="must not exist"):
        source.materialize(resolved, target)


def test_materialize_rejects_broken_symlink_target(
    source: GitSource,
    git_fixture: GitFixture,
    tmp_path: Path,
) -> None:
    resolved = source.resolve(git_fixture.remote, PowerContextRef(kind="branch", value="feature"))
    destination = tmp_path / "must-remain-missing"
    target = tmp_path / "broken-link"
    target.symlink_to(destination)

    with pytest.raises(GitSourceError, match="must not exist"):
        source.materialize(resolved, target)

    assert not destination.exists()


def test_materialize_revalidates_resolved_cache_provenance_before_any_git_command(
    tmp_path: Path,
    git_fixture: GitFixture,
) -> None:
    runner = RecordingProcessRunner()
    source = GitSource(cache_root=tmp_path / "cache", runner=runner)
    resolved = source.resolve(git_fixture.remote, PowerContextRef(kind="branch", value="feature"))
    calls_after_resolve = len(runner.calls)
    external_root = tmp_path / "external"
    external_root.mkdir()
    external_mirror = external_root / "mirror.git"
    resolved.cache_path.rename(external_mirror)
    resolved.cache_path.symlink_to(external_mirror, target_is_directory=True)
    config_before = (external_mirror / "config").read_bytes()
    refs_before = git(external_mirror, "show-ref").stdout
    target = tmp_path / "materialized"

    with pytest.raises(GitSourceError, match="symlink"):
        source.materialize(resolved, target)

    assert len(runner.calls) == calls_after_resolve
    assert not target.exists()
    assert not target.is_symlink()
    assert (external_mirror / "config").read_bytes() == config_before
    assert git(external_mirror, "show-ref").stdout == refs_before


def test_materialize_rejects_resolved_source_owned_by_different_cache_root(
    source: GitSource,
    git_fixture: GitFixture,
    tmp_path: Path,
) -> None:
    resolved = source.resolve(git_fixture.remote, PowerContextRef(kind="branch", value="feature"))
    unrelated = GitSource(cache_root=tmp_path / "unrelated-cache", runner=FailIfProcessRuns())
    target = tmp_path / "materialized"

    with pytest.raises(GitSourceError, match="cache root"):
        unrelated.materialize(resolved, target)

    assert not target.exists()
    assert not target.is_symlink()


def test_materialize_rejects_cache_bucket_from_a_different_source_before_any_git_command(
    tmp_path: Path,
    git_fixture: GitFixture,
) -> None:
    second_fixture_root = tmp_path / "second-fixture"
    second_fixture_root.mkdir()
    second_fixture = create_git_fixture(second_fixture_root)
    runner = RecordingProcessRunner()
    source = GitSource(cache_root=tmp_path / "cache", runner=runner)
    resolved = source.resolve(git_fixture.remote, PowerContextRef(kind="branch", value="feature"))
    unrelated = source.resolve(second_fixture.remote, PowerContextRef(kind="branch", value="feature"))
    forged = ResolvedGitSource(
        source=resolved.source,
        requested=resolved.requested,
        sha=resolved.sha,
        cache_root=resolved.cache_root,
        cache_path=unrelated.cache_path,
    )
    calls_after_resolve = len(runner.calls)
    target = tmp_path / "materialized"

    with pytest.raises(GitSourceError, match="source identity"):
        source.materialize(forged, target)

    assert len(runner.calls) == calls_after_resolve
    assert not target.exists()
    assert not target.is_symlink()


def test_materialize_rejects_missing_pin_before_clone(
    tmp_path: Path,
    git_fixture: GitFixture,
) -> None:
    runner = RecordingProcessRunner()
    source = GitSource(cache_root=tmp_path / "cache", runner=runner)
    resolved = source.resolve(git_fixture.remote, PowerContextRef(kind="branch", value="feature"))
    pin_ref = f"refs/powercontext-eval/pins/{resolved.sha}"
    git(resolved.cache_path, "update-ref", "-d", pin_ref)
    calls_after_resolve = len(runner.calls)
    target = tmp_path / "materialized"

    with pytest.raises(GitSourceError, match="pin"):
        source.materialize(resolved, target)

    assert not any("clone" in command for command in runner.commands[calls_after_resolve:])
    assert not target.exists()
    assert not target.is_symlink()


def test_materialize_rejects_pin_that_points_to_a_different_commit_before_clone(
    tmp_path: Path,
    git_fixture: GitFixture,
) -> None:
    runner = RecordingProcessRunner()
    source = GitSource(cache_root=tmp_path / "cache", runner=runner)
    resolved = source.resolve(git_fixture.remote, PowerContextRef(kind="branch", value="feature"))
    assert resolved.sha != git_fixture.initial_sha
    pin_ref = f"refs/powercontext-eval/pins/{resolved.sha}"
    git(resolved.cache_path, "update-ref", pin_ref, git_fixture.initial_sha)
    calls_after_resolve = len(runner.calls)
    target = tmp_path / "materialized"

    with pytest.raises(GitSourceError, match="pin"):
        source.materialize(resolved, target)

    assert not any("clone" in command for command in runner.commands[calls_after_resolve:])
    assert not target.exists()
    assert not target.is_symlink()


def test_materialize_checkout_failure_is_atomic_and_retryable(
    git_fixture: GitFixture,
    tmp_path: Path,
) -> None:
    runner = FailCheckoutOnceRunner()
    source = GitSource(cache_root=tmp_path / "cache", runner=runner)
    resolved = source.resolve(git_fixture.remote, PowerContextRef(kind="branch", value="feature"))
    publish_root = tmp_path / "publish"
    publish_root.mkdir()
    target = publish_root / "checkout"

    with pytest.raises(GitSourceError, match="check out"):
        source.materialize(resolved, target)

    assert not target.exists()
    assert not target.is_symlink()
    assert list(publish_root.iterdir()) == []

    retried = source.materialize(resolved, target)

    assert retried == target
    assert git(target, "rev-parse", "HEAD").stdout.strip() == resolved.sha
    assert list(publish_root.iterdir()) == [target]


def test_atomic_publish_directory_never_replaces_existing_target(tmp_path: Path) -> None:
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    (temporary / "candidate.txt").write_text("candidate\n", encoding="utf-8")
    target = tmp_path / "target"
    target.mkdir()
    marker = target / "owner.txt"
    marker.write_text("competitor\n", encoding="utf-8")
    original_inode = target.stat().st_ino

    with pytest.raises(FileExistsError):
        git_source_module._atomic_publish_directory(temporary, target)

    assert target.stat().st_ino == original_inode
    assert marker.read_text(encoding="utf-8") == "competitor\n"
    assert temporary.is_dir()
    assert (temporary / "candidate.txt").read_text(encoding="utf-8") == "candidate\n"


@pytest.mark.parametrize(
    ("architecture", "syscall_number"),
    [
        ("x86_64", 316),
        ("amd64", 316),
        ("aarch64", 276),
        ("arm64", 276),
    ],
)
def test_linux_atomic_publish_falls_back_to_raw_renameat2_syscall(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    architecture: str,
    syscall_number: int,
) -> None:
    temporary = tmp_path / "temporary"
    target = tmp_path / "target"
    libc = FakeLibcWithoutRenameat2()
    monkeypatch.setattr(git_source_module.sys, "platform", "linux")
    monkeypatch.setattr(platform, "machine", lambda: architecture)
    monkeypatch.setattr(git_source_module.ctypes, "CDLL", lambda *_args, **_kwargs: libc)

    git_source_module._atomic_publish_directory(temporary, target)

    assert libc.syscall.calls == [
        (
            syscall_number,
            -100,
            os.fsencode(temporary),
            -100,
            os.fsencode(target),
            1,
        )
    ]
    assert libc.syscall.argtypes == [
        git_source_module.ctypes.c_long,
        git_source_module.ctypes.c_int,
        git_source_module.ctypes.c_char_p,
        git_source_module.ctypes.c_int,
        git_source_module.ctypes.c_char_p,
        git_source_module.ctypes.c_uint,
    ]
    assert libc.syscall.restype is git_source_module.ctypes.c_long


def test_linux_raw_renameat2_syscall_preserves_existing_target_on_eexist(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    (temporary / "candidate.txt").write_text("candidate\n", encoding="utf-8")
    target = tmp_path / "target"
    target.mkdir()
    marker = target / "owner.txt"
    marker.write_text("competitor\n", encoding="utf-8")
    original_inode = target.stat().st_ino
    libc = FakeLibcWithoutRenameat2(returncode=-1, error_number=errno.EEXIST)
    monkeypatch.setattr(git_source_module.sys, "platform", "linux")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(git_source_module.ctypes, "CDLL", lambda *_args, **_kwargs: libc)

    with pytest.raises(FileExistsError):
        git_source_module._atomic_publish_directory(temporary, target)

    assert target.stat().st_ino == original_inode
    assert marker.read_text(encoding="utf-8") == "competitor\n"
    assert temporary.is_dir()
    assert (temporary / "candidate.txt").read_text(encoding="utf-8") == "candidate\n"


@pytest.mark.parametrize("error_number", [errno.ENOSYS, errno.EINVAL])
def test_linux_raw_renameat2_syscall_fails_closed_on_kernel_rejection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error_number: int,
) -> None:
    libc = FakeLibcWithoutRenameat2(returncode=-1, error_number=error_number)
    monkeypatch.setattr(git_source_module.sys, "platform", "linux")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(git_source_module.ctypes, "CDLL", lambda *_args, **_kwargs: libc)

    with pytest.raises(OSError) as caught:
        git_source_module._atomic_publish_directory(tmp_path / "temporary", tmp_path / "target")

    assert caught.value.errno == error_number
    assert len(libc.syscall.calls) == 1


def test_linux_raw_renameat2_syscall_fails_closed_on_unknown_architecture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    libc = FakeLibcWithoutRenameat2()
    monkeypatch.setattr(git_source_module.sys, "platform", "linux")
    monkeypatch.setattr(platform, "machine", lambda: "mystery-cpu")
    monkeypatch.setattr(git_source_module.ctypes, "CDLL", lambda *_args, **_kwargs: libc)

    with pytest.raises(OSError) as caught:
        git_source_module._atomic_publish_directory(tmp_path / "temporary", tmp_path / "target")

    assert caught.value.errno == errno.ENOTSUP
    assert libc.syscall.calls == []


def test_materialize_atomic_publish_race_preserves_competing_target(
    tmp_path: Path,
    git_fixture: GitFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = GitSource(cache_root=tmp_path / "cache")
    resolved = source.resolve(git_fixture.remote, PowerContextRef(kind="branch", value="feature"))
    publish_root = tmp_path / "publish"
    publish_root.mkdir()
    target = publish_root / "checkout"
    state: dict[str, int] = {}
    real_publish = git_source_module._atomic_publish_directory

    def publish_after_competitor_arrives(temporary: Path, destination: Path) -> None:
        destination.mkdir()
        (destination / "owner.txt").write_text("competitor\n", encoding="utf-8")
        state["inode"] = destination.stat().st_ino
        real_publish(temporary, destination)

    monkeypatch.setattr(git_source_module, "_atomic_publish_directory", publish_after_competitor_arrives)

    with pytest.raises(GitSourceError, match="already exists"):
        source.materialize(resolved, target)

    assert target.stat().st_ino == state["inode"]
    assert (target / "owner.txt").read_text(encoding="utf-8") == "competitor\n"
    assert list(publish_root.iterdir()) == [target]


def test_resolve_missing_exact_ref_raises_typed_error(source: GitSource, git_fixture: GitFixture) -> None:
    with pytest.raises(GitSourceError, match="could not resolve"):
        source.resolve(git_fixture.remote, PowerContextRef(kind="branch", value="missing"))


def test_resolve_rejects_existing_mirror_for_a_different_origin_without_fetching(
    tmp_path: Path,
    git_fixture: GitFixture,
) -> None:
    second_fixture_root = tmp_path / "second-fixture"
    second_fixture_root.mkdir()
    second_fixture = create_git_fixture(second_fixture_root)
    runner = RejectFetchRunner()
    source = GitSource(cache_root=tmp_path / "cache", runner=runner)
    cache_path = source.cache_path_for(git_fixture.remote)
    cache_path.parent.mkdir()
    git(tmp_path, "clone", "--mirror", str(second_fixture.remote), str(cache_path))
    config_before = (cache_path / "config").read_bytes()
    refs_before = git(cache_path, "show-ref").stdout

    with pytest.raises(GitSourceError, match="origin"):
        source.resolve(git_fixture.remote, PowerContextRef(kind="branch", value="feature"))

    assert not any("fetch" in command for command in runner.commands)
    assert (cache_path / "config").read_bytes() == config_before
    assert git(cache_path, "show-ref").stdout == refs_before


def test_resolve_rejects_existing_mirror_with_multiple_origins_without_fetching(
    tmp_path: Path,
    git_fixture: GitFixture,
) -> None:
    second_fixture_root = tmp_path / "second-fixture"
    second_fixture_root.mkdir()
    second_fixture = create_git_fixture(second_fixture_root)
    runner = RejectFetchRunner()
    source = GitSource(cache_root=tmp_path / "cache", runner=runner)
    cache_path = source.cache_path_for(git_fixture.remote)
    cache_path.parent.mkdir()
    git(tmp_path, "clone", "--mirror", str(second_fixture.remote), str(cache_path))
    git(cache_path, "config", "--add", "remote.origin.url", str(git_fixture.remote.resolve()))
    config_before = (cache_path / "config").read_bytes()
    refs_before = git(cache_path, "show-ref").stdout

    with pytest.raises(GitSourceError, match="origin") as caught:
        source.resolve(git_fixture.remote, PowerContextRef(kind="branch", value="feature"))

    assert caught.value.__cause__ is None
    assert ("git", "config", "--get-all", "remote.origin.url") in runner.commands
    assert not any("fetch" in command for command in runner.commands)
    assert (cache_path / "config").read_bytes() == config_before
    assert git(cache_path, "show-ref").stdout == refs_before


def test_resolve_rejects_existing_mirror_without_an_origin_and_discards_command_result(
    tmp_path: Path,
    git_fixture: GitFixture,
) -> None:
    runner = RejectFetchRunner()
    source = GitSource(cache_root=tmp_path / "cache", runner=runner)
    cache_path = source.cache_path_for(git_fixture.remote)
    cache_path.parent.mkdir()
    git(tmp_path, "clone", "--mirror", str(git_fixture.remote), str(cache_path))
    git(cache_path, "config", "--unset-all", "remote.origin.url")
    refs_before = git(cache_path, "show-ref").stdout

    with pytest.raises(GitSourceError, match="origin") as caught:
        source.resolve(git_fixture.remote, PowerContextRef(kind="branch", value="feature"))

    assert caught.value.__cause__ is None
    assert not hasattr(caught.value, "result")
    assert ("git", "config", "--get-all", "remote.origin.url") in runner.commands
    assert not any("fetch" in command for command in runner.commands)
    assert git(cache_path, "show-ref").stdout == refs_before


def test_origin_mismatch_error_does_not_retain_existing_origin_credentials(
    tmp_path: Path,
    git_fixture: GitFixture,
) -> None:
    runner = RejectFetchRunner()
    source = GitSource(cache_root=tmp_path / "cache", runner=runner)
    cache_path = source.cache_path_for(git_fixture.remote)
    cache_path.parent.mkdir()
    git(tmp_path, "clone", "--mirror", str(git_fixture.remote), str(cache_path))
    credential_origin = "https://encoded%2Duser:decoded%40secret@example.invalid/org/repo.git"
    git(cache_path, "config", "remote.origin.url", credential_origin)

    with pytest.raises(GitSourceError, match="origin") as caught:
        source.resolve(git_fixture.remote, PowerContextRef(kind="branch", value="feature"))

    retained = f"{caught.value!s}\n{caught.value!r}\n{caught.value.__cause__!r}"
    for secret in (credential_origin, "encoded%2Duser", "decoded%40secret", "encoded-user", "decoded@secret"):
        assert secret not in retained
    assert caught.value.__cause__ is None
    assert not any("fetch" in command for command in runner.commands)


def test_resolve_does_not_interpret_revision_syntax_as_part_of_exact_ref(
    source: GitSource,
    git_fixture: GitFixture,
) -> None:
    with pytest.raises(GitSourceError, match="could not resolve"):
        source.resolve(git_fixture.remote, PowerContextRef(kind="branch", value="feature^{commit}"))


def _probe_mirror_lock(lock_path: Path, connection: Connection) -> None:
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CLOEXEC)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            connection.send("blocked")
        else:
            connection.send("unexpectedly-acquired")
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        assert connection.recv() == "retry"
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        connection.send("acquired")
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)
        connection.close()


class FailIfProcessRuns(ProcessRunner):
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str | Path,
        timeout: float | None = None,
        cancel_event: threading.Event | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = True,
        secrets: Sequence[str] = (),
        input_bytes: bytes | None = None,
        stdout_sink: BinaryIO | None = None,
    ) -> CommandResult:
        del cancel_event, input_bytes, stdout_sink
        raise AssertionError("normalization and cache key calculation must not access the network")


class RecordingProcessRunner(ProcessRunner):
    def __init__(self) -> None:
        self.calls: list[tuple[float | None, dict[str, str]]] = []
        self.commands: list[tuple[str, ...]] = []

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str | Path,
        timeout: float | None = None,
        cancel_event: threading.Event | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = True,
        secrets: Sequence[str] = (),
        input_bytes: bytes | None = None,
        stdout_sink: BinaryIO | None = None,
    ) -> CommandResult:
        del cancel_event, input_bytes
        self.calls.append((timeout, dict(env or {})))
        self.commands.append(tuple(argv))
        return super().run(
            argv,
            cwd=cwd,
            timeout=timeout,
            env=env,
            check=check,
            secrets=secrets,
            stdout_sink=stdout_sink,
        )


class RejectFetchRunner(RecordingProcessRunner):
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str | Path,
        timeout: float | None = None,
        cancel_event: threading.Event | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = True,
        secrets: Sequence[str] = (),
        input_bytes: bytes | None = None,
        stdout_sink: BinaryIO | None = None,
    ) -> CommandResult:
        if "fetch" in argv:
            self.commands.append(tuple(argv))
            raise AssertionError("an unvalidated existing mirror must never be fetched")
        return super().run(
            argv,
            cwd=cwd,
            timeout=timeout,
            cancel_event=cancel_event,
            env=env,
            check=check,
            secrets=secrets,
            input_bytes=input_bytes,
            stdout_sink=stdout_sink,
        )


class FakeCFunction:
    def __init__(self, *, returncode: int = 0, error_number: int = 0) -> None:
        self._returncode = returncode
        self._error_number = error_number
        self.calls: list[tuple[object, ...]] = []
        self.argtypes: list[object] | None = None
        self.restype: object | None = None

    def __call__(self, *arguments: object) -> int:
        self.calls.append(arguments)
        git_source_module.ctypes.set_errno(self._error_number)
        return self._returncode


class FakeLibcWithoutRenameat2:
    def __init__(self, *, returncode: int = 0, error_number: int = 0) -> None:
        self.syscall = FakeCFunction(returncode=returncode, error_number=error_number)


class ControlledGitRunner(ProcessRunner):
    def __init__(self, sha: str) -> None:
        self.sha = sha
        self.calls: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
        self.origin: str | None = None

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str | Path,
        timeout: float | None = None,
        cancel_event: threading.Event | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = True,
        secrets: Sequence[str] = (),
        input_bytes: bytes | None = None,
        stdout_sink: BinaryIO | None = None,
    ) -> CommandResult:
        del cancel_event, input_bytes, stdout_sink
        command = tuple(argv)
        self.calls.append((command, tuple(secrets)))
        if "clone" in command:
            clone_index = command.index("clone")
            self.origin = command[clone_index + 2]
            Path(command[-1]).mkdir()
        if command[1:4] == ("remote", "set-url", "origin"):
            self.origin = command[4]
        stdout = f"{self.sha}\n" if "rev-parse" in command and "--verify" in command else ""
        return CommandResult(command, str(cwd), 0, stdout, "")


class FailCheckoutOnceRunner(ProcessRunner):
    def __init__(self) -> None:
        self.failed = False

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str | Path,
        timeout: float | None = None,
        cancel_event: threading.Event | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = True,
        secrets: Sequence[str] = (),
        input_bytes: bytes | None = None,
        stdout_sink: BinaryIO | None = None,
    ) -> CommandResult:
        if not self.failed and "checkout" in argv:
            self.failed = True
            result = CommandResult(tuple(argv), str(cwd), 73, "", "injected checkout failure\n")
            raise CommandFailed("injected checkout failure", result)
        return super().run(
            argv,
            cwd=cwd,
            timeout=timeout,
            cancel_event=cancel_event,
            env=env,
            check=check,
            secrets=secrets,
            input_bytes=input_bytes,
            stdout_sink=stdout_sink,
        )


def test_every_git_command_has_finite_timeout_and_noninteractive_environment(
    tmp_path: Path,
    git_fixture: GitFixture,
) -> None:
    runner = RecordingProcessRunner()
    source = GitSource(cache_root=tmp_path / "cache", runner=runner, command_timeout=12.5)

    source.resolve(git_fixture.remote, PowerContextRef(kind="branch", value="feature"))

    assert runner.calls
    for timeout, env in runner.calls:
        assert timeout == 12.5
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["GCM_INTERACTIVE"] == "never"


def test_git_command_timeout_defaults_to_300_seconds(tmp_path: Path, git_fixture: GitFixture) -> None:
    runner = RecordingProcessRunner()
    source = GitSource(cache_root=tmp_path / "cache", runner=runner)

    source.resolve(git_fixture.remote, PowerContextRef(kind="branch", value="feature"))

    assert runner.calls
    assert {timeout for timeout, _env in runner.calls} == {300.0}


@pytest.mark.parametrize("timeout", [0, -1])
def test_git_source_rejects_nonpositive_command_timeout(tmp_path: Path, timeout: float) -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        GitSource(cache_root=tmp_path / "cache", command_timeout=timeout)


def test_resolve_rejects_embedded_password_query_and_fragment_before_cache_creation(tmp_path: Path) -> None:
    raw_source = "https://user:password@example.com/org/repo.git?access_token=query-secret#fragment-secret"
    source = GitSource(cache_root=tmp_path / "cache", runner=FailIfProcessRuns())
    expected_bucket = source.cache_path_for(raw_source)

    with pytest.raises(GitSourceError, match="credential helper"):
        source.resolve(raw_source, PowerContextRef(kind="commit", value="a" * 40))

    assert not expected_bucket.exists()
    assert not expected_bucket.is_symlink()
    assert not (tmp_path / "cache").exists()


@pytest.mark.parametrize(
    "raw_source",
    [
        "secret%2Dtoken@example.invalid:org/repo.git",
        "ssh://secret%2Dtoken@example.invalid/org/repo.git",
    ],
)
def test_username_only_ssh_failure_redacts_raw_and_decoded_username_but_keeps_diagnostics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw_source: str,
) -> None:
    executable_directory = tmp_path / "bin"
    executable_directory.mkdir()
    fake_ssh = executable_directory / "ssh"
    fake_ssh.write_text(
        '#!/bin/sh\nprintf \'ssh-arg=%s\\n\' "$@" >&2\nif [ "${1-}" = "-G" ]; then exit 0; fi\nexit 19\n',
        encoding="utf-8",
    )
    fake_ssh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{executable_directory}{os.pathsep}{os.environ['PATH']}")
    source = GitSource(cache_root=tmp_path / "cache")

    with pytest.raises(GitSourceError, match="clone Git mirror") as caught:
        source.resolve(raw_source, PowerContextRef(kind="commit", value="a" * 40))

    cause = caught.value.__cause__
    assert isinstance(cause, CommandError)
    rendered = "\n".join(
        [
            str(caught.value),
            repr(caught.value),
            str(cause),
            repr(cause),
            repr(cause.result),
            *cause.result.argv,
            cause.result.stdout,
            cause.result.stderr,
        ]
    )
    for secret in (raw_source, "secret%2Dtoken", "secret-token"):
        assert secret not in rendered
    assert "example.invalid" in cause.result.stderr
    assert "org/repo.git" in cause.result.stderr


@pytest.mark.parametrize(
    ("raw_source", "normalized"),
    [
        ("ssh://deploy%2Dtoken@example.com/org/repo.git", "ssh://example.com/org/repo.git"),
        ("deploy%2Dtoken@example.com:org/repo.git", "example.com:org/repo.git"),
    ],
)
def test_username_only_ssh_uses_command_scoped_rewrite_and_sanitized_origin(
    tmp_path: Path,
    raw_source: str,
    normalized: str,
) -> None:
    sha = "a" * 40
    runner = ControlledGitRunner(sha)
    source = GitSource(cache_root=tmp_path / "cache", runner=runner)

    resolved = source.resolve(raw_source, PowerContextRef(kind="commit", value=sha))

    clone_call = next(call for call, _secrets in runner.calls if "clone" in call)
    clone_index = clone_call.index("clone")
    assert clone_call[clone_index + 2] == normalized
    assert "-c" in clone_call
    assert f"url.{raw_source}.insteadOf={normalized}" in clone_call
    assert not any(call[1:4] == ("remote", "set-url", "origin") for call, _secrets in runner.calls)
    assert runner.origin == normalized
    assert resolved.source == normalized
    assert "deploy%2Dtoken" not in repr(resolved)
    assert "deploy-token" not in repr(resolved)
    sensitive_calls = [
        (call, secrets) for call, secrets in runner.calls if any(raw_source in argument for argument in call)
    ]
    assert sensitive_calls
    assert all(
        raw_source in secrets and "deploy%2Dtoken" in secrets and "deploy-token" in secrets
        for _call, secrets in sensitive_calls
    )


@pytest.mark.parametrize(
    ("raw_source", "normalized"),
    [
        (
            "https://user:super-secret-token@example.com/org/repo.git?access_token=query-secret#fragment-secret",
            "https://example.com/org/repo.git",
        ),
        (
            "ssh://deploy:private-key@example.com:2222/org/repo.git?token=query-secret#fragment-secret",
            "ssh://example.com:2222/org/repo.git",
        ),
        ("git@example.com:org/repo.git", "example.com:org/repo.git"),
        ("example.com:org/repo.git", "example.com:org/repo.git"),
    ],
)
def test_credential_url_normalization_and_cache_path_never_leak_secrets(
    tmp_path: Path,
    raw_source: str,
    normalized: str,
) -> None:
    source = GitSource(cache_root=tmp_path / "cache", runner=FailIfProcessRuns())

    actual_normalized = source.normalize_source(raw_source)
    cache_path = source.cache_path_for(raw_source)

    assert actual_normalized == normalized
    rendered = f"{actual_normalized}\n{cache_path}"
    for secret in ("user", "deploy", "super-secret-token", "private-key", "query-secret", "fragment-secret"):
        assert secret not in rendered
    assert re.fullmatch(r"[0-9a-f]{64}", cache_path.name)


def test_local_source_normalizes_to_absolute_resolved_path(tmp_path: Path) -> None:
    relative = Path("evaluation")
    source = GitSource(cache_root=tmp_path / "cache", runner=FailIfProcessRuns())

    assert source.normalize_source(relative) == str(relative.resolve())


def test_scp_style_source_sanitization_and_cache_key_do_not_run_process(tmp_path: Path) -> None:
    raw_source = "private-token@example.com:org/repo.git"
    source = GitSource(cache_root=tmp_path / "cache", runner=FailIfProcessRuns())

    normalized = source.normalize_source(raw_source)
    cache_path = source.cache_path_for(raw_source)
    anonymous_cache_path = source.cache_path_for("example.com:org/repo.git")

    assert normalized == "example.com:org/repo.git"
    assert cache_path == anonymous_cache_path
    rendered = f"{normalized}\n{cache_path}"
    assert "private-token" not in rendered


def test_resolved_git_source_rejects_noncanonical_sha(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="40 lowercase"):
        ResolvedGitSource(
            source="/source",
            requested=PowerContextRef(kind="latest"),
            sha="ABC",
            cache_root=tmp_path / "cache",
            cache_path=tmp_path / "cache" / ("a" * 64),
        )
