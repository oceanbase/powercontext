#!/usr/bin/env python3
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

"""Select the CI cache backend and fingerprint local tool/dependency snapshots."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlparse


def write_command_file(filename: str, values: dict[str, str]) -> None:
    with Path(os.environ[filename]).open("a", encoding="utf-8") as output:
        for name, value in values.items():
            if "\n" in value or "\r" in value:
                delimiter = f"cache_{uuid.uuid4().hex}"
                output.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")
            else:
                output.write(f"{name}={value}\n")


def backend() -> None:
    internal = (
        os.environ.get("RUNNER_OS") == "Linux"
        and os.environ.get("RUNNER_ENVIRONMENT") == "self-hosted"
        and bool(os.environ.get("PC_CI_CACHE_ENDPOINT"))
    )
    if not internal:
        write_command_file("GITHUB_OUTPUT", {"backend": "github"})
        return
    names = ("ENDPOINT", "BUCKET", "ACCESS_KEY", "SECRET_KEY")
    values: dict[str, str] = {f"PC_CI_CACHE_{name}": os.environ.get(f"PC_CI_CACHE_{name}", "") for name in names}
    missing = [name for name, value in values.items() if not value]
    if missing:
        message = f"Internal cache configuration is incomplete: {', '.join(missing)}"
        raise ValueError(message)
    for name in ("PC_CI_CACHE_ACCESS_KEY", "PC_CI_CACHE_SECRET_KEY"):
        value = values[name].replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::add-mask::{value}")
    values["PC_CI_CACHE_REGION"] = os.environ.get("PC_CI_CACHE_REGION") or "us-east-1"
    host = urlparse(values["PC_CI_CACHE_ENDPOINT"]).hostname or ""
    bypass = [*os.environ.get("NO_PROXY", "").split(","), *os.environ.get("no_proxy", "").split(","), host]
    no_proxy = ",".join(dict.fromkeys(value.strip() for value in bypass if value.strip()))
    # Linux GITHUB_ENV preserves case, while action YAML env mappings reject
    # NO_PROXY/no_proxy together. Keep both consumers on the internal route.
    values.update(NO_PROXY=no_proxy, no_proxy=no_proxy)
    # Register inherited Pod values for later action expressions, without setting
    # default AWS credentials in the environment of unrelated test processes.
    write_command_file("GITHUB_ENV", values)
    write_command_file("GITHUB_OUTPUT", {"backend": "s3"})


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:24]


def file_fingerprint(paths: list[Path], root: Path) -> str:
    entries = []
    for path in sorted(set(paths)):
        if path.is_file():
            entries.append((str(path.relative_to(root)), hashlib.sha256(path.read_bytes()).hexdigest()))
    return digest(entries)


def platform_key() -> str:
    release = Path("/etc/os-release")
    image = release.read_text() if release.exists() else ""
    return f"{os.environ.get('RUNNER_OS', sys.platform)}-{os.environ.get('RUNNER_ARCH', '')}-{digest(image)}"


def cargo() -> None:
    root = Path.cwd()
    workspaces = []
    targets = []
    for line in os.environ.get("CI_CACHE_WORKSPACES", ". -> target").splitlines():
        if not line.strip():
            continue
        workspace, _, target = line.partition("->")
        workspace_path = (root / workspace.strip()).resolve()
        workspaces.append(workspace_path)
        targets.append((workspace_path / (target.strip() or "target")).resolve())
    sources = []
    for workspace in workspaces:
        for folder, directories, files in os.walk(workspace):
            directories[:] = [
                name
                for name in directories
                if name not in {".git", ".venv", "node_modules", "target"}
                and (Path(folder) / name).resolve() not in targets
            ]
            sources.extend(Path(folder) / name for name in files)
    dependencies = [path for path in sources if path.name in {"Cargo.toml", "Cargo.lock"}]
    configs = [root / ".cargo/config.toml", root / "rust-toolchain", root / "rust-toolchain.toml"]
    configs.extend(
        path
        for path in sources
        if path.name in {"rust-toolchain", "rust-toolchain.toml"} or path.parts[-2:] == (".cargo", "config.toml")
    )
    runtime = subprocess.run(["rustc", "-vV"], capture_output=True, text=True, check=True).stdout  # noqa: S607
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.startswith(("CARGO", "CC", "CFLAGS", "CXX", "CMAKE", "RUST"))
    }
    identity = digest([runtime, environment, file_fingerprint(configs, root)])
    dependencies_hash = file_fingerprint(dependencies, root)
    scope = os.environ.get("CI_CACHE_SHARED_KEY") or os.environ.get("GITHUB_JOB", "cargo")
    extra = os.environ.get("CI_CACHE_KEY", "")
    prefix = f"cargo-v1-{platform_key()}-{scope}-{extra}-{identity}-{dependencies_hash}-"
    cargo_home = Path(os.environ.get("CARGO_HOME", str(Path.home() / ".cargo")))
    paths = [cargo_home / "registry", cargo_home / "git", *targets]
    if os.environ.get("CI_CACHE_BIN") == "true":
        paths.extend([cargo_home / "bin", cargo_home / ".crates.toml", cargo_home / ".crates2.json"])
    write_command_file(
        "GITHUB_OUTPUT",
        {
            "cache-path": "\n".join(str(path) for path in paths),
            "cache-key": prefix + file_fingerprint(sources, root),
            "restore-key": prefix,
        },
    )


def mise() -> None:
    root = Path.cwd()
    patterns = ("**/mise*.toml", "**/.mise*.toml", "**/.tool-versions", "**/mise*.lock", "**/.mise*.lock")
    configs = [path for pattern in patterns for path in root.glob(pattern)]
    configs = [path for path in configs if not {"node_modules", ".venv", ".git"}.intersection(path.parts)]
    values = {key: value for key, value in os.environ.items() if key.startswith("CI_CACHE_MISE_")}
    values["MISE_ENV"] = os.environ.get("MISE_ENV", "")
    data_dir = os.environ.get("MISE_DATA_DIR") or str(
        Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "mise"
    )
    bootstrap = {
        "action": os.environ.get("CI_CACHE_MISE_ACTION", ""),
        "version": os.environ.get("CI_CACHE_MISE_VERSION") or "default",
    }
    write_command_file(
        "GITHUB_OUTPUT",
        {
            "cache-path": data_dir,
            "cache-key": f"mise-internal-v1-{platform_key()}-{digest(values)}-{file_fingerprint(configs, root)}",
            # The upstream action verifies its binary using the version record
            # and signed checksum files stored beside it in this directory.
            "bootstrap-cache-path": str(Path(data_dir) / "bin"),
            "bootstrap-cache-key": f"mise-bootstrap-v1-{platform_key()}-{digest(bootstrap)}",
        },
    )


def mise_bootstrap_ready() -> None:
    directory = Path(os.environ["CI_CACHE_BOOTSTRAP_PATH"])
    ready = False
    try:
        version = (directory / "mise-version").read_text(encoding="utf-8").strip()
        files = [
            directory / name for name in ("mise", f"SHASUMS256-{version}.txt", f"SHASUMS256-{version}.txt.minisig")
        ]
        ready = bool(version) and all(path.is_file() and path.stat().st_size > 0 for path in files)
    except (OSError, UnicodeError):
        pass
    write_command_file("GITHUB_OUTPUT", {"ready": str(ready).lower()})
    if not ready:
        print("Mise bootstrap cache is incomplete; skipping cache publication.")


def cargo_clean() -> None:
    for line in os.environ.get("CI_CACHE_WORKSPACES", ". -> target").splitlines():
        if not line.strip():
            continue
        workspace, _, target = line.partition("->")
        workspace_path = Path(workspace.strip()).resolve()
        manifest = str(workspace_path / "Cargo.toml")
        target_path = str(workspace_path / (target.strip() or "target"))
        metadata_command = [
            "cargo",
            "metadata",
            "--locked",
            "--offline",
            "--no-deps",
            "--format-version",
            "1",
            "--manifest-path",
            manifest,
        ]
        metadata = json.loads(subprocess.run(metadata_command, capture_output=True, text=True, check=True).stdout)  # noqa: S603
        members = set(metadata["workspace_members"])
        packages = [package["name"] for package in metadata["packages"] if package["id"] in members]
        if packages:
            command = [
                "cargo",
                "clean",
                "--locked",
                "--offline",
                "--manifest-path",
                manifest,
                "--target-dir",
                target_path,
            ]
            for package in packages:
                command.extend(["--package", package])
            subprocess.run(command, check=True)  # noqa: S603 - Cargo workspace package names are passed as argv


def go() -> None:
    root = Path.cwd()
    version = subprocess.run(["go", "version"], capture_output=True, text=True, check=True).stdout.strip()  # noqa: S607
    paths = subprocess.run(
        ["go", "env", "GOMODCACHE", "GOCACHE"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    patterns = os.environ.get("CI_CACHE_DEPENDENCY_PATH", "").splitlines() or ["go.sum"]
    files = [path for pattern in patterns for path in root.glob(pattern.strip()) if pattern.strip()]
    prefix = f"go-internal-v1-{platform_key()}-{digest(version)}-"
    write_command_file(
        "GITHUB_OUTPUT",
        {"cache-path": paths, "cache-key": prefix + file_fingerprint(files, root), "restore-key": prefix},
    )


def download_dependencies(root: Path) -> list[Path]:
    patterns = os.environ.get("CI_CACHE_DEPENDENCY_PATH", "").splitlines()
    return [path for pattern in patterns if pattern.strip() for path in root.glob(pattern.strip()) if path.is_file()]


def npm() -> None:
    root = Path.cwd()
    dependencies = download_dependencies(root)
    packages = sorted(os.environ.get("CI_CACHE_PACKAGES", "").split())
    if not dependencies and not packages:
        message = "npm cache requires dependency files or pinned packages"
        raise ValueError(message)
    runtime = subprocess.run(
        ["node", "-p", "JSON.stringify([process.version, process.platform, process.arch])"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    version = subprocess.run(["npm", "--version"], capture_output=True, text=True, check=True).stdout.strip()  # noqa: S607
    cache_dir = subprocess.run(
        ["npm", "config", "get", "cache"],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    prefix = f"npm-v1-{platform_key()}-{digest([runtime, version])}-"
    write_command_file(
        "GITHUB_OUTPUT",
        {
            "cache-path": str(Path(cache_dir) / "_cacache"),
            "cache-key": prefix + digest([packages, file_fingerprint(dependencies, root)]),
            "restore-key": prefix,
        },
    )


def pip() -> None:
    root = Path.cwd()
    dependencies = download_dependencies(root)
    if not dependencies:
        message = "pip cache requires exported dependency files"
        raise ValueError(message)
    command = [sys.executable, "-m", "pip", "cache", "dir"]
    cache_dir = Path(
        subprocess.run(  # noqa: S603 - fixed pip cache query through the current Python interpreter
            command, capture_output=True, text=True, check=True
        ).stdout.strip()
    )
    runtime = [
        sys.version_info[:3],
        sys.implementation.cache_tag,
        platform.machine(),
        importlib.metadata.version("pip"),
    ]
    prefix = f"pip-http-v1-{platform_key()}-{digest(runtime)}-"
    # HTTP responses include downloaded wheels, but never locally built wheels
    # or checkout-generated distributions. pip still verifies the locked hashes.
    write_command_file(
        "GITHUB_OUTPUT",
        {
            "cache-path": "\n".join(str(cache_dir / name) for name in ("http", "http-v2")),
            "cache-key": prefix + file_fingerprint(dependencies, root),
            "restore-key": prefix,
        },
    )


if __name__ == "__main__":
    try:
        {
            "backend": backend,
            "cargo": cargo,
            "cargo-clean": cargo_clean,
            "mise": mise,
            "mise-bootstrap-ready": mise_bootstrap_ready,
            "go": go,
            "npm": npm,
            "pip": pip,
        }[sys.argv[1]]()
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
