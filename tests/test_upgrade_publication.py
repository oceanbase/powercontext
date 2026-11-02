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

"""Exercise publication inputs with actual offline sdist builds; intercept uploads."""

import io
import json
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest
import run_upgrade_release_gate as runner
from upgrade_release_gate import GateError, sha256


def write_wheel(path, content=b"current implementation", *, version="2.0.0"):
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("powercontext/__init__.py", content)
        archive.writestr(f"powercontext-{version}.dist-info/METADATA", f"Name: powercontext\nVersion: {version}\n")
        archive.writestr(f"powercontext-{version}.dist-info/WHEEL", "Wheel-Version: 1.0\nTag: py3-none-any\n")
        archive.writestr(f"powercontext-{version}.dist-info/RECORD", "")


def write_source(path, content=b"current implementation", *, version="2.0.0", metadata_version=None):
    # No external build dependencies or network. uv invokes a real local PEP 517
    # backend so stale same-version source changes alter the rebuilt wheel.
    backend = f"""import pathlib, zipfile

def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    name = "powercontext-{version}-py3-none-any.whl"
    with zipfile.ZipFile(pathlib.Path(wheel_directory) / name, "w") as archive:
        archive.writestr("powercontext/__init__.py", pathlib.Path("payload.py").read_bytes())
        archive.writestr("powercontext-{version}.dist-info/METADATA", "Name: powercontext\\nVersion: {version}\\n")
        archive.writestr("powercontext-{version}.dist-info/WHEEL", "Wheel-Version: 1.0\\nTag: py3-none-any\\n")
        archive.writestr("powercontext-{version}.dist-info/RECORD", "")
    return name
"""
    files = {
        "PKG-INFO": f"Name: powercontext\nVersion: {metadata_version or version}\n".encode(),
        "pyproject.toml": b'[build-system]\nrequires = []\nbuild-backend = "fixture_backend"\nbackend-path = ["."]\n',
        "fixture_backend.py": backend.encode(),
        "payload.py": content,
    }
    with tarfile.open(path, "w:gz") as archive:
        for name, data in files.items():
            member = tarfile.TarInfo(f"powercontext-{version}/{name}")
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))


@pytest.fixture
def publication(tmp_path, monkeypatch):
    monkeypatch.setenv("UV_OFFLINE", "true")
    dist = tmp_path / "dist"
    dist.mkdir()
    wheel = dist / "powercontext-2.0.0-py3-none-any.whl"
    source = dist / "powercontext-2.0.0.tar.gz"
    write_wheel(wheel)
    write_source(source)
    (dist / ".gitignore").write_text("*")
    return dist, wheel, source, tmp_path / "evidence"


@pytest.mark.parametrize(
    "invalid", ["old-version", "metadata-mismatch", "same-version-stale-content", "extra-file", "missing-sdist"]
)
def test_invalid_publication_set_never_reaches_acceptance(publication, monkeypatch, invalid):
    dist, _wheel, source, output = publication
    if invalid == "old-version":
        source.unlink()
        write_source(dist / "powercontext-1.3.0.tar.gz", version="1.3.0")
    elif invalid == "metadata-mismatch":
        write_source(source, metadata_version="1.3.0")
    elif invalid == "same-version-stale-content":
        write_source(source, b"old implementation")
    elif invalid == "extra-file":
        (dist / "leftover.zip").write_bytes(b"unchecked archive")
    else:
        source.unlink()

    def unexpected_gate(*_args, **_kwargs):
        pytest.fail("Invalid publication inputs must fail before upgrade acceptance")

    monkeypatch.setattr(runner, "gate", unexpected_gate)
    with pytest.raises(GateError):
        runner.accept_distributions(dist, output, release=True, release_notes="fixture")
    assert not (output / "validated-dist").exists()


def test_failed_upgrade_cannot_produce_publishable_files(publication, monkeypatch):
    dist, _, _, output = publication

    def fail(*_args, **_kwargs):
        raise GateError("migration_required")

    monkeypatch.setattr(runner, "gate", fail)
    with pytest.raises(GateError, match="migration_required"):
        runner.accept_distributions(dist, output, release=True, release_notes="fixture")
    assert not (output / "validated-dist").exists()


def test_publish_uses_only_tested_snapshot_even_if_dist_changes(publication, monkeypatch):
    dist, wheel, source, output = publication
    expected = {path.name: sha256(path) for path in (wheel, source)}
    notes = dist.parent / "notes.md"
    notes.write_text("fixture release notes")

    def accept(_root, tested_wheel, evidence, **_kwargs):
        assert sha256(tested_wheel) == expected[wheel.name]
        # A concurrent build must not replace bytes being tested or introduce an
        # unchecked package into the later upload glob.
        write_wheel(wheel, b"replacement after validation")
        write_source(source, b"replacement after validation")
        write_source(dist / "powercontext-1.3.0.tar.gz", version="1.3.0")
        evidence.mkdir()
        (evidence / "result.json").write_text(
            json.dumps({
                "mode": "supported-upgrades",
                "resolved_matrix": {"candidate_cases": [1, 2, 3]},
                "cases": [1, 2, 3],
            })
        )

    monkeypatch.setattr(runner, "gate", accept)
    original_run = runner.subprocess.run
    uploads = []

    def intercept_upload(command, **kwargs):
        if command[:2] == ["uv", "publish"]:
            uploads.extend(Path(name) for name in command[2:])
            return None
        return original_run(command, **kwargs)

    monkeypatch.setattr(runner.subprocess, "run", intercept_upload)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_upgrade_release_gate.py",
            "--dist",
            str(dist),
            "--output",
            str(output),
            "--release",
            "--publish",
            "--release-notes",
            str(notes),
        ],
    )
    runner.main()
    assert {path.name: sha256(path) for path in uploads} == expected
    assert all(path.parent == output / "validated-dist" for path in uploads)
    assert json.loads((output / "publication.json").read_text())["artifacts"] == expected


def test_wheel_only_pr_build_still_runs_upgrade_acceptance(publication, monkeypatch):
    dist, wheel, source, output = publication
    source.unlink()
    tested = []

    def accept(_root, tested_wheel, evidence, **kwargs):
        tested.append(sha256(tested_wheel))
        assert not kwargs["release"]
        evidence.mkdir()

    monkeypatch.setattr(runner, "gate", accept)
    accepted = runner.accept_distributions(dist, output, release=False, release_notes=None)
    assert tested == [sha256(wheel)]
    assert [path.name for path in accepted] == [wheel.name]
