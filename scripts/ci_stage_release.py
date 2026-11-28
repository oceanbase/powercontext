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

"""Stage accepted release bytes and supplement the portable candidate on other platforms."""

import argparse
import hashlib
import json
import shutil
import zipfile
from email.parser import BytesParser
from pathlib import Path


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def wheel_version(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        (metadata,) = (name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        parsed = BytesParser().parsebytes(archive.read(metadata))
    require(parsed["Name"] == "powercontext", "Unexpected release package")
    return str(parsed["Version"])


def stage(candidate: Path, evidence: Path, native_wheels: Path, output: Path) -> dict[str, str]:
    record = json.loads(evidence.read_text(encoding="utf-8"))
    require(
        record.get("complete") is True or record.get("fresh_install_smoke") == "passed",
        "Publication requires complete upgrade acceptance or the explicit fresh-install bypass",
    )
    files = sorted(candidate.iterdir())
    require(all(path.is_file() and not path.is_symlink() for path in files), "Invalid candidate distributions")
    require({path.name: sha256(path) for path in files} == record["artifacts"], "Accepted distribution hashes changed")
    wheels = [path for path in files if path.suffix == ".whl"]
    sources = [path for path in files if path.name.endswith(".tar.gz")]
    require(len(wheels) == len(sources) == 1 and len(files) == 2, "Expected one accepted wheel and sdist")
    (accepted,) = wheels
    version = wheel_version(accepted)
    require(sources[0].name == f"powercontext-{version}.tar.gz", "Accepted distribution versions differ")
    if accepted.name.endswith("-py3-none-any.whl"):
        require(not native_wheels.exists(), "Universal releases must use only their accepted distributions")
    else:
        require(
            "-cp311-abi3-manylinux" in accepted.name and accepted.name.endswith("_x86_64.whl"),
            "Native upgrade acceptance must use the publishable manylinux x86_64 wheel",
        )
        platforms = {"manylinux-x86_64", "manylinux-aarch64", "macos-x86_64", "macos-arm64", "windows-x86_64"}
        require(
            {path.name for path in native_wheels.iterdir()} == {f"native-wheels-{item}" for item in platforms},
            "Missing or unexpected native wheel artifacts",
        )
        for platform in sorted(platforms):
            directory = native_wheels / f"native-wheels-{platform}"
            require(directory.is_dir() and not directory.is_symlink(), "Invalid native wheel artifact")
            (wheel,) = directory.iterdir()
            require(wheel.is_file() and not wheel.is_symlink() and wheel.suffix == ".whl", "Invalid native wheel")
            require(wheel_version(wheel) == version, "Platform wheel version differs from the accepted candidate")
            # x86_64 publication is owned by the actual upgraded candidate.
            if platform != "manylinux-x86_64":
                files.append(wheel)
    require(len({path.name for path in files}) == len(files), "Duplicate publication filenames")
    output.mkdir(parents=True, exist_ok=False)
    expected = {path.name: sha256(path) for path in files}
    for source in files:
        target = output / source.name
        shutil.copyfile(source, target)
        require(sha256(target) == expected[source.name], "Publication copy changed")
    require(
        all(sha256(output / name) == digest for name, digest in record["artifacts"].items()),
        "Publication replaced an accepted distribution",
    )
    return expected


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--native-wheels", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    artifacts = stage(args.candidate, args.evidence, args.native_wheels, args.output)
    print(json.dumps({"artifacts": artifacts}, indent=2))


if __name__ == "__main__":
    main()
