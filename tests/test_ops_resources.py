# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Small real archives/filesystem; HTTPS responses are simulated, never release qualification."""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import sys
import tarfile
import zipfile
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from powercontext.ops import resources
from powercontext.ops.resources import ResourceCatalog, ResourceError, ResourceSpec, ResourceStore, verify
from powercontext.service.environment import protect_directory, write_protected_file

_FILES = {
    "python/pythonw.exe": b"windowed fixture",
    "python/python.exe": b"console fixture",
    "LICENSE": b"fixture license",
}


def _archive(files: dict[str, bytes] | None = None, *, archive_format: str = "zip") -> bytes:
    output = io.BytesIO()
    if archive_format == "zip":
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in (files or _FILES).items():
                archive.writestr(name, content)
    else:
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            for name, content in (files or _FILES).items():
                member = tarfile.TarInfo(name)
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
    return output.getvalue()


def _spec(archive: bytes, **overrides: object) -> ResourceSpec:
    values = {
        "resource_id": "fixture-windows-x64",
        "server_version": "1.0.0",
        "python_version": "3.12.13",
        "platform": "windows",
        "architecture": "x86_64",
        "archive_format": "zip",
        "url": "https://producer.example/server.zip",
        "allowed_origins": ["https://producer.example", "https://cdn.example"],
        "archive_sha256": hashlib.sha256(archive).hexdigest(),
        "archive_size": len(archive),
        "unpacked_size": sum(map(len, _FILES.values())),
        "python_relative": "python/pythonw.exe",
        "ops_protocol_min": 1,
        "ops_protocol_max": 1,
        "source_refs": ["test fixture; not a release"],
        "license_refs": ["LICENSE"],
        "qualification_ref": "test-only-unqualified",
        "files": [
            {
                "path": name,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "executable": name.endswith(".exe"),
            }
            for name, content in _FILES.items()
        ],
    }
    values.update(overrides)
    return ResourceSpec.model_validate(values)


@pytest.fixture(autouse=True)
def platform_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resources, "_current_platform", lambda: ("windows", "x86_64"))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native path budget")
def test_unsupported_destination_is_rejected_before_download_or_directory_creation(tmp_path: Path) -> None:
    destination = tmp_path / ("a" * 100) / ("b" * 100)
    cache = tmp_path / "cache"
    with pytest.raises(ResourceError, match="resource_path_unsupported"):
        ResourceStore(cache).prepare(_spec(_archive()), destination)
    assert not cache.exists()
    assert not destination.parent.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native path budget")
def test_destination_budget_counts_utf16_and_checks_nested_directory_creation(tmp_path: Path) -> None:
    spec = _spec(_archive())
    resources.validate_destination(spec, tmp_path / "supported")
    # Non-BMP characters require two Windows WCHARs, even though Python len is one.
    with pytest.raises(ResourceError, match="resource_path_unsupported"):
        resources.validate_destination(spec, tmp_path / ("\U0001f4e6" * 128))
    nested = spec.model_copy(
        update={
            "files": (
                resources.ResourceFile(
                    path="a" * 240 + "/x",
                    size=1,
                    sha256="0" * 64,
                ),
            )
        }
    )
    with pytest.raises(ResourceError, match="resource_path_unsupported"):
        resources.validate_destination(nested, tmp_path)


def _transport(monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]) -> None:
    client = httpx.Client
    monkeypatch.setattr(
        resources.httpx, "Client", lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs)
    )


@pytest.mark.parametrize("archive_format", ["zip", "tar.gz"])
def test_verified_cache_and_complete_tree_are_reusable_without_fetching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, archive_format: str
) -> None:
    archive = _archive(archive_format=archive_format)
    spec = _spec(archive, archive_format=archive_format)
    fetched: list[str] = []

    def fetch(request: httpx.Request) -> httpx.Response:
        fetched.append(str(request.url))
        return httpx.Response(200, stream=httpx.ByteStream(archive))

    _transport(monkeypatch, fetch)
    store = ResourceStore(tmp_path / "cache")
    prepared = store.prepare(spec, tmp_path / "staging")
    assert prepared.spec == spec
    assert prepared.identity["inode"] == prepared.directory.stat().st_ino
    assert {name: (prepared.directory / name).read_bytes() for name in _FILES} == _FILES
    verify(spec, prepared.directory)
    second = store.prepare(spec, tmp_path / "staging")
    assert second.directory != prepared.directory
    assert len(fetched) == 1
    (tmp_path / "cache" / (spec.archive_sha256 + ".archive")).write_bytes(b"damaged cache")
    store.prepare(spec, tmp_path / "staging")
    assert len(fetched) == 2
    (prepared.directory / "LICENSE").write_bytes(b"modified")
    with pytest.raises(ResourceError, match="resource_corrupt"):
        verify(spec, prepared.directory)


