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

"""Producer regressions; synthetic archives are not native platform qualification."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path, PureWindowsPath
from typing import Any

import httpx
import pytest
from build_intel_managed_wheel import _check_linkage, native_build_environment, verify_backend
from ci_managed_resource_smoke import MAX_DIAGNOSTIC_BYTES, preserve_failure
from prepare_managed_resources import (
    _locked_dependencies,
    acquire,
    extract_runtime,
    inventory,
    json_bytes,
    sha256,
    target_spec,
)
from produce_managed_resources import deterministic_archive, measure_windows_paths

from powercontext.ops import resources
from powercontext.ops.resources import ResourceCatalog, ResourceError, ResourceSpec, ResourceStore, verify
from powercontext.service.environment import protect_directory, write_protected_file

_ROOT = Path(__file__).resolve().parents[1]


def _runtime_archive(path: Path, *, alias: str = "python.exe", directory_alias: bool = False) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, content in {
            "python/python.exe": b"console interpreter fixture",
            "python/LICENSE.txt": b"fixture license",
        }.items():
            file = tarfile.TarInfo(name)
            file.size = len(content)
            file.mode = 0o755 if name.endswith(".exe") else 0o644
            archive.addfile(file, io.BytesIO(content))
        if directory_alias:
            directory = tarfile.TarInfo("python/lib")
            directory.type = tarfile.DIRTYPE
            archive.addfile(directory)
        file = tarfile.TarInfo("python/pythonw.exe")
        file.type = tarfile.SYMTYPE
        file.linkname = alias
        archive.addfile(file)


def _spec(archive: Path, files: list[dict[str, Any]]) -> ResourceSpec:
    return ResourceSpec.model_validate({
        "resource_id": "synthetic-candidate",
        "server_version": "1.2.3",
        "python_version": "3.12.16",
        "platform": "windows",
        "architecture": "x86_64",
        "archive_format": "tar.gz",
        "url": "https://candidate.invalid/server.tar.gz",
        "allowed_origins": ["https://candidate.invalid"],
        "archive_sha256": sha256(archive),
        "archive_size": archive.stat().st_size,
        "unpacked_size": sum(file["size"] for file in files),
        "python_relative": "pythonw.exe",
        "ops_protocol_min": 1,
        "ops_protocol_max": 1,
        "source_refs": ["fixture-source"],
        "license_refs": ["LICENSE.txt"],
        "qualification_ref": "unqualified-candidate:fixture",
        "files": files,
    })


def test_internal_file_alias_is_materialized_and_consumed_with_pinned_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tmp_path = tmp_path.resolve()
    source = tmp_path / "python.tar.gz"
    _runtime_archive(source)
    tree = tmp_path / "python"
    aliases = extract_runtime(source, tree)
    assert aliases == [{"path": "pythonw.exe", "source": "python.exe"}]
    assert not (tree / "pythonw.exe").is_symlink()
    assert (tree / "pythonw.exe").read_bytes() == (tree / "python.exe").read_bytes()
    archive = tmp_path / "server.tar.gz"
    files = inventory(tree)
    deterministic_archive(tree, archive, files)
    spec = _spec(archive, files)
    control = protect_directory(tmp_path / "control", create=True)
    content = json_bytes({"format_version": 1, "resources": [spec.model_dump(mode="json")]})
    write_protected_file(control / "catalog.json", content)
    catalog = ResourceCatalog.read(control / "catalog.json", hashlib.sha256(content).hexdigest())
    monkeypatch.setattr(resources, "_current_platform", lambda: ("windows", "x86_64"))
    client = httpx.Client
    monkeypatch.setattr(
        resources.httpx,
        "Client",
        lambda **kwargs: client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, stream=httpx.ByteStream(archive.read_bytes()))
            ),
            **kwargs,
        ),
    )
    prepared = ResourceStore(tmp_path / "cache").prepare(catalog.select_current(), tmp_path / "staging")
    verify(spec, prepared.directory)
    assert inventory(prepared.directory) == files
    (prepared.directory / "python.exe").write_bytes(b"corruption")
    with pytest.raises(ResourceError, match="resource_corrupt"):
        verify(spec, prepared.directory)
    with pytest.raises(ResourceError, match="resource_catalog_invalid"):
        ResourceCatalog.read(control / "catalog.json", "0" * 64)
    monkeypatch.setattr(resources, "_current_platform", lambda: ("macos", "arm64"))
    with pytest.raises(ResourceError, match="resource_unavailable"):
        catalog.select_current()


@pytest.mark.parametrize(
    "alias,directory", [("../outside", False), ("/absolute/python.exe", False), ("lib", True), ("pythonw.exe", False)]
)
def test_unsafe_or_directory_runtime_alias_rejected_before_extraction(
    tmp_path: Path, alias: str, directory: bool
) -> None:
    source = tmp_path / "python.tar.gz"
    _runtime_archive(source, alias=alias, directory_alias=directory)
    with pytest.raises(ValueError, match="alias"):
        extract_runtime(source, tmp_path / "output")
    assert not (tmp_path / "output").exists()


def test_archive_is_deterministic_across_input_roots_and_timestamps(tmp_path: Path) -> None:
    archives = []
    for index in range(2):
        tree = tmp_path / f"root-{index}"
        tree.mkdir()
        file = tree / "python.exe"
        file.write_bytes(b"same candidate bytes")
        file.chmod(0o755)
        os.utime(file, (index + 1, index + 1))
        archive = tmp_path / f"artifact-{index}.tar.gz"
        deterministic_archive(tree, archive, inventory(tree))
        archives.append(archive.read_bytes())
    assert archives[0] == archives[1]
    with pytest.raises(FileExistsError):
        deterministic_archive(tree, archive, inventory(tree))


def test_corrupt_cached_interpreter_is_rejected_without_network(tmp_path: Path) -> None:
    artifact = tmp_path / "python.tar.gz"
    artifact.write_bytes(b"bad existing input")
    with pytest.raises(ValueError, match="wrong digest"):
        acquire({"url": "https://candidate.invalid/python.tar.gz", "sha256": "0" * 64}, artifact)
    assert artifact.read_bytes() == b"bad existing input"


def test_windows_layout_requires_console_and_windowless_interpreters(tmp_path: Path) -> None:
    source = tmp_path / "python.tar.gz"
    _runtime_archive(source)
    tree = tmp_path / "python"
    extract_runtime(source, tree)
    files = inventory(tree)
    archive = tmp_path / "server.tar.gz"
    deterministic_archive(tree, archive, files)
    spec = _spec(archive, files)
    payload = spec.model_dump(mode="json")
    payload["files"] = [item for item in payload["files"] if item["path"] != "python.exe"]
    payload["unpacked_size"] = sum(item["size"] for item in payload["files"])
    with pytest.raises(ValueError, match="resource_invalid"):
        ResourceSpec.model_validate(payload)


def test_windows_path_budget_covers_real_default_staging_and_utf16() -> None:
    root = PureWindowsPath(
        "C:/Users/PowerContext User/AppData/Local/Programs/PowerContext/managed/staging/resource-" + "0" * 32
    )
    files = [{"path": "Lib/site-packages/anthropic/types/beta/" + "a" * 75 + ".py"}]
    measured = measure_windows_paths(root, files)
    assert measured["longest_file_utf16_units"] <= 259
    with pytest.raises(ValueError, match="budget"):
        measure_windows_paths(root, [{"path": "Lib/" + "📦" * 80 + ".py"}])
    with pytest.raises(ValueError, match="directory"):
        measure_windows_paths(PureWindowsPath("C:/" + "a" * 245), [{"path": "x"}])


def test_foreign_target_is_not_a_cross_compilation_request() -> None:
    for target in ("macos-arm64", "macos-x86_64", "windows-x86_64"):
        target_spec(target, native=False)
    matching = {"darwin": "macos", "win32": "windows"}.get(sys.platform)
    foreign = "windows-x86_64" if matching != "windows" else "macos-arm64"
    with pytest.raises(ValueError, match="matching native"):
        target_spec(foreign)


def test_intel_crypto_source_exception_is_version_and_target_specific() -> None:
    digest = "a" * 64
    lock = {
        "package": [
            {
                "name": "cryptography",
                "version": "50.0.0",
                "sdist": {
                    "url": "https://candidate.invalid/cryptography-50.0.0.tar.gz",
                    "hash": "sha256:" + digest,
                    "size": 10,
                },
            }
        ]
    }
    export = "cryptography==50.0.0 --hash=sha256:" + digest
    selected = _locked_dependencies(export, lock, {}, ["cp312-cp312-macosx_10_13_x86_64"], "macos-x86_64")
    assert selected[0]["kind"] == "intel-cryptography-sdist"
    assert selected[0]["sha256"] == digest
    with pytest.raises(ValueError, match="source builds are not implicitly allowed"):
        _locked_dependencies(export, lock, {}, ["cp312-cp312-win_amd64"], "windows-x86_64")
    with pytest.raises(ValueError, match="exported requirement hashes"):
        _locked_dependencies(
            export.replace(digest, "b" * 64), lock, {}, ["cp312-cp312-macosx_10_13_x86_64"], "macos-x86_64"
        )


def test_native_build_cannot_be_retargeted_by_ambient_library_or_backend_options(tmp_path: Path) -> None:
    ambient = {
        "PATH": str(tmp_path / "untrusted"),
        "X86_64_APPLE_DARWIN_OPENSSL_DIR": str(tmp_path / "untrusted-openssl"),
        "X86_64_APPLE_DARWIN_OPENSSL_LIB_DIR": str(tmp_path / "untrusted-lib"),
        "PYO3_CONFIG_FILE": str(tmp_path / "untrusted-python.cfg"),
        "MATURIN_PEP517_ARGS": "--features untrusted",
        "CC_x86_64_apple_darwin": str(tmp_path / "untrusted-compiler"),
        "CPATH": str(tmp_path / "untrusted-headers"),
        "PKG_CONFIG_PATH": str(tmp_path / "untrusted-config"),
        "CONDA_PREFIX": str(tmp_path / "untrusted-conda"),
        "VIRTUAL_ENV": str(tmp_path / "untrusted-venv"),
        "MAKEFLAGS": "-f untrusted.make",
        "HTTPS_PROXY": "https://proxy.invalid",
    }
    selected = native_build_environment(
        ambient,
        python=tmp_path / "backend/bin/python",
        rust=tmp_path / "rust",
        workspace=tmp_path,
        compiler="/fixed/clang",
        sdk_path="/fixed/SDK",
        deployment="10.13",
    )
    assert not any(name in selected for name in ambient if name not in {"PATH", "HTTPS_PROXY"})
    assert selected["CC"] == "/fixed/clang"
    assert selected["SDKROOT"] == "/fixed/SDK"
    assert selected["HTTPS_PROXY"] == "https://proxy.invalid"
    assert str(tmp_path / "untrusted") not in selected["PATH"]


@pytest.mark.skipif(os.name == "nt", reason="Intel backend uses POSIX executable lookup")
def test_bare_backend_command_resolves_the_hash_pinned_wheel_binary(tmp_path: Path) -> None:
    python = tmp_path / "backend/bin/python"
    python.parent.mkdir(parents=True)
    executable = python.parent / "maturin"
    content = f"#!{sys.executable}\nprint('fixed backend')\n".encode()
    executable.write_bytes(content)
    executable.chmod(0o755)
    archive = tmp_path / "maturin.whl"
    with zipfile.ZipFile(archive, "w") as wheel:
        wheel.writestr("maturin-1.14.1.data/scripts/maturin", content)
    selected = native_build_environment(
        {"PATH": str(tmp_path / "ambient")},
        python=python,
        rust=tmp_path / "rust",
        workspace=tmp_path,
        compiler="/fixed/clang",
        sdk_path="/fixed/SDK",
        deployment="10.13",
    )
    assert verify_backend(archive, python, selected) == hashlib.sha256(content).hexdigest()
    result = subprocess.run(
        ["maturin", "pep517"],  # noqa: S607 - exercise the backend's bare-command executable lookup
        env=selected,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "fixed backend"
    executable.write_bytes(b"changed backend")
    with pytest.raises(ValueError, match="pinned maturin executable"):
        verify_backend(archive, python, selected)


def test_smoke_failure_keeps_bounded_logs_after_temporary_cleanup(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    with (
        pytest.raises(RuntimeError, match="bounded diagnostics") as failed,
        tempfile.TemporaryDirectory() as temporary,
        preserve_failure(evidence, Path(temporary)),
    ):
        workspace = Path(temporary)
        (workspace / "control").mkdir()
        (workspace / "control/stderr.log").write_bytes(b"x" * (MAX_DIAGNOSTIC_BYTES + 20) + b"useful tail")
        message = "raw error content must not appear inline"
        raise ValueError(message)
    assert not workspace.exists()
    saved = next(evidence.iterdir())
    assert (saved / "stderr.log").stat().st_size == MAX_DIAGNOSTIC_BYTES
    assert (saved / "stderr.log").read_bytes().endswith(b"useful tail")
    assert json.loads((saved / "failure.json").read_bytes())["exception_class"] == "ValueError"
    assert "raw error content" not in str(failed.value)


@pytest.mark.skipif(sys.platform != "darwin", reason="requires real Mach-O compiler and load-command inspection")
@pytest.mark.parametrize("architecture", ["arm64", "x86_64"])
@pytest.mark.parametrize("external", [None, "dependency", "rpath"])
def test_native_linkage_distinguishes_install_name_from_loaded_dependencies(
    tmp_path: Path, architecture: str, external: str | None
) -> None:
    tree = tmp_path / "wheel"
    tree.mkdir()
    source = tmp_path / "extension.c"
    source.write_text("int resource_probe(void) { return 1; }\n", encoding="utf-8")
    sdk = (
        os.environ.get("SDKROOT")
        or subprocess.check_output(["/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-path"], text=True).strip()
    )
    compiler = ["/usr/bin/clang", "-dynamiclib", "-arch", architecture, "-isysroot", sdk]
    arguments = []
    if external == "dependency":
        dependency = tmp_path / "libexternal.dylib"
        subprocess.run(
            [
                *compiler,
                str(source),
                "-Wl,-install_name,/opt/producer-fixture/libexternal.dylib",
                "-o",
                str(dependency),
            ],
            check=True,
            capture_output=True,
        )
        source.write_text("extern int resource_probe(void); int extension(void) { return resource_probe(); }\n")
        arguments.append(str(dependency))
    elif external == "rpath":
        arguments.append("-Wl,-rpath,/opt/producer-fixture/lib")
    subprocess.run(
        [*compiler, str(source), *arguments, "-Wl,-install_name,@rpath/extension.so", "-o", str(tree / "extension.so")],
        check=True,
        capture_output=True,
    )
    if external is not None:
        with pytest.raises(ValueError, match="non-system libraries or runtime search paths"):
            _check_linkage(tree, dict(os.environ))
    else:
        (linked,) = _check_linkage(tree, dict(os.environ))
        assert linked["install_names"] == ["@rpath/extension.so"]
        assert linked["dependencies"] and all(path.startswith("/usr/lib/") for path in linked["dependencies"])
        assert not linked["rpaths"]


@pytest.fixture(scope="module")
def native_candidates(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, Path, str]:
    selected = os.environ.get("POWERCONTEXT_RESOURCE_TEST_INPUTS")
    if not selected:
        pytest.skip("requires explicitly prepared native pinned inputs; synthetic tests do not qualify a platform")
    inputs = Path(selected).resolve()
    expected = os.environ["POWERCONTEXT_RESOURCE_TEST_INPUTS_SHA256"]
    root = tmp_path_factory.mktemp("native-resource-candidates").resolve()
    output = os.environ.get("POWERCONTEXT_RESOURCE_TEST_OUTPUT")
    outputs = (Path(output).resolve() if output else root / "first", root / "second")
    relocated_inputs = root / "relocated-inputs"
    shutil.copytree(inputs, relocated_inputs, copy_function=shutil.copyfile)
    for output, prepared_inputs in zip(outputs, (inputs, relocated_inputs), strict=True):
        subprocess.run(
            [
                sys.executable,
                str(_ROOT / "scripts/produce_managed_resources.py"),
                "--inputs",
                str(prepared_inputs),
                "--inputs-sha256",
                expected,
                "--server-url",
                "https://candidate.invalid/server.tar.gz",
                "--output",
                str(output),
            ],
            cwd=_ROOT,
            check=True,
            timeout=600,
        )
    return outputs[0], outputs[1], inputs, expected


def test_native_preparation_uses_canonical_lock_bytes() -> None:
    selected = os.environ.get("POWERCONTEXT_RESOURCE_TEST_INPUTS")
    if not selected:
        pytest.skip("requires explicitly prepared native pinned inputs")
    inputs = Path(selected).resolve()
    metadata = json.loads((inputs / "inputs.json").read_bytes())
    content = (inputs / "requirements.lock").read_bytes()
    # The consumer compares this hash-bound wire format byte for byte. Windows
    # text-mode CRLF translation must not change otherwise identical inputs.
    assert content and content.endswith(b"\n") and b"\r" not in content
    assert hashlib.sha256(content).hexdigest() == metadata["requirements_sha256"]


def test_native_producer_is_deterministic_and_preserves_immutable_output(native_candidates) -> None:
    first, second, inputs, expected = native_candidates
    for path in (
        "server/server.tar.gz",
        "server/catalog.json",
        "ops.tar.gz",
        "ops/bundle.json",
        "ops-provision.json",
        "candidate.json",
    ):
        assert sha256(first / path) == sha256(second / path)
    before = sha256(first / "candidate.json")
    result = subprocess.run(
        [
            sys.executable,
            str(_ROOT / "scripts/produce_managed_resources.py"),
            "--inputs",
            str(inputs),
            "--inputs-sha256",
            expected,
            "--server-url",
            "https://candidate.invalid/server.tar.gz",
            "--output",
            str(first),
        ],
        cwd=_ROOT,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert b"never overwrites" in result.stderr
    assert sha256(first / "candidate.json") == before
    candidate = json.loads((first / "candidate.json").read_bytes())
    assert candidate["status"] == "unqualified_candidate"
    assert candidate["qualification_ref"].startswith("unqualified-candidate:sha256:")


def test_native_candidate_is_consumed_and_runs_independent_ops(native_candidates) -> None:
    first, _second, _inputs, _expected = native_candidates
    subprocess.run(
        [
            sys.executable,
            str(_ROOT / "scripts/ci_managed_resource_smoke.py"),
            "--candidate",
            str(first),
            "--serve",
            "--evidence-dir",
            os.environ.get("POWERCONTEXT_RESOURCE_TEST_EVIDENCE", str(first.with_name(first.name + "-evidence"))),
        ],
        cwd=_ROOT,
        check=True,
        timeout=300,
    )


def test_native_version_mismatch_is_rejected_before_output(native_candidates, tmp_path: Path) -> None:
    _first, _second, inputs, _expected = native_candidates
    copied = tmp_path / "inputs"
    shutil.copytree(inputs, copied, copy_function=shutil.copyfile)
    metadata = json.loads((copied / "inputs.json").read_bytes())
    metadata["server_version"] = "999.0.0"
    (copied / "inputs.json").write_bytes(json_bytes(metadata))
    output = tmp_path / "rejected"
    result = subprocess.run(
        [
            sys.executable,
            str(_ROOT / "scripts/produce_managed_resources.py"),
            "--inputs",
            str(copied),
            "--inputs-sha256",
            sha256(copied / "inputs.json"),
            "--server-url",
            "https://candidate.invalid/server.tar.gz",
            "--output",
            str(output),
        ],
        cwd=_ROOT,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
    assert b"requested Server version" in result.stderr
    assert not output.exists()
