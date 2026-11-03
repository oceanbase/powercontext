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

"""Build locked cryptography for Intel macOS with local, static OpenSSL.

Invoked only by input preparation. Upstream removed this platform, so producing
and smoke-testing this wheel cannot claim upstream support or qualification.
"""

# ruff: noqa: TRY003, S603 - fixed native producer toolchain and pinned source builds

from __future__ import annotations

import hashlib
import json
import platform
import re
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path
from typing import Any

PINS = Path(__file__).with_name("managed_resource_intel_build.json")


def _hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _unpack(path: Path, destination: Path) -> Path:
    destination.mkdir()
    with tarfile.open(path) as archive:
        archive.extractall(destination, filter="data")
    roots = list(destination.iterdir())
    if len(roots) != 1 or not roots[0].is_dir():
        raise ValueError("Native build source must have exactly one root directory")
    return roots[0]


def _retain_native_licenses(tree: Path, vendor: Path, openssl: Path, crates: list[dict[str, Any]]) -> None:
    licenses = next(tree.glob("*.dist-info")) / "licenses"
    licenses.mkdir(exist_ok=True)
    (licenses / "OPENSSL-LICENSE.txt").write_bytes((openssl / "LICENSE.txt").read_bytes())
    records = []
    for crate in crates:
        root = vendor / f"{crate['name']}-{crate['version']}"
        package = tomllib.loads((root / "Cargo.toml").read_text(encoding="utf-8"))["package"]
        notices = []
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if path.is_file() and any(
                part.lower().startswith(("license", "copying", "notice")) for part in relative.parts
            ):
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    raise ValueError("Vendored license notices must be regular in-tree files")
                destination = licenses / "rust" / root.name / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, destination)
                notices.append({"path": destination.relative_to(tree).as_posix(), "sha256": _hash(destination)})
        records.append({
            "name": crate["name"],
            "version": crate["version"],
            "source_sha256": crate["checksum"],
            "license": package.get("license"),
            "notices": notices,
        })
    (licenses / "native-build-licenses.json").write_text(
        json.dumps({"format_version": 1, "crates": records}, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def _check_linkage(tree: Path, env: dict[str, str]) -> list[dict[str, Any]]:
    linkage = []
    for library in tree.rglob("*.so"):
        commands = subprocess.check_output(["/usr/bin/otool", "-l", str(library)], env=env, text=True)
        dependencies, rpaths, install_names = [], [], []
        for block in re.split(r"\nLoad command \d+\n", commands):
            command = re.search(r"^\s*cmd (LC_\w+)$", block, re.MULTILINE)
            if command is None:
                continue
            kind = command[1]
            if kind == "LC_RPATH":
                field, values = "path", rpaths
            elif kind.endswith("_DYLIB"):
                # otool -L also lists LC_ID_DYLIB: the extension's own identity
                # is not a dependency loaded from an external runtime directory.
                field, values = "name", install_names if kind == "LC_ID_DYLIB" else dependencies
            else:
                continue
            value = re.search(rf"^\s*{field} (.*?) \(offset \d+\)$", block, re.MULTILINE)
            if value is None:
                raise ValueError("Native extension has an unreadable dynamic-library load command")
            values.append(value[1])
        if any(not item.startswith(("/usr/lib/", "/System/Library/")) for item in [*dependencies, *rpaths]):
            raise ValueError("Native cryptography wheel depends on non-system libraries or runtime search paths")
        linkage.append({
            "path": library.relative_to(tree).as_posix(),
            "install_names": install_names,
            "dependencies": dependencies,
            "rpaths": rpaths,
        })
    if not linkage:
        raise ValueError("Native cryptography wheel contains no checked native extension")
    return linkage


def native_build_environment(
    base: dict[str, str], *, python: Path, rust: Path, workspace: Path, compiler: str, sdk_path: str, deployment: str
) -> dict[str, str]:
    """Select only the fixed backend/interpreter/compiler and native library inputs."""
    # Start from operational necessities, not a growing list of compiler knobs:
    # prefixed OpenSSL/cc variables and Conda/virtualenv controls must not win.
    allowed = {
        "HOME",
        "USER",
        "LOGNAME",
        "TMPDIR",
        "TMP",
        "TEMP",
        "LANG",
        "LC_ALL",
        "DEVELOPER_DIR",
        "SOURCE_DATE_EPOCH",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    }
    clean = {key: value for key, value in base.items() if key.upper() in allowed or key.upper().startswith("LC_")}
    return {
        **clean,
        "PATH": str(python.parent) + ":" + str(rust / "bin") + ":/usr/bin:/bin:/usr/sbin:/sbin",
        "SDKROOT": sdk_path,
        "CC": compiler,
        "MACOSX_DEPLOYMENT_TARGET": deployment,
        "CARGO_HOME": str(workspace / "cargo-home"),
        "RUSTUP_HOME": str(workspace / "unused-rustup"),
        "RUSTC": str(rust / "bin/rustc"),
    }


def verify_backend(archive: Path, python: Path, environment: dict[str, str]) -> str:
    """A bare PEP 517 maturin command must resolve to the exact pinned wheel binary."""
    executable = python.parent / "maturin"
    selected = shutil.which("maturin", path=environment["PATH"])
    with zipfile.ZipFile(archive) as wheel:
        names = [name for name in wheel.namelist() if name.endswith(".data/scripts/maturin")]
        if len(names) != 1:
            raise ValueError("Pinned maturin wheel must contain one backend executable")
        expected = hashlib.sha256(wheel.read(names[0])).hexdigest()
    if selected is None or Path(selected) != executable or _hash(executable) != expected:
        raise ValueError("The backend command does not resolve to the pinned maturin executable")
    return expected


def build(  # noqa: C901 - ordered native build and verification boundary
    *,
    source_archive: Path,
    source_identity: dict[str, Any],
    artifacts: Path,
    dependencies: list[dict[str, Any]],
    python: Path,
    uv: str,
    wheels: Path,
    environment: dict[str, str],
) -> dict[str, Any]:
    pins = json.loads(PINS.read_bytes())
    if platform.system() != "Darwin" or platform.machine().lower() != "x86_64":
        raise ValueError("The Intel cryptography source builder requires native Intel macOS")
    if (source_identity["name"], source_identity["version"]) != (pins["package"], pins["version"]):
        raise ValueError("The Intel source-build exception applies only to the explicitly pinned package/version")
    if _hash(source_archive) != source_identity["sha256"]:
        raise ValueError("Native package source changed after acquisition")
    for item in pins["artifacts"].values():
        path = artifacts / item["filename"]
        if _hash(path) != item["sha256"]:
            raise ValueError("Native build-tool/source digest mismatch")
    xcode = subprocess.check_output(["/usr/bin/xcodebuild", "-version"], env=environment, text=True).strip()
    sdk = subprocess.check_output(
        ["/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-version"], env=environment, text=True
    ).strip()
    if xcode != f"Xcode {pins['xcode_version']}\nBuild version {pins['xcode_build']}" or sdk != pins["sdk_version"]:
        raise ValueError("Intel source production requires the pinned Xcode build and macOS SDK")
    sdk_path = subprocess.check_output(
        ["/usr/bin/xcrun", "--sdk", "macosx", "--show-sdk-path"], env=environment, text=True
    ).strip()
    compiler = subprocess.check_output(["/usr/bin/xcrun", "--find", "clang"], env=environment, text=True).strip()
    with tempfile.TemporaryDirectory(prefix="pc-intel-wheel-") as temporary:
        work = Path(temporary).resolve()
        source = _unpack(source_archive, work / "package")
        openssl = _unpack(artifacts / pins["artifacts"]["openssl"]["filename"], work / "openssl-source")
        rust_source = _unpack(artifacts / pins["artifacts"]["rust"]["filename"], work / "rust-source")
        rust = work / "rust"
        env = native_build_environment(
            environment,
            python=python,
            rust=rust,
            workspace=work,
            compiler=compiler,
            sdk_path=sdk_path,
            deployment=pins["deployment_target"],
        )
        subprocess.run(
            [
                "/bin/sh",
                str(rust_source / "install.sh"),
                "--prefix=" + str(rust),
                "--disable-ldconfig",
                "--components=rustc,cargo,rust-std-x86_64-apple-darwin",
            ],
            check=True,
            env=env,
            cwd=work,
            timeout=300,
        )
        rust_version = subprocess.check_output([str(rust / "bin/rustc"), "--version"], env=env, text=True).strip()
        if not rust_version.startswith("rustc 1.95.0 "):
            raise ValueError("Pinned Rust distribution reports an unexpected version")
        prefix = work / "openssl"
        subprocess.run(
            [
                "/usr/bin/perl",
                "Configure",
                "darwin64-x86_64-cc",
                "no-shared",
                "no-module",
                "no-tests",
                "-fPIC",
                "--prefix=" + str(prefix),
                "--libdir=lib",
            ],
            cwd=openssl,
            env=env,
            check=True,
            timeout=120,
        )
        subprocess.run(["/usr/bin/make", "-j2"], cwd=openssl, env=env, check=True, timeout=1800)
        subprocess.run(["/usr/bin/make", "install_sw"], cwd=openssl, env=env, check=True, timeout=300)
        openssl_version = subprocess.check_output([str(prefix / "bin/openssl"), "version"], env=env, text=True).strip()
        if not openssl_version.startswith("OpenSSL 4.0.3 "):
            raise ValueError("Pinned OpenSSL source reports an unexpected version")
        maturin = pins["artifacts"]["maturin"]
        build_requirements = work / "backend-requirements.lock"
        closure = [item for item in dependencies if item["name"] in {"cffi", "pycparser"}]
        if {item["name"] for item in closure} != {"cffi", "pycparser"}:
            raise ValueError("Intel native backend requires the complete locked cffi/pycparser closure")
        build_requirements.write_text(
            "maturin==1.14.1 --hash=sha256:"
            + maturin["sha256"]
            + "\n"
            + "".join(f"{p['name']}=={p['version']} --hash=sha256:{p['sha256']}\n" for p in closure),
            encoding="utf-8",
        )
        subprocess.run(
            [
                uv,
                "--offline",
                "pip",
                "install",
                "--python",
                str(python),
                "--no-index",
                "--find-links",
                str(artifacts),
                "--only-binary",
                ":all:",
                "--require-hashes",
                "-r",
                str(build_requirements),
            ],
            check=True,
            env=env,
            timeout=120,
        )
        backend_digest = verify_backend(artifacts / maturin["filename"], python, env)
        lock_path = source / "Cargo.lock"
        lock = tomllib.loads(lock_path.read_text(encoding="utf-8"))
        crates = [item for item in lock["package"] if "source" in item]
        if any(
            item["source"] != "registry+https://github.com/rust-lang/crates.io-index"
            or re.fullmatch(r"[0-9a-f]{64}", item.get("checksum", "")) is None
            for item in crates
        ):
            raise ValueError("All external Cargo dependencies must have locked crates.io checksums")
        lock_digest = _hash(lock_path)
        cargo = rust / "bin/cargo"
        vendored = subprocess.check_output(
            [
                str(cargo),
                "vendor",
                "--locked",
                "--versioned-dirs",
                "--manifest-path",
                str(source / "Cargo.toml"),
                str(work / "vendor"),
            ],
            cwd=source,
            env=env,
            text=True,
            timeout=1800,
        )
        (source / ".cargo").mkdir(exist_ok=True)
        (source / ".cargo/config.toml").write_text(vendored, encoding="utf-8")
        env.update(
            CARGO_NET_OFFLINE="true",
            CARGO_TARGET_DIR=str(work / "target"),
            OPENSSL_DIR=str(prefix),
            OPENSSL_STATIC="1",
            OPENSSL_NO_VENDOR="1",
            PYO3_PYTHON=str(python),
        )
        subprocess.run(
            [
                str(python),
                "-I",
                "-B",
                "-m",
                "pip",
                "--no-cache-dir",
                "wheel",
                "--no-deps",
                "--no-build-isolation",
                "--no-index",
                "--wheel-dir",
                str(wheels),
                str(source),
            ],
            cwd=source,
            env=env,
            check=True,
            timeout=1800,
        )
        if _hash(lock_path) != lock_digest:
            raise ValueError("The source build changed Cargo.lock")
        produced = list(wheels.glob("cryptography-50.0.0-*.whl"))
        if len(produced) != 1:
            raise ValueError("Native source build must produce one exact cryptography wheel")
        unpacked = work / "wheel"
        subprocess.run(
            [str(python), "-I", "-B", "-m", "wheel", "unpack", str(produced[0]), "-d", str(unpacked)],
            check=True,
            env=env,
            timeout=60,
        )
        tree = next(unpacked.iterdir())
        _retain_native_licenses(tree, work / "vendor", openssl, crates)
        linkage = _check_linkage(tree, env)
        produced[0].unlink()
        subprocess.run(
            [str(python), "-I", "-B", "-m", "wheel", "pack", str(tree), "-d", str(wheels)],
            check=True,
            env=env,
            timeout=60,
        )
        return {
            "status": "unqualified_native_source_build",
            "source_sha256": source_identity["sha256"],
            "pins_sha256": _hash(PINS),
            "pins": pins,
            "cargo_lock_sha256": lock_digest,
            "crates": [
                {"name": item["name"], "version": item["version"], "sha256": item["checksum"]} for item in crates
            ],
            "rust_version": rust_version,
            "maturin_executable_sha256": backend_digest,
            "openssl_version": openssl_version,
            "xcode": xcode,
            "sdk_version": sdk,
            "compiler": subprocess.check_output([compiler, "--version"], env=env, text=True).splitlines()[0],
            "linkage": linkage,
        }
