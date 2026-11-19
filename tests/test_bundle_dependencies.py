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


def source_distribution(path: Path, *, name: str = "jieba", version: str = "0.42.1") -> None:
    # A real local PEP 517 backend needs no downloaded build dependencies.
    backend = """from pathlib import Path
import zipfile

def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    filename = "jieba-0.42.1-py3-none-any.whl"
    with zipfile.ZipFile(Path(wheel_directory) / filename, "w") as wheel:
        wheel.writestr("jieba/__init__.py", "value = 'locked source'\\n")
        wheel.writestr("jieba-0.42.1.dist-info/METADATA", "Metadata-Version: 2.1\\nName: jieba\\nVersion: 0.42.1\\n")
        wheel.writestr("jieba-0.42.1.dist-info/WHEEL", "Wheel-Version: 1.0\\nGenerator: fixture\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n")
        wheel.writestr("jieba-0.42.1.dist-info/RECORD", "")
    return filename
"""
    content = {
        "pyproject.toml": '[build-system]\nrequires = []\nbuild-backend = "fixture_backend"\nbackend-path = ["."]\n',
        "fixture_backend.py": backend,
        "PKG-INFO": "Metadata-Version: 2.1\nName: jieba\nVersion: 0.42.1\n",
    }
    with tarfile.open(path, "w:gz") as archive:
        for member, text in content.items():
            info = tarfile.TarInfo(f"{name}-{version}/{member}")
            payload = text.replace("jieba", name).replace("0.42.1", version).encode()
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))


def wheel_distribution(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as wheel:
        wheel.writestr("plugin_fixture/__init__.py", "value = 1\n")
        wheel.writestr(
            "plugin_fixture-1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: plugin-fixture\nVersion: 1.0\n"
        )
        wheel.writestr("plugin_fixture-1.0.dist-info/WHEEL", "Wheel-Version: 1.0\nTag: py3-none-any\n")
        wheel.writestr("plugin_fixture-1.0.dist-info/RECORD", "")


@pytest.fixture
def bundle(tmp_path: Path):
    downloads = tmp_path / "index"
    downloads.mkdir()
    source = downloads / "jieba-0.42.1.tar.gz"
    source_distribution(source)
    wheel = downloads / "plugin_fixture-1.0-py3-none-any.whl"
    wheel_distribution(wheel)
    work = tmp_path / "bundle"
    work.mkdir()
    for name, package, artifact in [
        ("upstream-requirements.lock", "jieba==0.42.1", source),
        ("upstream-plugin-requirements.lock", "plugin-fixture==1.0", wheel),
    ]:
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        (work / name).write_text(f"{package} --hash=sha256:{digest}\n")
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


def test_locked_source_build_installs_offline_with_derived_wheel_hash(bundle):
    work, python, env = bundle
    original = (work / "upstream-requirements.lock").read_bytes()
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
    assert old.returncode != 0 and "No matching distribution found for jieba" in old.stderr
    for stage in ("download", "build"):
        result = run_stage(bundle, stage)
        assert result.returncode == 0, result.stdout + result.stderr
    assert (work / "upstream-requirements.lock").read_bytes() == original
    (wheel,) = (work / "wheelhouse").glob("*.whl")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert f"jieba==0.42.1 --hash=sha256:{digest}" in (work / "requirements.lock").read_text()
    assert f"sha256:{digest}" not in original.decode()
    result = install(bundle)
    assert result.returncode == 0, result.stdout + result.stderr
    value = subprocess.check_output(
        [str(work.parent / "consumer/bin/python"), "-c", "import jieba; print(jieba.value)"], text=True
    )
    assert value.strip() == "locked source"
    provenance = json.loads((work / "dependency-provenance.json").read_text())
    assert provenance["dependencies"][0]["sources"][0]["filename"] == "jieba-0.42.1.tar.gz"


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


def test_jieba_exception_keeps_other_source_distributions_disabled(bundle):
    work, _python, env = bundle
    unsupported = Path(env["PIP_FIND_LINKS"]) / "source_only-1.0.tar.gz"
    source_distribution(unsupported, name="source_only", version="1.0")
    digest = hashlib.sha256(unsupported.read_bytes()).hexdigest()
    with (work / "upstream-requirements.lock").open("a") as requirements:
        requirements.write(f"source-only==1.0 --hash=sha256:{digest}\n")
    result = run_stage(bundle, "download")
    assert result.returncode != 0
    assert "No matching distribution found for source-only" in result.stderr
    assert not (work / "requirements.lock").exists()
