from __future__ import annotations

import json
import math
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any, cast

import pytest

import powercontext_eval.artifacts as artifacts_module
from powercontext_eval.artifacts import (
    ArmState,
    ArmStateMachine,
    ArtifactAlreadyExists,
    ArtifactDurabilityUnknown,
    ArtifactStore,
    InvalidStateTransition,
    SecretDetected,
    StateAlreadyExists,
    UnsafeArtifactPath,
)


def test_linux_noreplace_rename_falls_back_to_raw_syscall_without_glibc_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, ...]] = []

    class FakeCall:
        argtypes: tuple[object, ...] = ()
        restype: object = None

        def __call__(self, *arguments: object) -> int:
            calls.append(arguments)
            return 0

    class OldGlibc:
        syscall = FakeCall()

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(artifacts_module, "CDLL", lambda *_args, **_kwargs: OldGlibc())
    monkeypatch.setattr(os, "uname", lambda: SimpleNamespace(machine="x86_64"))

    ArtifactStore._rename_noreplace(9, "source", "target")

    assert calls == [(316, 9, b"source", 9, b"target", 1)]


def test_write_json_is_canonical_and_replaces_existing_file(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    store.write_json("state/manifest.json", {"z": [2, 1], "a": "雪"})
    assert (tmp_path / "artifacts/state/manifest.json").read_bytes() == (
        b'{\n  "a": "\xe9\x9b\xaa",\n  "z": [\n    2,\n    1\n  ]\n}\n'
    )

    store.write_json("state/manifest.json", {"updated": True})
    assert (tmp_path / "artifacts/state/manifest.json").read_bytes() == b'{\n  "updated": true\n}\n'


def test_text_and_bytes_writes_are_exact(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    store.write_text("text.txt", "hello\n")
    store.write_bytes("data.bin", b"\x00\xff")
    assert (tmp_path / "artifacts/text.txt").read_bytes() == b"hello\n"
    assert (tmp_path / "artifacts/data.bin").read_bytes() == b"\x00\xff"


def test_json_rejects_non_standard_non_finite_numbers_without_artifact(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    with pytest.raises(ValueError):
        store.write_json("invalid.json", {"value": math.nan})
    assert not (tmp_path / "artifacts/invalid.json").exists()


@pytest.mark.parametrize("path", ["", ".", "..", "../escape", "a/../../escape", "/absolute", "a/\x00/b"])
def test_unsafe_relative_paths_are_rejected(tmp_path: Path, path: str) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    with pytest.raises(UnsafeArtifactPath):
        store.write_text(path, "data")
    assert not (tmp_path / "escape").exists()


def test_parent_and_final_symlinks_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "linked-parent").symlink_to(outside, target_is_directory=True)
    (root / "linked-file").symlink_to(outside / "file")
    store = ArtifactStore(root)

    with pytest.raises(UnsafeArtifactPath):
        store.write_text("linked-parent/escape.txt", "data")
    with pytest.raises(UnsafeArtifactPath):
        store.write_text("linked-file", "data")
    assert not (outside / "escape.txt").exists()
    assert not (outside / "file").exists()


def test_symlink_root_is_rejected(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(UnsafeArtifactPath):
        ArtifactStore(linked)


def test_symlink_in_root_ancestor_is_rejected_without_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnsafeArtifactPath):
        ArtifactStore(linked / "nested")

    assert list(outside.iterdir()) == []


def test_parent_component_in_root_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(UnsafeArtifactPath):
        ArtifactStore(tmp_path / "first" / ".." / "second")
    assert not (tmp_path / "first").exists()


@pytest.mark.parametrize("secret", ["", b""])
def test_empty_forbidden_values_are_rejected(tmp_path: Path, secret: str | bytes) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        ArtifactStore(tmp_path / "artifacts", forbidden_values=[secret])


@pytest.mark.parametrize("secret", ["super-secret", b"binary-secret"])
def test_secret_is_rejected_without_target_temp_or_message_leak(tmp_path: Path, secret: str | bytes) -> None:
    root = tmp_path / "artifacts"
    store = ArtifactStore(root, forbidden_values=[secret])
    payload = secret.encode() if isinstance(secret, str) else secret

    with pytest.raises(SecretDetected) as caught:
        store.write_bytes("nested/result.txt", b"prefix-" + payload + b"-suffix")

    assert payload.decode() not in str(caught.value)
    assert not (root / "nested/result.txt").exists()
    assert not (root / "nested").exists()


def test_failed_atomic_replace_preserves_old_target_and_cleans_own_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "artifacts"
    store = ArtifactStore(root)
    store.write_text("state.json", "old")

    def fail_replace(*args: object, **kwargs: object) -> None:
        raise OSError("injected replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="injected"):
        store.write_text("state.json", "new")

    assert (root / "state.json").read_text() == "old"
    assert [path.name for path in root.iterdir()] == ["state.json"]


def test_exclusive_create_does_not_replace_existing_artifact(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    store = ArtifactStore(root)
    store.create_text("state.json", "first")
    with pytest.raises(ArtifactAlreadyExists):
        store.create_text("state.json", "second")
    assert (root / "state.json").read_text() == "first"
    assert [path.name for path in root.iterdir()] == ["state.json"]


@pytest.mark.parametrize("replace_temp", [False, True])
def test_exclusive_cleanup_never_removes_published_target_or_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replace_temp: bool
) -> None:
    root = tmp_path / "artifacts"
    store = ArtifactStore(root)
    original_cleanup = store._cleanup_exclusive_temp
    observed_temp: list[str] = []

    def remove_or_replace_temp(parent_fd: int, temporary_name: str, target_name: str, expected: os.stat_result) -> None:
        observed_temp.append(temporary_name)
        os.unlink(temporary_name, dir_fd=parent_fd)
        if replace_temp:
            replacement_fd = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent_fd,
            )
            os.write(replacement_fd, b"third-party")
            os.close(replacement_fd)
        original_cleanup(parent_fd, temporary_name, target_name, expected)

    monkeypatch.setattr(store, "_cleanup_exclusive_temp", remove_or_replace_temp)
    store.create_text("result.txt", "published")

    assert (root / "result.txt").read_text() == "published"
    if replace_temp:
        assert (root / observed_temp[0]).read_text() == "third-party"
    else:
        assert sorted(path.name for path in root.iterdir()) == ["result.txt"]


def test_directory_swap_after_preflight_rolls_back_and_leaves_no_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "artifacts"
    outside = tmp_path / "outside"
    outside.mkdir()
    store = ArtifactStore(root)
    store.write_text("nested/result.txt", "old")
    original_verify = store._verify_logical_parent
    calls = 0

    def swap_after_preflight(parts: tuple[str, ...], parent_fd: int) -> None:
        nonlocal calls
        original_verify(parts, parent_fd)
        calls += 1
        if calls == 1:
            (root / "nested").rename(outside / "nested")
            (root / "nested").symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(store, "_verify_logical_parent", swap_after_preflight)
    with pytest.raises(ArtifactDurabilityUnknown) as caught:
        store.write_text("nested/result.txt", "new-sensitive-payload")

    assert (outside / "nested/result.txt").read_text() == "old"
    assert caught.value.recovery_name is not None
    assert (outside / "nested" / caught.value.recovery_name).read_text() == "old"
    assert "new-sensitive-payload" not in "\n".join(
        path.read_text(errors="ignore") for path in (outside / "nested").iterdir() if path.is_file()
    )


def test_moved_temp_and_same_name_replacement_are_detected_and_cleaned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "artifacts"
    store = ArtifactStore(root)
    store.write_text("result.txt", "old")
    original_assert = store._assert_temp_named
    moved = False

    def move_after_check(parent_fd: int, temporary_name: str, temporary_fd: int) -> None:
        nonlocal moved
        original_assert(parent_fd, temporary_name, temporary_fd)
        if not moved:
            os.rename(temporary_name, ".moved-temp", src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            replacement_fd = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=parent_fd,
            )
            os.write(replacement_fd, b"attacker-replacement")
            os.close(replacement_fd)
            moved = True

    monkeypatch.setattr(store, "_assert_temp_named", move_after_check)
    with pytest.raises(ArtifactDurabilityUnknown) as caught:
        store.write_text("result.txt", "new-sensitive-payload")

    assert (root / "result.txt").read_text() == "old"
    assert caught.value.recovery_name is not None
    assert (root / caught.value.recovery_name).read_text() == "old"
    assert "new-sensitive-payload" not in "\n".join(
        path.read_text(errors="ignore") for path in root.iterdir() if path.is_file()
    )


@pytest.mark.parametrize("mode", ["exclusive", "absent", "overwrite"])
def test_name_to_inode_publication_race_is_transactionally_rolled_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    root = tmp_path / "artifacts"
    store = ArtifactStore(root)
    if mode == "overwrite":
        store.write_text("result.txt", "old")
    original_assert = store._assert_temp_named
    raced = False

    def replace_after_check(parent_fd: int, temporary_name: str, temporary_fd: int) -> None:
        nonlocal raced
        original_assert(parent_fd, temporary_name, temporary_fd)
        if not raced:
            os.rename(temporary_name, ".trusted-moved", src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            attacker_fd = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent_fd,
            )
            os.write(attacker_fd, b"attacker-target")
            os.close(attacker_fd)
            raced = True

    monkeypatch.setattr(store, "_assert_temp_named", replace_after_check)
    expected_error = ArtifactDurabilityUnknown if mode == "overwrite" else UnsafeArtifactPath
    with pytest.raises(expected_error) as caught:
        if mode == "exclusive":
            store.create_text("result.txt", "trusted-payload")
        else:
            store.write_text("result.txt", "trusted-payload")

    if mode == "overwrite":
        assert (root / "result.txt").read_text() == "old"
        assert isinstance(caught.value, ArtifactDurabilityUnknown)
        assert caught.value.recovery_name is not None
        assert (root / caught.value.recovery_name).read_text() == "old"
    else:
        assert not (root / "result.txt").exists()
    assert "attacker-target" not in "\n".join(
        path.read_text(errors="ignore") for path in root.iterdir() if path.is_file()
    )
    assert "trusted-payload" not in "\n".join(
        path.read_text(errors="ignore") for path in root.iterdir() if path.is_file()
    )


def test_postpublication_replacement_is_preserved_and_reports_unknown_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "artifacts"
    store = ArtifactStore(root)
    original_observe = store._observe_published_target
    replaced = False

    def replace_after_observation(parent_fd: int, target_name: str) -> os.stat_result:
        nonlocal replaced
        observed = original_observe(parent_fd, target_name)
        if not replaced:
            os.rename(target_name, ".published-moved", src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            attacker_fd = os.open(
                target_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent_fd,
            )
            os.write(attacker_fd, b"third-party-target")
            os.close(attacker_fd)
            replaced = True
        return observed

    monkeypatch.setattr(store, "_observe_published_target", replace_after_observation)
    with pytest.raises(ArtifactDurabilityUnknown) as caught:
        store.write_text("result.txt", "trusted-payload")

    assert (root / "result.txt").read_text() == "third-party-target"
    assert (root / ".published-moved").read_text() == "trusted-payload"
    assert caught.value.target_name == "result.txt"
    assert caught.value.recovery_name == ".published-moved"
    assert "trusted-payload" not in str(caught.value)


def test_overwrite_rollback_does_not_replace_target_changed_after_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "artifacts"
    store = ArtifactStore(root)
    store.write_text("result.txt", "old-payload")
    original_verify = store._verify_logical_parent
    verify_calls = 0

    def fail_postpublication_parent_check(parts: tuple[str, ...], parent_fd: int) -> None:
        nonlocal verify_calls
        verify_calls += 1
        if verify_calls == 2:
            raise UnsafeArtifactPath("injected logical parent failure")
        original_verify(parts, parent_fd)

    original_observe = store._observe_published_target
    observe_calls = 0

    def replace_after_rollback_observation(parent_fd: int, target_name: str) -> os.stat_result:
        nonlocal observe_calls
        observed = original_observe(parent_fd, target_name)
        observe_calls += 1
        if observe_calls == 2:
            os.rename(target_name, ".published-moved", src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            third_party_fd = os.open(
                target_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent_fd,
            )
            os.write(third_party_fd, b"third-party-target")
            os.close(third_party_fd)
        return observed

    monkeypatch.setattr(store, "_verify_logical_parent", fail_postpublication_parent_check)
    monkeypatch.setattr(store, "_observe_published_target", replace_after_rollback_observation)

    with pytest.raises(ArtifactDurabilityUnknown) as caught:
        store.write_text("result.txt", "trusted-payload")

    assert (root / "result.txt").read_text() == "third-party-target"
    assert (root / ".published-moved").read_text() == "trusted-payload"
    assert caught.value.recovery_name is not None
    assert caught.value.recovery_name != "result.txt"
    assert (root / caught.value.recovery_name).read_text() == "old-payload"


def test_overwrite_rollback_preserves_target_created_after_quarantine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "artifacts"
    store = ArtifactStore(root)
    store.write_text("result.txt", "old-payload")
    original_verify = store._verify_logical_parent
    verify_calls = 0

    def fail_postpublication_parent_check(parts: tuple[str, ...], parent_fd: int) -> None:
        nonlocal verify_calls
        verify_calls += 1
        if verify_calls == 2:
            raise UnsafeArtifactPath("injected logical parent failure")
        original_verify(parts, parent_fd)

    original_quarantine = store._quarantine_target
    quarantine_name: str | None = None

    def create_target_after_quarantine(parent_fd: int, target_name: str) -> tuple[str, os.stat_result]:
        nonlocal quarantine_name
        quarantine_name, metadata = original_quarantine(parent_fd, target_name)
        third_party_fd = os.open(
            target_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent_fd,
        )
        os.write(third_party_fd, b"concurrent-third-party")
        os.close(third_party_fd)
        return quarantine_name, metadata

    monkeypatch.setattr(store, "_verify_logical_parent", fail_postpublication_parent_check)
    monkeypatch.setattr(store, "_quarantine_target", create_target_after_quarantine)

    with pytest.raises(ArtifactDurabilityUnknown) as caught:
        store.write_text("result.txt", "trusted-payload")

    assert (root / "result.txt").read_text() == "concurrent-third-party"
    assert quarantine_name is not None
    assert (root / quarantine_name).read_text() == "trusted-payload"
    assert caught.value.recovery_name is not None
    assert (root / caught.value.recovery_name).read_text() == "old-payload"


def test_overwrite_rollback_retains_backup_when_restored_target_is_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "artifacts"
    store = ArtifactStore(root)
    store.write_text("result.txt", "old-payload")
    original_verify = store._verify_logical_parent
    verify_calls = 0

    def fail_postpublication_parent_check(parts: tuple[str, ...], parent_fd: int) -> None:
        nonlocal verify_calls
        verify_calls += 1
        if verify_calls == 2:
            raise UnsafeArtifactPath("injected logical parent failure")
        original_verify(parts, parent_fd)

    original_link = os.link
    replaced = False

    def replace_after_backup_restore(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        target: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        nonlocal replaced
        original_link(
            source,
            target,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )
        source_name = os.fsdecode(source)
        target_name = os.fsdecode(target)
        if not replaced and ".backup-" in source_name and target_name == "result.txt":
            assert dst_dir_fd is not None
            os.unlink(target, dir_fd=dst_dir_fd)
            third_party_fd = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=dst_dir_fd,
            )
            os.write(third_party_fd, b"post-restore-third-party")
            os.close(third_party_fd)
            replaced = True

    monkeypatch.setattr(store, "_verify_logical_parent", fail_postpublication_parent_check)
    monkeypatch.setattr(os, "link", replace_after_backup_restore)

    with pytest.raises(ArtifactDurabilityUnknown) as caught:
        store.write_text("result.txt", "trusted-payload")

    assert (root / "result.txt").read_text() == "post-restore-third-party"
    assert caught.value.recovery_name is not None
    assert ".backup-" in caught.value.recovery_name
    assert (root / caught.value.recovery_name).read_text() == "old-payload"


def test_durable_write_orders_temp_fsync_replace_and_parent_fsync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    events: list[tuple[str, int]] = []
    real_fsync = os.fsync
    real_replace = os.replace

    def record_fsync(fd: int) -> None:
        events.append(("fsync", fd))
        real_fsync(fd)

    def record_replace(
        source: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        target: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        events.append(("replace", -1))
        real_replace(source, target, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    monkeypatch.setattr(os, "fsync", record_fsync)
    monkeypatch.setattr(os, "replace", record_replace)
    store.write_text("nested/result.txt", "data")

    replace_index = events.index(("replace", -1))
    assert any(event[0] == "fsync" for event in events[:replace_index])
    assert any(event[0] == "fsync" for event in events[replace_index + 1 :])


def test_parent_fsync_failure_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    real_fsync = os.fsync
    calls = 0

    def fail_second_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected parent fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_second_fsync)
    with pytest.raises(OSError, match="parent fsync"):
        store.write_text("result.txt", "data")
    assert not (tmp_path / "artifacts/result.txt").exists()
    assert list((tmp_path / "artifacts").iterdir()) == []


def test_failed_publish_and_failed_rollback_fsync_reports_unknown_durability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    real_fsync = os.fsync
    calls = 0

    def fail_commit_and_recovery_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls >= 2:
            raise OSError("injected persistent fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_commit_and_recovery_fsync)
    with pytest.raises(ArtifactDurabilityUnknown) as caught:
        store.write_text("result.txt", "unknown-durability-payload")
    assert caught.value.target_name == "result.txt"
    assert "unknown-durability-payload" not in str(caught.value)


LEGAL_TRANSITIONS = {
    ArmState.CREATED: (ArmState.REVISIONS_RESOLVED, ArmState.CONFIGURATION_ERROR),
    ArmState.REVISIONS_RESOLVED: (
        ArmState.GOLD_VERIFIED,
        ArmState.GOLD_CHECK_FAILED,
        ArmState.INFRASTRUCTURE_ERROR,
    ),
    ArmState.GOLD_VERIFIED: (ArmState.ENVIRONMENT_READY, ArmState.INFRASTRUCTURE_ERROR),
    ArmState.ENVIRONMENT_READY: (ArmState.CODEX_RUNNING, ArmState.INFRASTRUCTURE_ERROR),
    ArmState.CODEX_RUNNING: (ArmState.PATCH_CAPTURED, ArmState.CODEX_ERROR, ArmState.CODEX_TIMEOUT),
    ArmState.PATCH_CAPTURED: (ArmState.EVALUATED, ArmState.EVALUATION_ERROR),
    ArmState.EVALUATED: (ArmState.TREATMENT_VALIDATED, ArmState.INVALID_TREATMENT),
    ArmState.TREATMENT_VALIDATED: (ArmState.REPORTED,),
}


@pytest.mark.parametrize(("source", "targets"), LEGAL_TRANSITIONS.items())
def test_every_legal_state_transition(tmp_path: Path, source: ArmState, targets: tuple[ArmState, ...]) -> None:
    for target in targets:
        arm_root = tmp_path / f"{source}-{target}"
        store = ArtifactStore(arm_root)
        machine = ArmStateMachine(store, "off", initial_state=source, initial_sequence=7)
        snapshot = machine.transition(target, {"sha": "a" * 40})
        assert snapshot.state is target
        assert snapshot.sequence == 8
        assert json.loads((arm_root / "state.json").read_text()) == {
            "arm": "off",
            "evidence": {"sha": "a" * 40},
            "sequence": 8,
            "state": target.value,
        }


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (ArmState.CREATED, ArmState.CREATED),
        (ArmState.CREATED, ArmState.CODEX_RUNNING),
        (ArmState.CODEX_RUNNING, ArmState.ENVIRONMENT_READY),
        (ArmState.REPORTED, ArmState.CREATED),
        (ArmState.CODEX_ERROR, ArmState.REPORTED),
    ],
)
def test_invalid_transition_preserves_memory_and_disk(tmp_path: Path, source: ArmState, target: ArmState) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    machine = ArmStateMachine(store, "on", initial_state=source, initial_sequence=3)
    before = (tmp_path / "artifacts/state.json").read_bytes()

    with pytest.raises(InvalidStateTransition):
        machine.transition(target, {"reason": "must not persist"})

    assert machine.state is source
    assert machine.sequence == 3
    assert (tmp_path / "artifacts/state.json").read_bytes() == before


def test_unknown_transition_is_typed_and_does_not_change_state(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    machine = ArmStateMachine(store, "off")
    before = (tmp_path / "artifacts/state.json").read_bytes()
    with pytest.raises(InvalidStateTransition):
        machine.transition("invented", {})  # type: ignore[arg-type]
    assert machine.state is ArmState.CREATED
    assert (tmp_path / "artifacts/state.json").read_bytes() == before


def test_second_state_machine_does_not_overwrite_existing_state(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    first = ArmStateMachine(store, "off")
    first.transition(ArmState.REVISIONS_RESOLVED, {"sha": "a" * 40})
    before = (tmp_path / "artifacts/state.json").read_bytes()

    with pytest.raises(StateAlreadyExists):
        ArmStateMachine(store, "off")

    assert (tmp_path / "artifacts/state.json").read_bytes() == before
    assert json.loads(before)["sequence"] == 1


def test_state_evidence_is_canonicalized_and_deeply_frozen(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    machine = ArmStateMachine(store, "on")
    evidence = {"nested": {"items": ["first"], "flag": True}}
    snapshot = machine.transition(ArmState.REVISIONS_RESOLVED, evidence)
    evidence["nested"]["items"].append("mutated")  # type: ignore[index,union-attr]
    evidence["nested"]["flag"] = False  # type: ignore[index]

    assert isinstance(snapshot.evidence, MappingProxyType)
    assert snapshot.as_json()["evidence"] == {"nested": {"flag": True, "items": ["first"]}}
    assert json.loads((tmp_path / "artifacts/state.json").read_text())["evidence"] == {
        "nested": {"flag": True, "items": ["first"]}
    }


@pytest.mark.parametrize("bad_evidence", [{1: "non-string"}, {"value": math.inf}, {"value": object()}])
def test_invalid_state_evidence_leaves_memory_and_disk_unchanged(
    tmp_path: Path, bad_evidence: dict[object, object]
) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    machine = ArmStateMachine(store, "off")
    before = (tmp_path / "artifacts/state.json").read_bytes()
    with pytest.raises((TypeError, ValueError)):
        machine.transition(ArmState.REVISIONS_RESOLVED, cast(Mapping[str, Any], bad_evidence))
    assert machine.state is ArmState.CREATED
    assert machine.sequence == 0
    assert (tmp_path / "artifacts/state.json").read_bytes() == before


def test_unknown_state_durability_does_not_advance_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = ArtifactStore(tmp_path / "artifacts")
    machine = ArmStateMachine(store, "off")
    real_fsync = os.fsync
    calls = 0

    def fail_commit_and_recovery_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls >= 3:
            raise OSError("injected persistent fsync failure")
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_commit_and_recovery_fsync)
    with pytest.raises(ArtifactDurabilityUnknown) as caught:
        machine.transition(ArmState.REVISIONS_RESOLVED, {"marker": "must-not-enter-error"})
    assert caught.value.target_name == "state.json"
    assert machine.state is ArmState.CREATED
    assert machine.sequence == 0
    assert "must-not-enter-error" not in str(caught.value)
