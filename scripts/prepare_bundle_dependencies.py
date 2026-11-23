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

"""Download locked bundle inputs, then build missing dependency wheels offline."""

# ruff: noqa: TRY003, S603 - fixed packaging tools and explicit bundle paths

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import zipfile
from email.parser import BytesParser
from pathlib import Path
from typing import Any

BUILD_TOOLS = Path(__file__).with_name("managed_resource_build_tools.json")
DEPENDENCIES = (
    ("upstream-requirements.lock", "downloads", "wheelhouse", "requirements.lock"),
    ("upstream-plugin-requirements.lock", "plugin-downloads", "plugin-wheelhouse", "plugin-requirements.lock"),
)


def sha256(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def tools_plan(work: Path) -> None:
    work.mkdir(parents=True, exist_ok=True)
    pins = json.loads(BUILD_TOOLS.read_bytes())
    requirements = work / "build-tools.lock"
    requirements.write_text(
        "".join(f"{p['name']}=={p['version']} --hash=sha256:{p['sha256']}\n" for p in pins["packages"]),
        encoding="utf-8",
    )
    shutil.copyfile(BUILD_TOOLS, work / "build-tools.json")
    tools = work / "build-tool-wheels"
    builder = work / "builder"
    result = {
        "cache-key": "bundle-build-tools-v1-" + sha256(BUILD_TOOLS),
        "cache-path": str(tools),
        "requirements": str(requirements),
        "builder-directory": str(builder),
        "python-path": str(builder / ("Scripts/python.exe" if os.name == "nt" else "bin/python")),
    }
    if command_file := os.environ.get("GITHUB_OUTPUT"):
        with Path(command_file).open("a", encoding="utf-8") as output:
            for key, value in result.items():
                output.write(f"{key}={value}\n")
    print(json.dumps(result, sort_keys=True))


def wheel_identity(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        metadata_files = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(metadata_files) != 1:
            raise ValueError(f"Expected one wheel metadata file: {path.name}")
        content = archive.read(metadata_files[0])
    return artifact_identity(path, content)


def source_identity(path: Path) -> dict[str, Any]:
    if path.name.endswith(".tar.gz"):
        with tarfile.open(path) as archive:
            members = [
                item for item in archive.getmembers() if item.name.count("/") == 1 and item.name.endswith("/PKG-INFO")
            ]
            if len(members) != 1 or not members[0].isfile():
                raise ValueError(f"Expected one source metadata file: {path.name}")
            stream = archive.extractfile(members[0])
            if stream is None:
                raise ValueError(f"Source metadata cannot be read: {path.name}")
            content = stream.read()
    elif path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            names = [name for name in archive.namelist() if name.count("/") == 1 and name.endswith("/PKG-INFO")]
            if len(names) != 1:
                raise ValueError(f"Expected one source metadata file: {path.name}")
            content = archive.read(names[0])
    else:
        raise ValueError(f"Unsupported source archive format: {path.name}")
    return artifact_identity(path, content)


def artifact_identity(path: Path, content: bytes) -> dict[str, Any]:
    metadata = BytesParser().parsebytes(content)
    name, version = metadata.get("Name"), metadata.get("Version")
    if not name or not version:
        raise ValueError(f"Artifact has no package identity: {path.name}")
    return {
        "name": re.sub(r"[-_.]+", "-", name).lower(),
        "version": version,
        "filename": path.name,
        "sha256": sha256(path),
    }


def download(work: Path) -> None:
    records = []
    for upstream, directory, _wheelhouse, _requirements in DEPENDENCIES:
        exported = work / upstream
        contents = exported.read_text(encoding="utf-8")
        destination = work / directory
        if destination.exists() and any(destination.iterdir()):
            raise ValueError(f"Dependency download destination must be empty: {destination}")
        destination.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--require-hashes",
            "--prefer-binary",
            "--no-build-isolation",
        ]
        command += ["--dest", str(destination), "--requirement", str(exported)]
        if installer := os.environ.get("PC_CI_PYTHON_INSTALLER"):
            command = [sys.executable, installer, "pip", "--python", sys.executable, *command[3:]]
        subprocess.run(command, check=True)
        accepted_hashes = set(re.findall(r"--hash=sha256:([0-9a-f]{64})", contents))
        artifacts = []
        for path in sorted(destination.iterdir()):
            digest = sha256(path)
            if digest not in accepted_hashes:
                raise ValueError(f"Downloaded artifact hash is absent from upstream requirements: {path.name}")
            if path.suffix == ".whl":
                item = wheel_identity(path)
                item["kind"] = "wheel"
            else:
                item = source_identity(path)
                item["kind"] = "sdist"
            artifacts.append(item)
        records.append({"requirements": upstream, "requirements_sha256": sha256(exported), "sources": artifacts})
    write_json(work / "dependency-downloads.json", {"dependencies": records})


def build(work: Path) -> None:  # noqa: C901 - ordered input verification, source build and closure publication
    downloaded = json.loads((work / "dependency-downloads.json").read_bytes())
    completed: list[dict[str, Any]] = []
    for (upstream, directory, wheelhouse, requirements), inputs in zip(
        DEPENDENCIES, downloaded["dependencies"], strict=True
    ):
        if inputs["requirements"] != upstream or inputs["requirements_sha256"] != sha256(work / upstream):
            raise ValueError("The upstream requirements changed after dependency download")
        destination = work / wheelhouse
        if destination.exists() and any(destination.iterdir()):
            raise ValueError(f"Built wheel destination must be empty: {destination}")
        destination.mkdir(parents=True, exist_ok=True)
        expected = []
        for item in inputs["sources"]:
            source = work / directory / item["filename"]
            if sha256(source) != item["sha256"]:
                raise ValueError(f"Dependency input hash changed: {source.name}")
            expected.append((item["name"], item["version"]))
            if item["kind"] == "wheel":
                shutil.copyfile(source, destination / source.name)
                continue
            if item["kind"] != "sdist":
                raise ValueError(f"Unsupported dependency input kind: {item['kind']}")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "--no-cache-dir",
                    "wheel",
                    "--no-deps",
                    "--no-build-isolation",
                    "--no-index",
                    "--wheel-dir",
                    str(destination),
                    str(source),
                ],
                check=True,
            )
        wheels = [wheel_identity(path) for path in sorted(destination.glob("*.whl"))]
        if sorted((item["name"], item["version"]) for item in wheels) != sorted(expected):
            raise ValueError("Built wheel closure differs from the verified dependency inputs")
        completed.append({**inputs, "install_requirements": requirements, "wheels": wheels})
    # Publish install locks only after both closures are complete.
    for item in completed:
        (work / item["install_requirements"]).write_text(
            "".join(
                f"{wheel['name']}=={wheel['version']} --hash=sha256:{wheel['sha256']}\n" for wheel in item["wheels"]
            ),
            encoding="utf-8",
        )
    provenance: dict[str, Any] = {"dependencies": completed}
    if (work / "build-tools.json").exists():
        provenance["build_tools_sha256"] = sha256(work / "build-tools.json")
        provenance["build_tools_lock_sha256"] = sha256(work / "build-tools.lock")
    write_json(work / "dependency-provenance.json", provenance)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("tools-plan", "download", "build"))
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    {"tools-plan": tools_plan, "download": download, "build": build}[args.stage](args.work.resolve())


if __name__ == "__main__":
    main()
