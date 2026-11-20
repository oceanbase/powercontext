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

"""Actual source-build and offline-install regressions for the customer bundle."""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/prepare_bundle_dependencies.py"


def source_distribution(
    path: Path, *, name: str = "jieba", version: str = "0.42.1", backend_name: str = "fixture_backend"
) -> None:
    # A real local PEP 517 backend needs no downloaded build dependencies.
    backend = """from pathlib import Path
import os
import zipfile

def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    version = os.environ.get("FIXTURE_WHEEL_VERSION", "0.42.1")
    filename = f"jieba-{version}-py3-none-any.whl"
    with zipfile.ZipFile(Path(wheel_directory) / filename, "w") as wheel:
        wheel.writestr("jieba/__init__.py", "value = 'locked source'\\n")
        wheel.writestr(f"jieba-{version}.dist-info/METADATA", f"Metadata-Version: 2.1\\nName: jieba\\nVersion: {version}\\n")
        wheel.writestr(f"jieba-{version}.dist-info/WHEEL", "Wheel-Version: 1.0\\nGenerator: fixture\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n")
        wheel.writestr(f"jieba-{version}.dist-info/RECORD", "")
    return filename
"""
    content = {
        "pyproject.toml": f'[build-system]\nrequires = []\nbuild-backend = "{backend_name}"\nbackend-path = ["."]\n',
        "fixture_backend.py": backend,
        "PKG-INFO": "Metadata-Version: 2.1\nName: jieba\nVersion: 0.42.1\n",
    }
    with tarfile.open(path, "w:gz") as archive:
        for member, text in content.items():
            info = tarfile.TarInfo(f"{name}-{version}/{member}")
            payload = text.replace("jieba", name).replace("0.42.1", version).encode()
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))


def wheel_distribution(path: Path, *, name: str = "plugin_fixture", version: str = "1.0") -> None:
    with zipfile.ZipFile(path, "w") as wheel:
        wheel.writestr(f"{name}/__init__.py", "value = 'prebuilt wheel'\n")
        wheel.writestr(
            f"{name}-{version}.dist-info/METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
        )
        wheel.writestr(f"{name}-{version}.dist-info/WHEEL", "Wheel-Version: 1.0\nTag: py3-none-any\n")
        wheel.writestr(f"{name}-{version}.dist-info/RECORD", "")


