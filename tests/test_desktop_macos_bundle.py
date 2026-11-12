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

"""The macOS packaging recovery keeps failures visible and cleans only its own image."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FAKE_TOOL = r"""import json
import os
import plistlib
import sys
from pathlib import Path

if sys.argv[1:] == ["--version"]:
    print("fixture version")
    sys.exit(0)

root = Path.cwd()
case = os.environ["DMG_TEST_CASE"]
calls = root / "calls.jsonl"
with calls.open("a") as output:
    output.write(json.dumps([Path(sys.argv[0]).name, *sys.argv[1:]]) + "\n")
image = root / "src-tauri/target/release/bundle/macos/rw.123.Preview.dmg"
if case == "foreign":
    image = root / "outside/rw.123.Preview.dmg"
if Path(sys.argv[0]).name == "pnpm":
    if sys.argv[2] == "bundle":
        assert not image.exists(), "stale image would be included in the next DMG"
        if case in {"recover", "create", "detach-busy"}:
            print("Finished DMG packaging")
            sys.exit(0)
    if case == "compiler":
        print("error: could not compile application")
        sys.exit(23)
    if case == "create":
        print("hdiutil: create failed - Resource busy")
        print("failed to bundle project: error running bundle_dmg.sh")
        sys.exit(1)
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"disposable raw image")
    print("created: " + str(image))
    print('hdiutil: couldn\'t eject "disk2" - Resource busy')
    print("failed to bundle project: error running bundle_dmg.sh")
    sys.exit(1)
if sys.argv[1] == "info":
    sys.stdout.buffer.write(plistlib.dumps({"images": [
        {"image-path": str(root / "unrelated.dmg"), "system-entities": [{"dev-entry": "/dev/disk9"}]},
        {"image-path": str(image), "system-entities": [{"dev-entry": "/dev/disk2"}, {"dev-entry": "/dev/disk2s1"}]}
    ]}))
else:
    assert sys.argv[1:] == ["detach", "-force", "/dev/disk2"]
    if case == "detach-busy":
        print('hdiutil: couldn\'t eject "disk2" - Resource busy', file=sys.stderr)
        sys.exit(16)
"""


@pytest.mark.skipif(sys.platform == "win32", reason="macOS packaging uses POSIX executables")
@pytest.mark.parametrize("case", ["recover", "create", "compiler", "persistent", "foreign", "detach-busy"])
def test_macos_packaging_recovery(tmp_path: Path, case: str) -> None:
    desktop = tmp_path / "desktop"
    scripts = desktop / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(ROOT / "desktop/scripts/build_macos.py", scripts / "build_macos.py")
    tools = tmp_path / "bin"
    tools.mkdir()
    for name in ("pnpm", "hdiutil"):
        executable = tools / name
        executable.write_text(f"#!{sys.executable}\n" + FAKE_TOOL, encoding="utf-8")
        executable.chmod(0o700)
    result = subprocess.run(
        [sys.executable, str(scripts / "build_macos.py")],
        cwd=desktop,
        env={
            **os.environ,
            "PATH": str(tools) + os.pathsep + os.environ["PATH"],
            "DMG_TEST_CASE": case,
            "GITHUB_OUTPUT": str(tmp_path / "step-output"),
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    calls = [json.loads(line) for line in (desktop / "calls.jsonl").read_text().splitlines()]
    packages = [call[2] for call in calls if call[0] == "pnpm"]
    detach = [call for call in calls if call[0:2] == ["hdiutil", "detach"]]
    log = (desktop / ".artifacts/macos-build.log").read_text()
    if case in {"recover", "create", "detach-busy"}:
        assert result.returncode == 0, result.stderr
        assert packages == ["build", "bundle"]
        assert detach == ([] if case == "create" else [["hdiutil", "detach", "-force", "/dev/disk2"]])
        assert "Resource busy" in log and "Finished DMG packaging" in log
        assert (tmp_path / "step-output").read_text() == "retried=true\n"
        assert "Resource busy" in (desktop / ".artifacts/macos-build-attempt-1.log").read_text()
        assert (desktop / ".artifacts/macos-build-attempt-1-state.log").is_file()
        if case == "detach-busy":
            images = list((desktop / ".artifacts").glob("busy-dmg-*/rw.*.dmg"))
            assert len(images) == 1 and images[0].read_bytes() == b"disposable raw image"
            assert "exit 16" in log
    elif case == "compiler":
        assert result.returncode == 23
        assert packages == ["build"]
        assert detach == []
        assert "could not compile" in log
    elif case == "persistent":
        assert result.returncode != 0
        assert packages == ["build", "bundle", "bundle"]
        assert len(detach) == 2
        assert log.count("Resource busy") == 3
    else:
        assert result.returncode != 0
        assert packages == ["build"]
        assert detach == []
        assert (desktop / "outside/rw.123.Preview.dmg").exists()
