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

from __future__ import annotations

import os
from pathlib import Path

import pytest

from powercontext.service.environment import (
    ProtectedEnvironmentFileError,
    ensure_protected_file,
    load_protected_environment_file,
    protect_directory,
    read_protected_file,
    write_protected_file,
)


def test_private_record_roundtrip_and_replacement_invalidates_old_identity(tmp_path: Path) -> None:
    directory = protect_directory(tmp_path / "control", create=True)
    path = directory / "operation.json"
    original = write_protected_file(path, '{"message":"服务已停止"}\n'.encode())
    assert read_protected_file(path, expected=original).content == '{"message":"服务已停止"}\n'.encode()
    updated = write_protected_file(path, b'{"outcome":"succeeded"}\n')
    assert read_protected_file(path, expected=updated).content == b'{"outcome":"succeeded"}\n'
    with pytest.raises(ProtectedEnvironmentFileError, match="identity changed"):
        read_protected_file(path, expected=original)


def test_written_configuration_is_accepted_by_the_service_loader(tmp_path: Path) -> None:
    path = tmp_path / "private" / "server.env"
    identity = write_protected_file(path, b"POWERCONTEXT_SERVER_HTTP_PORT=8123\n")
    loaded = load_protected_environment_file(path, expected=identity)
    assert loaded.values == {"POWERCONTEXT_SERVER_HTTP_PORT": "8123"}


def test_ensuring_private_lock_file_preserves_existing_identity_and_content(tmp_path: Path) -> None:
    path = tmp_path / "private" / "operations.lock"
    created = ensure_protected_file(path)
    assert ensure_protected_file(path) == created
    assert read_protected_file(path).content == b""
    path.write_bytes(b"existing owner evidence")
    existing = read_protected_file(path)
    assert ensure_protected_file(path) == existing.identity
    assert read_protected_file(path).content == existing.content


def test_reading_oversized_record_does_not_return_its_content(tmp_path: Path) -> None:
    path = tmp_path / "private" / "record"
    write_protected_file(path, b"not-for-diagnostics")
    with pytest.raises(ProtectedEnvironmentFileError, match="size limit") as failure:
        read_protected_file(path, max_bytes=4)
    assert "not-for-diagnostics" not in str(failure.value)


def test_linked_control_path_cannot_read_or_replace_another_file(tmp_path: Path) -> None:
    target = tmp_path / "private" / "target"
    write_protected_file(target, b"preserve")
    link = target.with_name("linked")
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("creating symbolic links is not available to this account")
    for operation in (lambda: read_protected_file(link), lambda: write_protected_file(link, b"replacement")):
        with pytest.raises(ProtectedEnvironmentFileError, match="links"):
            operation()
    assert read_protected_file(target).content == b"preserve"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission mode regression")
def test_existing_shared_directory_is_rejected_without_changing_permissions(tmp_path: Path) -> None:
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    with pytest.raises(ProtectedEnvironmentFileError, match="private"):
        write_protected_file(shared / "secret", b"private")
    assert not (shared / "secret").exists()
    assert shared.stat().st_mode & 0o777 == 0o755


@pytest.mark.skipif(os.name == "nt", reason="Windows refuses renaming a directory with an open temporary file")
def test_replaced_parent_directory_is_rejected_before_publishing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = protect_directory(tmp_path / "control", create=True)
    original_fsync = os.fsync
    replaced = False

    def replace_parent(descriptor: int) -> None:
        nonlocal replaced
        original_fsync(descriptor)
        if not replaced:
            replaced = True
            parent.rename(tmp_path / "moved")
            parent.mkdir(mode=0o700)

    monkeypatch.setattr(os, "fsync", replace_parent)
    with pytest.raises(ProtectedEnvironmentFileError, match="directory changed"):
        write_protected_file(parent / "record", b"private")
    assert not (parent / "record").exists()