@pytest.fixture
def bundle(tmp_path: Path, request):
    name, version = getattr(request, "param", ("jieba", "0.42.1"))
    downloads = tmp_path / "index"
    downloads.mkdir()
    source = downloads / f"{name}-{version}.tar.gz"
    source_distribution(source, name=name, version=version)
    wheel = downloads / "plugin_fixture-1.0-py3-none-any.whl"
    wheel_distribution(wheel)
    work = tmp_path / "bundle"
    work.mkdir()
    for export_name, package, artifact in [
        ("upstream-requirements.lock", f"{name}=={version}", source),
        ("upstream-plugin-requirements.lock", "plugin-fixture==1.0", wheel),
    ]:
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        (work / export_name).write_text(f"{package} --hash=sha256:{digest}\n")
    python = tmp_path / "builder/bin/python"
    subprocess.run([sys.executable, "-m", "venv", str(python.parent.parent)], check=True)
    env = {**os.environ, "PIP_NO_INDEX": "1", "PIP_FIND_LINKS": str(downloads), "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
    return work, python, env


def run_stage(bundle, stage: str) -> subprocess.CompletedProcess[str]:
    work, python, env = bundle
    return subprocess.run(
        [str(python), str(SCRIPT), stage, "--work", str(work)], env=env, capture_output=True, text=True
    )


def install(bundle, *, name: str = "consumer") -> subprocess.CompletedProcess[str]:
    work, python, env = bundle
    consumer = work.parent / name
    subprocess.run([str(python), "-m", "venv", str(consumer)], check=True)
    return subprocess.run(
        [
            str(consumer / "bin/python"),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            str(work / "wheelhouse"),
            "--require-hashes",
            "-r",
            str(work / "requirements.lock"),
        ],
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("bundle", [("jieba", "0.42.1"), ("jieba", "0.43.0"), ("source_only", "1.0")], indirect=True)
def test_locked_source_build_installs_offline_with_derived_wheel_hash(bundle):
    work, python, env = bundle
    original = (work / "upstream-requirements.lock").read_bytes()
    requirement = original.decode().split()[0]
    package, version = requirement.split("==")
    old = subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "download",
            "--require-hashes",
            "--only-binary=:all:",
            "-r",
            str(work / "upstream-requirements.lock"),
            "--dest",
            str(work / "old-download"),
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    assert old.returncode != 0 and "No matching distribution found" in old.stderr
    for stage in ("download", "build"):
        result = run_stage(bundle, stage)
        assert result.returncode == 0, result.stdout + result.stderr
    assert (work / "upstream-requirements.lock").read_bytes() == original
    (wheel,) = (work / "wheelhouse").glob("*.whl")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert f"{package.replace('_', '-')}=={version} --hash=sha256:{digest}" in (work / "requirements.lock").read_text()
    assert f"sha256:{digest}" not in original.decode()
    result = install(bundle)
    assert result.returncode == 0, result.stdout + result.stderr
    value = subprocess.check_output(
        [str(work.parent / "consumer/bin/python"), "-c", f"import {package}; print({package}.value)"], text=True
    )
    assert value.strip() == "locked source"
    provenance = json.loads((work / "dependency-provenance.json").read_text())
    assert provenance["dependencies"][0]["sources"][0]["filename"] == f"{package}-{version}.tar.gz"


def test_wrong_locked_source_hash_is_rejected(bundle):
    work, _python, _env = bundle
    (work / "upstream-requirements.lock").write_text("jieba==0.42.1 --hash=sha256:" + "0" * 64 + "\n")
    result = run_stage(bundle, "download")
    assert result.returncode != 0
    assert "HASHES" in result.stderr.upper()
    assert not (work / "requirements.lock").exists()


def test_changed_source_after_download_cannot_produce_install_lock(bundle):
    work, _python, _env = bundle
    result = run_stage(bundle, "download")
    assert result.returncode == 0, result.stdout + result.stderr
    archive = work / "downloads/jieba-0.42.1.tar.gz"
    archive.write_bytes(archive.read_bytes() + b"modified")
    result = run_stage(bundle, "build")
    assert result.returncode != 0 and "hash" in result.stderr.lower()
    assert not (work / "requirements.lock").exists()


def test_modified_built_wheel_is_rejected_by_offline_installer(bundle):
    work, _python, _env = bundle
    for stage in ("download", "build"):
        result = run_stage(bundle, stage)
        assert result.returncode == 0, result.stdout + result.stderr
    (wheel,) = (work / "wheelhouse").glob("*.whl")
    wheel.write_bytes(wheel.read_bytes() + b"modified")
    result = install(bundle)
    assert result.returncode != 0 and "HASHES" in result.stderr.upper()


def test_compatible_locked_wheel_is_preferred_over_source(bundle):
    work, _python, env = bundle
    wheel = Path(env["PIP_FIND_LINKS"]) / "jieba-0.42.1-py3-none-any.whl"
    wheel_distribution(wheel, name="jieba", version="0.42.1")
    requirements = work / "upstream-requirements.lock"
    requirements.write_text(
        requirements.read_text().rstrip() + f" --hash=sha256:{hashlib.sha256(wheel.read_bytes()).hexdigest()}\n"
    )
    for stage in ("download", "build"):
        result = run_stage(bundle, stage)
        assert result.returncode == 0, result.stdout + result.stderr
    result = install(bundle)
    assert result.returncode == 0, result.stdout + result.stderr
    value = subprocess.check_output(
        [str(work.parent / "consumer/bin/python"), "-c", "import jieba; print(jieba.value)"], text=True
    )
    assert value.strip() == "prebuilt wheel"


def test_missing_source_backend_fails_without_publishing_install_lock(bundle):
    work, _python, env = bundle
    source = Path(env["PIP_FIND_LINKS"]) / "source_only-1.0.tar.gz"
    source_distribution(source, name="source_only", version="1.0", backend_name="unavailable_fixture_backend")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    (work / "upstream-requirements.lock").write_text(f"source-only==1.0 --hash=sha256:{digest}\n")
    result = run_stage(bundle, "download")
    assert result.returncode != 0
    assert "unavailable_fixture_backend" in result.stderr
    assert not (work / "requirements.lock").exists()


def test_source_build_cannot_publish_a_different_package_version(bundle):
    work, _python, env = bundle
    result = run_stage(bundle, "download")
    assert result.returncode == 0, result.stdout + result.stderr
    env["FIXTURE_WHEEL_VERSION"] = "9.9"
    result = run_stage(bundle, "build")
    assert result.returncode != 0
    assert "closure" in result.stderr or "inconsistent version" in result.stdout + result.stderr
    assert not (work / "requirements.lock").exists()