@pytest.mark.parametrize(
    "target", ["http://producer.example/file", "https://foreign.example/file", "https://user:pass@cdn.example/file"]
)
def test_redirect_is_rejected_before_contacting_unauthorized_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    requests: list[str] = []

    def fetch(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(302, headers={"location": target})

    _transport(monkeypatch, fetch)
    with pytest.raises(ResourceError, match="resource_source_invalid"):
        ResourceStore(tmp_path / "cache").prepare(_spec(_archive()), tmp_path / "stage")
    assert requests == ["https://producer.example/server.zip"]
    assert not list((tmp_path / "stage").iterdir())


def test_authorized_redirect_is_followed_and_bytes_must_match(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = _archive()

    def fetch(request: httpx.Request) -> httpx.Response:
        if request.url.host == "producer.example":
            return httpx.Response(307, headers={"location": "https://cdn.example/fixed.zip"})
        return httpx.Response(200, stream=httpx.ByteStream(archive))

    _transport(monkeypatch, fetch)
    prepared = ResourceStore(tmp_path / "cache").prepare(_spec(archive), tmp_path / "stage")
    verify(_spec(archive), prepared.directory)


@pytest.mark.parametrize("failure", ["digest", "too_long", "truncated"])
def test_bad_download_never_becomes_a_cache_or_extracted_resource(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    archive = _archive()
    content = {
        "digest": archive[:-1] + bytes([archive[-1] ^ 1]),
        "too_long": archive + b"x",
        "truncated": archive[:-1],
    }[failure]
    _transport(monkeypatch, lambda request: httpx.Response(200, stream=httpx.ByteStream(content)))
    with pytest.raises(ResourceError, match="resource_corrupt"):
        ResourceStore(tmp_path / "cache").prepare(_spec(archive), tmp_path / "stage")
    assert not list((tmp_path / "cache").glob("*.archive"))
    assert not list((tmp_path / "cache").glob("*.partial"))
    assert not list((tmp_path / "stage").iterdir())


@pytest.mark.parametrize(
    "member",
    ["../escaped", "/absolute", "C:/escaped", "python/../escape", "python/name:stream", "python/NUL", "python/name. "],
)
def test_archive_paths_never_escape_private_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, member: str) -> None:
    archive = _archive({**_FILES, member: b"outside"})
    _transport(monkeypatch, lambda request: httpx.Response(200, stream=httpx.ByteStream(archive)))
    with pytest.raises(ResourceError):
        ResourceStore(tmp_path / "cache").prepare(_spec(archive), tmp_path / "stage")
    assert not list((tmp_path / "stage").iterdir())
    assert not (tmp_path / "escaped").exists()
    assert not (tmp_path / "escape").exists()


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_tar_links_and_special_files_are_not_materialized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        member = tarfile.TarInfo("python/pythonw.exe")
        member.type = {"symlink": tarfile.SYMTYPE, "hardlink": tarfile.LNKTYPE, "fifo": tarfile.FIFOTYPE}[kind]
        member.linkname = str(tmp_path / "outside")
        archive.addfile(member)
    payload = output.getvalue()
    _transport(monkeypatch, lambda request: httpx.Response(200, stream=httpx.ByteStream(payload)))
    with pytest.raises(ResourceError, match="resource_archive_invalid"):
        ResourceStore(tmp_path / "cache").prepare(_spec(payload, archive_format="tar.gz"), tmp_path / "stage")
    assert not (tmp_path / "outside").exists()


def test_zip_symlink_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        member = zipfile.ZipInfo("python/pythonw.exe")
        member.create_system = 3
        member.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(member, "../../outside")
    payload = output.getvalue()
    _transport(monkeypatch, lambda request: httpx.Response(200, stream=httpx.ByteStream(payload)))
    with pytest.raises(ResourceError, match="resource_archive_invalid"):
        ResourceStore(tmp_path / "cache").prepare(_spec(payload), tmp_path / "stage")


def test_tar_metadata_expansion_is_bounded_before_materializing_a_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz", format=tarfile.PAX_FORMAT) as archive:
        member = tarfile.TarInfo("python/pythonw.exe")
        member.size = len(_FILES[member.name])
        member.pax_headers = {"comment": "x" * (2 * 1024 * 1024)}
        archive.addfile(member, io.BytesIO(_FILES[member.name]))
    payload = output.getvalue()
    _transport(monkeypatch, lambda request: httpx.Response(200, stream=httpx.ByteStream(payload)))
    with pytest.raises(ResourceError, match="resource_archive_invalid"):
        ResourceStore(tmp_path / "cache").prepare(_spec(payload, archive_format="tar.gz"), tmp_path / "stage")
    assert not list((tmp_path / "stage").rglob("pythonw.exe"))


def test_interrupted_download_can_restart_without_reusing_partial_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _archive()
    cancellation = [False]

    class Interrupted(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            yield archive[:10]
            cancellation[0] = True
            yield archive[10:]

    responses = iter([httpx.Response(200, stream=Interrupted()), httpx.Response(200, stream=httpx.ByteStream(archive))])
    _transport(monkeypatch, lambda request: next(responses))
    store = ResourceStore(tmp_path / "cache")
    with pytest.raises(ResourceError, match="resource_cancelled"):
        store.prepare(_spec(archive), tmp_path / "stage", cancelled=lambda: cancellation[0])
    assert not list((tmp_path / "cache").glob("*.archive"))
    assert not list((tmp_path / "cache").glob("*.partial"))
    cancellation[0] = False
    prepared = store.prepare(_spec(archive), tmp_path / "stage")
    verify(_spec(archive), prepared.directory)


def test_cancelled_extraction_discards_only_its_own_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = _archive()
    _transport(monkeypatch, lambda request: httpx.Response(200, stream=httpx.ByteStream(archive)))
    store = ResourceStore(tmp_path / "cache")
    retained = store.prepare(_spec(archive), tmp_path / "stage")

    def cancel_after_first_file() -> bool:
        return any(
            stage != retained.directory and (stage / "python/pythonw.exe").exists()
            for stage in (tmp_path / "stage").glob("resource-*")
        )

    with pytest.raises(ResourceError, match="resource_cancelled"):
        store.prepare(_spec(archive), tmp_path / "stage", cancelled=cancel_after_first_file)
    assert list((tmp_path / "stage").iterdir()) == [retained.directory]
    verify(_spec(archive), retained.directory)
    assert len(list((tmp_path / "cache").glob("*.archive"))) == 1


def test_failure_cleanup_does_not_remove_a_replaced_stage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = _archive()
    _transport(monkeypatch, lambda request: httpx.Response(200, stream=httpx.ByteStream(archive)))
    replacement: list[Path] = []

    def replace_and_cancel() -> bool:
        for stage in (tmp_path / "stage").glob("resource-*"):
            stage.rename(stage.with_name("moved-stage"))
            protect_directory(stage, create=True)
            (stage / "unrelated.txt").write_text("preserve this replacement", encoding="utf-8")
            replacement.append(stage)
            return True
        return False

    with pytest.raises(ResourceError, match="resource_cancelled"):
        ResourceStore(tmp_path / "cache").prepare(_spec(archive), tmp_path / "stage", cancelled=replace_and_cancel)
    assert len(replacement) == 1
    assert (replacement[0] / "unrelated.txt").read_text(encoding="utf-8") == "preserve this replacement"
    assert (tmp_path / "stage/moved-stage").is_dir()


def test_catalog_is_pinned_private_and_read_only(tmp_path: Path) -> None:
    private = protect_directory(tmp_path / "private", create=True)
    content = json.dumps({"format_version": 1, "resources": [_spec(_archive()).model_dump(mode="json")]}).encode()
    path = private / "catalog.json"
    write_protected_file(path, content)
    catalog = ResourceCatalog.read(path, hashlib.sha256(content).hexdigest())
    assert catalog.select_current().python_console_relative == "python/python.exe"
    assert catalog.select_current().digest == catalog.select("fixture-windows-x64").digest
    assert "files" not in catalog.select_current().as_dict()
    with pytest.raises(ResourceError, match="resource_catalog_invalid"):
        ResourceCatalog.read(path, "0" * 64)
    with pytest.raises(ResourceError, match="resource_catalog_invalid"):
        ResourceCatalog.read(tmp_path / "missing" / "catalog.json", hashlib.sha256(content).hexdigest())
    assert not (tmp_path / "missing").exists()


@pytest.mark.parametrize("name", ["../escape", "python/CON.txt", "python/name.", "python/file:stream"])
def test_manifest_cannot_authorize_unsafe_paths(name: str) -> None:
    file = {"path": name, "size": 1, "sha256": "0" * 64}
    with pytest.raises(ValidationError):
        _spec(_archive(), files=[file])


def test_foreign_platform_is_rejected_before_creating_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _spec(_archive())
    monkeypatch.setattr(resources, "_current_platform", lambda: ("macos", "arm64"))
    with pytest.raises(ResourceError, match="resource_platform_unsupported"):
        ResourceStore(tmp_path / "cache").prepare(spec, tmp_path / "stage")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX symlink creation; Windows reparse rejection uses the same lstat check"
)
def test_existing_tree_symlink_is_rejected_without_reading_target(tmp_path: Path) -> None:
    root = protect_directory(tmp_path / "private", create=True)
    (root / "outside").symlink_to(tmp_path / "missing")
    with pytest.raises(ResourceError, match="resource_unsafe_path"):
        resources.validate_owned_tree(root)
