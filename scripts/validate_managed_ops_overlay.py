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

"""Private Intel acceptance using an immutable retained runtime and one facade overlay."""

# ruff: noqa: S101, S202 - private acceptance assertions and digest-checked archive

from __future__ import annotations

import argparse
import email.parser
import hashlib
import json
import platform
import re
import subprocess
import tarfile
import time
import traceback
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

import ci_ops_bundle_smoke

ARCHIVE_SHA = "bff6172888e62cd3ef781a2299548530a8817ad319dc7cb6d068eb7f8cafdb0e"
RUNTIME_SHA = "743da5bac0c5f627d88b9b31212dcd6a1d12a5a0bd08c0a5749eb1503570ede9"
OLD_FACADE = "02dea7e9d5860b2336badd2761dc2cccf0969ef370eb3a0054b9108b79a0b312"
NEW_FACADE = "0d0394433949cbd52d2709a0e39f4b33dc573aad1f4eef5652f2cc0aadae4064"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def verify_files(bundle: Path, expected: dict[str, str]) -> None:
    for name, sha in expected.items():
        path = bundle / name
        assert path.resolve().is_relative_to(bundle.resolve())
        assert path.is_file() and digest(path) == sha, name


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    assert (platform.system(), platform.machine()) == ("Darwin", "x86_64")
    assert digest(args.archive) == ARCHIVE_SHA and digest(args.candidate) == NEW_FACADE
    work = args.work.resolve()
    work.mkdir(parents=True, exist_ok=False)
    evidence = work / "evidence"
    evidence.mkdir()
    baseline = work / "retained"
    with zipfile.ZipFile(args.archive) as archive:
        for item in archive.infolist():
            name = PurePosixPath(item.filename)
            assert not name.is_absolute() and ".." not in name.parts
        assert archive.testzip() is None
        archive.extractall(baseline)
    runtime = baseline / "ops-diagnostic-runtime.tar.gz"
    assert digest(runtime) == RUNTIME_SHA
    with tarfile.open(runtime) as archive:
        archive.extractall(work / "candidate", filter="data")
    bundle = work / "candidate" / "ops"
    manifest_path = bundle / "bundle.json"
    original_manifest_bytes = manifest_path.read_bytes()
    original_manifest = json.loads(original_manifest_bytes)
    expected = original_manifest["files_sha256"]
    verify_files(bundle, expected)
    inputs = json.loads((baseline / "resource-inputs/inputs.json").read_text())
    normalized = lambda name: re.sub(r"[-_.]+", "-", name).lower()
    installed = {}
    for path in (bundle / "packages").glob("*.dist-info/METADATA"):
        metadata = email.parser.Parser().parsestr(path.read_text())
        installed[normalized(metadata["Name"])] = metadata["Version"]
    assert installed == {normalized(item["name"]): item["version"] for item in inputs["wheels"]}
    assert len(installed) == 121
    facade = bundle / "packages/powercontext/ops/__init__.py"
    assert digest(facade) == OLD_FACADE
    entry = bundle / "entry.py"
    original_entry = baseline / "ops-diagnostic/entry-original.py"
    # The retained entry has observation hooks. Restore the exact pre-observation entry.
    import build_ops_bundle

    assert original_entry.read_text() == build_ops_bundle._ENTRY
    before = dict(expected)
    facade.write_bytes(args.candidate.read_bytes())
    entry.write_bytes(original_entry.read_bytes())
    after = {name: digest(bundle / name) for name in expected}
    changed = {name for name in after if before[name] != after[name]}
    assert changed == {"entry.py", "packages/powercontext/ops/__init__.py"}
    manifest = {**original_manifest, "files_sha256": after}
    manifest["installed_bytes"] = sum((bundle / name).stat().st_size for name in after)
    (evidence / "original-bundle.json").write_bytes(original_manifest_bytes)
    write(manifest_path, manifest)
    write(evidence / "candidate-bundle.json", manifest)
    verify_files(bundle, manifest["files_sha256"])
    write(
        evidence / "identity.json",
        {
            "host": {"system": platform.system(), "machine": platform.machine(), "release": platform.release()},
            "parent_python": platform.python_version(),
            "artifact_sha256": ARCHIVE_SHA,
            "runtime_archive_sha256": RUNTIME_SHA,
            "original_bundle_manifest_sha256": digest(evidence / "original-bundle.json"),
            "candidate_facade_sha256": NEW_FACADE,
            "unchanged_dependency_versions": installed,
            "all_original_manifest_files_verified": len(expected),
            "changed_files": [{"path": name, "before": before[name], "after": after[name]} for name in sorted(changed)],
            "candidate_manifest_sha256": digest(manifest_path),
            "boundary": "Retained original AA runtime with only facade overlay and restored uninstrumented entry; manifest updated to match those two files. Not a new producer, determinism, or whole-workflow qualification.",
        },
    )
    original_run = subprocess.run
    stages = []

    def observed_run(command, *positional, **kwargs):
        index = len(stages) + 1
        started = time.monotonic()
        stage = {"index": index, "started_at": datetime.now(UTC).isoformat(), "timeout": kwargs.get("timeout")}
        assert index <= 4
        stages.append(stage)
        try:
            result = original_run(command, *positional, **kwargs)
        except subprocess.SubprocessError as error:
            stage.update(outcome="failed", exception=type(error).__name__)
            for name, content in (
                ("stdout", getattr(error, "stdout", None)),
                ("stderr", getattr(error, "stderr", None)),
            ):
                if content is not None:
                    (evidence / f"{index}-{name}.log").write_bytes(
                        content.encode() if isinstance(content, str) else content
                    )
            raise
        else:
            stage.update(outcome="completed", returncode=result.returncode)
            for name, content in (("stdout", result.stdout), ("stderr", result.stderr)):
                (evidence / f"{index}-{name}.log").write_bytes(
                    content.encode() if isinstance(content, str) else content or b""
                )
            return result
        finally:
            stage.update(elapsed_seconds=time.monotonic() - started, completed_at=datetime.now(UTC).isoformat())
            write(evidence / "stages.json", stages)

    subprocess.run = observed_run  # ty: ignore[invalid-assignment] - bounded private call recording
    try:
        ci_ops_bundle_smoke.check(bundle)
        assert [stage["timeout"] for stage in stages] == [60, 60, 60, 90]
        assert [stage["returncode"] for stage in stages] == [0, 1, 0, 0]
    except BaseException:
        (evidence / "exception.txt").write_text(traceback.format_exc(), encoding="utf-8")
        raise
    finally:
        subprocess.run = original_run
    verify_files(bundle, manifest["files_sha256"])
    write(evidence / "RESULT.json", {"status": "passed", "stages": stages, "files_unchanged_after_smoke": True})


if __name__ == "__main__":
    main()
