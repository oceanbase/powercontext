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

"""Package and verify standalone Gateway distributions without a Rust toolchain."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "x86_64-unknown-linux-gnu": "Linux glibc 2.35+",
    "aarch64-unknown-linux-gnu": "Linux glibc 2.35+",
    "x86_64-apple-darwin": "macOS 15+ (unsigned, not notarized)",
    "aarch64-apple-darwin": "macOS 15+ (unsigned, not notarized)",
}
MEMBERS = {"pc-gateway", "manifest.json", "LICENSE", "README.md"}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def identity(version: str, commit: str, target: str) -> None:
    require(bool(re.fullmatch(r"\d+\.\d+\.\d+(?:(?:a|b|rc)\d+|[-+]dev[.][0-9a-f]+)?", version)), "Invalid version")
    require(bool(re.fullmatch(r"[0-9a-f]{40}", commit)), "Expected a full source commit")
    require(target in TARGETS, "Unsupported Gateway target")


def archive_name(version: str, target: str) -> str:
    return f"powercontext-gateway-{version}-{target}.tar.gz"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def version_line(version: str, commit: str, target: str) -> str:
    return f"pc-gateway {version} ({commit}; {target})"


def inspect_archive(archive: Path, version: str, commit: str, target: str) -> dict[str, Any]:
    identity(version, commit, target)
    require(archive.name == archive_name(version, target), "Unexpected archive filename")
    checksum = archive.with_name(archive.name + ".sha256").read_text().strip()
    require(checksum == f"{sha256(archive)}  {archive.name}", "Archive checksum mismatch")
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        require(len(members) == len(MEMBERS) and {m.name for m in members} == MEMBERS, "Invalid archive members")
        require(all(m.isfile() for m in members), "Archive must contain only regular files")
        metadata_file = source.extractfile("manifest.json")
        binary_file = source.extractfile("pc-gateway")
        if metadata_file is None or binary_file is None:
            message = "Incomplete archive"
            raise ValueError(message)
        metadata = json.load(metadata_file)
        require(
            metadata
            == {
                "format": 1,
                "version": version,
                "source_commit": commit,
                "target": target,
                "minimum_platform": TARGETS[target],
                "binary_sha256": hashlib.sha256(binary_file.read()).hexdigest(),
            },
            "Binary or release identity mismatch",
        )
        require(source.getmember("pc-gateway").mode & 0o111 != 0, "Binary is not executable")
    return metadata


def unpack(archive: Path, destination: Path, version: str, commit: str, target: str) -> Path:
    inspect_archive(archive, version, commit, target)
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(archive, "r:gz") as source:
        for member in source.getmembers():
            stream = source.extractfile(member)
            if stream is None:
                message = "Missing archive file"
                raise ValueError(message)
            path = destination / member.name
            with path.open("xb") as output:
                shutil.copyfileobj(stream, output)
            path.chmod(0o755 if member.name == "pc-gateway" else 0o644)
    binary = (destination / "pc-gateway").resolve()
    result = subprocess.check_output([str(binary), "--version"], text=True, timeout=15)  # noqa: S603
    require(result.strip() == version_line(version, commit, target), "Executable version mismatch")
    return binary


def package(binary: Path, output: Path, version: str, commit: str, target: str) -> Path:
    identity(version, commit, target)
    result = subprocess.check_output([str(binary.resolve()), "--version"], text=True, timeout=15)  # noqa: S603
    require(result.strip() == version_line(version, commit, target), "Build metadata mismatch")
    output.mkdir(parents=True, exist_ok=True)
    archive = output / archive_name(version, target)
    with tempfile.TemporaryDirectory(prefix="gateway-package-") as temporary:
        root = Path(temporary)
        shutil.copyfile(binary, root / "pc-gateway")
        (root / "pc-gateway").chmod(0o755)
        shutil.copyfile(ROOT / "LICENSE", root / "LICENSE")
        (root / "README.md").write_text(
            f"# PowerContext Gateway {version}\n\n"
            f"Source: {commit}\nTarget: {target}\nRequires: {TARGETS[target]}\n\n"
            "Run `./pc-gateway --version` to inspect this build. Core is installed separately.\n"
            "Configure PC_GATEWAY_CORE_BASE or PC_GATEWAY_CORE_UDS and the Core token before running.\n\n"
            "Installation, verification and configuration:\n"
            "https://powercontext.oceanbase.io/en/docs/operate/install-gateway/\n"
            "https://powercontext.oceanbase.io/zh/docs/operate/install-gateway/\n"
        )
        (root / "manifest.json").write_text(
            json.dumps(
                {
                    "format": 1,
                    "version": version,
                    "source_commit": commit,
                    "target": target,
                    "minimum_platform": TARGETS[target],
                    "binary_sha256": sha256(binary),
                },
                indent=2,
            )
            + "\n"
        )
        with tarfile.open(archive, "w:gz") as packaged:
            for name in sorted(MEMBERS):
                packaged.add(root / name, arcname=name)
    archive.with_name(archive.name + ".sha256").write_text(f"{sha256(archive)}  {archive.name}\n")
    inspect_archive(archive, version, commit, target)
    return archive


def catalog(directory: Path, version: str, commit: str) -> None:
    expected = {archive_name(version, target) for target in TARGETS}
    require({p.name for p in directory.glob("*.tar.gz")} == expected, "All four Gateway platforms are required")
    artifacts = []
    for target in TARGETS:
        archive = directory / archive_name(version, target)
        metadata = inspect_archive(archive, version, commit, target)
        artifacts.append({**metadata, "archive": archive.name, "archive_sha256": sha256(archive)})
    (directory / "powercontext-gateway-manifest.json").write_text(json.dumps(artifacts, indent=2) + "\n")
    (directory / "powercontext-gateway-SHA256SUMS").write_text(
        "".join(f"{item['archive_sha256']}  {item['archive']}\n" for item in artifacts)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["package", "verify", "catalog"])
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--target", choices=TARGETS)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--extract-to", type=Path)
    args = parser.parse_args()
    if args.command == "catalog":
        catalog(args.directory, args.version, args.commit)
    elif args.command == "package":
        require(args.binary is not None and args.target is not None, "Binary and target are required")
        package(args.binary, args.directory, args.version, args.commit, args.target)
    else:
        require(args.target is not None and args.extract_to is not None, "Target and extraction directory are required")
        binary = unpack(
            args.directory / archive_name(args.version, args.target),
            args.extract_to,
            args.version,
            args.commit,
            args.target,
        )
        print(binary)


if __name__ == "__main__":
    main()
