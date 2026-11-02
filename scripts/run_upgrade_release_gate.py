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

"""Validate the complete distribution set and gate its publication on upgrades."""

import argparse
import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from email.parser import BytesParser
from pathlib import Path

from upgrade_release_gate import ROOT, GateError, gate, require, sha256, wheel_version


def wheel_contents(wheel: Path) -> dict[str, str]:
    """Compare installed payloads, excluding only the derived RECORD hash listing."""
    with zipfile.ZipFile(wheel) as archive:
        names = [item.filename for item in archive.infolist() if not item.is_dir()]
        require(len(set(names)) == len(names), "Duplicate wheel members")
        return {
            name: hashlib.sha256(archive.read(name)).hexdigest()
            for name in names
            if not name.endswith(".dist-info/RECORD")
        }


def validate_sdist(sdist: Path, wheel: Path, work: Path) -> None:
    expected = wheel_version(wheel)
    require(sdist.name == f"powercontext-{expected}.tar.gz", "Source distribution version differs from the wheel")
    with tarfile.open(sdist, "r:gz") as archive:
        names = [item for item in archive.getmembers() if item.name == f"powercontext-{expected}/PKG-INFO"]
        require(len(names) == 1 and names[0].isfile(), "Source distribution requires one root PKG-INFO")
        stream = archive.extractfile(names[0])
        if stream is None:
            raise GateError("Cannot read source distribution metadata")  # noqa: TRY003 -- operator diagnostic
        metadata = BytesParser().parsebytes(stream.read())
        require(
            metadata["Name"] == "powercontext" and metadata["Version"] == expected,
            "Source distribution metadata differs from the wheel",
        )
    rebuilt = work / "rebuilt"
    rebuilt.mkdir()
    with (work / "sdist-build.log").open("w", encoding="utf-8") as log:
        subprocess.run(  # noqa: S603 -- reviewed local distribution and fixed build tool; no shell
            ["uv", "build", "--wheel", str(sdist), "--out-dir", str(rebuilt)],  # noqa: S607 -- project tool on PATH
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=600,
            check=True,
        )
    wheels = list(rebuilt.glob("*.whl"))
    require(len(wheels) == 1, "Source distribution must build exactly one wheel")
    require(wheels[0].name == wheel.name, "Source distribution produces a different wheel filename")
    require(
        wheel_contents(wheels[0]) == wheel_contents(wheel),
        "Source distribution builds different wheel contents; rebuild the complete publication set",
    )


def stage_distributions(dist: Path, staging: Path, *, release: bool) -> tuple[Path, list[Path]]:
    """Freeze all publication inputs before testing; later dist changes cannot be uploaded."""
    files = sorted(path for path in dist.iterdir() if path.name != ".gitignore")
    require(
        all(path.is_file() and not path.is_symlink() for path in files), "Distribution inputs must be regular files"
    )
    wheels = [path for path in files if path.suffix == ".whl"]
    sources = [path for path in files if path.name.endswith(".tar.gz")]
    require(len(wheels) == 1, "Build exactly one release wheel before running upgrade acceptance")
    require(len(sources) <= 1, "Build at most one source distribution")
    require(set(files) == set(wheels + sources), "Unexpected files in publication directory")
    require(not release or len(sources) == 1, "Release publication requires one wheel and one source distribution")
    staging.mkdir()
    staged = []
    for path in files:
        copied = staging / path.name
        shutil.copyfile(path, copied)
        staged.append(copied)
    wheel = staging / wheels[0].name
    if sources:
        validate_sdist(staging / sources[0].name, wheel, staging.parent)
    return wheel, staged


def accept_distributions(dist: Path, output: Path, *, release: bool, release_notes: str | None) -> list[Path]:
    # A private snapshot prevents a leftover or concurrent dist write from joining
    # publication after acceptance. Keep the same bytes through testing and upload.
    require(not output.exists(), "Use a fresh evidence directory")
    with tempfile.TemporaryDirectory(prefix="pc-release-inputs-") as temporary:
        work = Path(temporary)
        try:
            wheel, files = stage_distributions(dist, work / "inputs", release=release)
            expected = {path.name: sha256(path) for path in files}
            gate(ROOT, wheel, output, release=release, release_notes=release_notes)
            require({path.name: sha256(path) for path in files} == expected, "Distribution changed during acceptance")
            validated = output / "validated-dist"
            validated.mkdir()
            accepted = []
            for path in files:
                destination = validated / path.name
                shutil.copyfile(path, destination)
                require(sha256(destination) == expected[path.name], "Validated distribution copy changed")
                accepted.append(destination)
            (output / "publication.json").write_text(
                json.dumps({"artifacts": expected}, indent=2) + "\n", encoding="utf-8"
            )
            return accepted
        finally:
            if (work / "sdist-build.log").is_file():
                output.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(work / "sdist-build.log", output / "sdist-build.log")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--release", action="store_true")
    parser.add_argument(
        "--publish", action="store_true", help="Upload only this run's validated files; requires --release"
    )
    notes = parser.add_mutually_exclusive_group()
    notes.add_argument("--release-event", type=Path)
    notes.add_argument("--release-notes", type=Path)
    args = parser.parse_args()
    try:
        require(not args.publish or args.release, "--publish requires --release")
        release_notes = None
        if args.release_event:
            release_notes = json.loads(args.release_event.read_text(encoding="utf-8"))["release"]["body"] or ""
        elif args.release_notes:
            release_notes = args.release_notes.read_text(encoding="utf-8")
        output = (args.output or Path(tempfile.mkdtemp(prefix="pc-upgrade-evidence-")) / "report").resolve()
        accepted = accept_distributions(args.dist.resolve(), output, release=args.release, release_notes=release_notes)
        result = json.loads((output / "result.json").read_text(encoding="utf-8"))
        print(
            f"Upgrade gate passed: {result['mode']}; "
            f"{len(result['resolved_matrix']['candidate_cases'])} discovered candidates; "
            f"{len(result['cases'])} accepted upgrade combinations. "
            f"Evidence: {output / 'result.json'}"
        )
        if args.publish:
            subprocess.run(["uv", "publish", *map(str, accepted)], check=True)  # noqa: S603, S607 -- project tool, exact validated files
    except (GateError, OSError, ValueError, subprocess.SubprocessError, tarfile.TarError, zipfile.BadZipFile) as error:
        raise SystemExit(f"Upgrade gate failed: {error}") from error  # noqa: TRY003 -- operator diagnostic


if __name__ == "__main__":
    main()
