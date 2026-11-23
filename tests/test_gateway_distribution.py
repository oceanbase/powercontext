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

"""Distribution integrity and release identity at the downloadable archive boundary."""

import io
import json
import os
import tarfile
from pathlib import Path

import gateway_distribution as distribution
import pytest

pytestmark = pytest.mark.skipif(os.name == "nt", reason="Gateway distributions currently target Linux and macOS")
VERSION = "1.6.0"
COMMIT = "a" * 40
TARGET = "x86_64-unknown-linux-gnu"


def fixture_binary(path: Path, target: str = TARGET) -> Path:
    # A controlled executable for archive contract tests; native execution is
    # independently exercised by the four-platform distribution workflow.
    path.write_text("#!/bin/sh\nprintf '%s\\n' '" + distribution.version_line(VERSION, COMMIT, target) + "'\n")
    path.chmod(0o755)
    return path


@pytest.fixture
def archive(tmp_path):
    return distribution.package(fixture_binary(tmp_path / "fixture"), tmp_path / "dist", VERSION, COMMIT, TARGET)


def rewrite_archive(archive, replacement):
    with tarfile.open(archive, "r:gz") as source:
        files = {}
        for member in source.getmembers():
            stream = source.extractfile(member)
            assert stream is not None
            files[member.name] = stream.read()
    replacement(files)
    with tarfile.open(archive, "w:gz") as output:
        for name, content in files.items():
            member = tarfile.TarInfo(name)
            member.mode = 0o755 if name == "pc-gateway" else 0o644
            member.size = len(content)
            output.addfile(member, io.BytesIO(content))
    archive.with_name(archive.name + ".sha256").write_text(f"{distribution.sha256(archive)}  {archive.name}\n")


def test_downloaded_archive_preserves_executable_and_identity(archive, tmp_path):
    binary = distribution.unpack(archive, tmp_path / "installed", VERSION, COMMIT, TARGET)
    assert binary.is_file() and os.access(binary, os.X_OK)
    metadata = json.loads((binary.parent / "manifest.json").read_text())
    assert metadata["source_commit"] == COMMIT
    assert metadata["minimum_platform"] == "Linux glibc 2.39+"


def test_corrupt_download_is_rejected_before_extraction(archive, tmp_path):
    with archive.open("ab") as output:
        output.write(b"corrupt")
    with pytest.raises(ValueError, match="Archive checksum mismatch"):
        distribution.unpack(archive, tmp_path / "installed", VERSION, COMMIT, TARGET)
    assert not (tmp_path / "installed").exists()


def test_wrong_commit_is_rejected(archive):
    with pytest.raises(ValueError, match="Binary or release identity mismatch"):
        distribution.inspect_archive(archive, VERSION, "b" * 40, TARGET)


def test_binary_tampering_is_rejected_even_with_new_archive_checksum(archive):
    rewrite_archive(archive, lambda files: files.update({"pc-gateway": b"different binary"}))
    with pytest.raises(ValueError, match="Binary or release identity mismatch"):
        distribution.inspect_archive(archive, VERSION, COMMIT, TARGET)


def test_path_traversal_is_rejected_before_extraction(archive, tmp_path):
    rewrite_archive(archive, lambda files: files.update({"../escape": b"bad"}))
    with pytest.raises(ValueError, match="Invalid archive members"):
        distribution.unpack(archive, tmp_path / "installed", VERSION, COMMIT, TARGET)
    assert not (tmp_path / "escape").exists()


def test_binary_report_must_match_manifest_after_unpack(archive, tmp_path):
    def replace_binary(files):
        files["pc-gateway"] = b"#!/bin/sh\necho 'wrong build'\n"
        metadata = json.loads(files["manifest.json"])
        metadata["binary_sha256"] = distribution.hashlib.sha256(files["pc-gateway"]).hexdigest()
        files["manifest.json"] = json.dumps(metadata).encode()

    rewrite_archive(archive, replace_binary)
    with pytest.raises(ValueError, match="Executable version mismatch"):
        distribution.unpack(archive, tmp_path / "installed", VERSION, COMMIT, TARGET)


def test_catalog_requires_every_platform_with_same_release_identity(tmp_path):
    output = tmp_path / "dist"
    for target in distribution.TARGETS:
        binary = fixture_binary(tmp_path / "fixture", target)
        distribution.package(binary, output, VERSION, COMMIT, target)
    distribution.catalog(output, VERSION, COMMIT)
    metadata = json.loads((output / "powercontext-gateway-manifest.json").read_text())
    assert {item["target"] for item in metadata} == set(distribution.TARGETS)
    assert len((output / "powercontext-gateway-SHA256SUMS").read_text().splitlines()) == 4
    next(output.glob("*.tar.gz")).unlink()
    with pytest.raises(ValueError, match="All four Gateway platforms are required"):
        distribution.catalog(output, VERSION, COMMIT)
