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

"""Build the macOS preview, recovering only a busy disposable DMG mount."""

from __future__ import annotations

import errno
import os
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

DESKTOP = Path(__file__).resolve().parents[1]


def capture_macos_state(artifacts: Path, attempt: int) -> None:
    with (artifacts / f"macos-build-attempt-{attempt}-state.log").open("w", encoding="utf-8") as state:
        state.write(f"captured_at={datetime.now(timezone.utc).isoformat()}\n")  # noqa: UP017 -- macOS runner Python may be 3.9.
        for section, commands in (
            ("macOS", (["sw_vers"], ["uname", "-a"])),
            ("Xcode", (["xcodebuild", "-version"],)),
            (
                "toolchain",
                (["node", "--version"], ["pnpm", "--version"], ["rustc", "--version"], ["cargo", "--version"]),
            ),
            ("attached disk images", (["hdiutil", "info"],)),
            ("disks", (["diskutil", "list"],)),
            ("mounts", (["mount"],)),
            ("bundle files", (["find", "src-tauri/target/release/bundle", "-maxdepth", "4", "-print"],)),
        ):
            state.write(f"## {section}\n")
            state.flush()
            for command in commands:
                try:
                    subprocess.run(  # noqa: S603 -- Fixed diagnostic commands, no shell.
                        command, cwd=DESKTOP, stdout=state, stderr=subprocess.STDOUT, check=False, timeout=15
                    )
                except (OSError, subprocess.TimeoutExpired) as error:
                    state.write(f"Diagnostic unavailable: {type(error).__name__}\n")


def discard_busy_image(output: str, artifacts: Path) -> Path | None:
    created = re.findall(r"^created: (.+/rw\.\d+\..+\.dmg)$", output, re.MULTILINE)
    if len(created) != 1:
        message = "Cannot identify the temporary DMG from this packaging attempt"
        raise RuntimeError(message)
    image = Path(created[0]).resolve(strict=True)
    if image.parent != DESKTOP / "src-tauri/target/release/bundle/macos":
        message = "Refusing to clean a DMG outside this build's staging directory"
        raise RuntimeError(message)
    hdiutil = shutil.which("hdiutil")
    if not hdiutil:
        sys.exit("hdiutil is required for macOS packaging")
    info = subprocess.run([hdiutil, "info", "-plist"], capture_output=True, check=True)  # noqa: S603
    for mounted in plistlib.loads(info.stdout).get("images", []):
        if Path(mounted.get("image-path", "")).resolve() != image:
            continue
        devices = [
            entity["dev-entry"]
            for entity in mounted.get("system-entities", [])
            if re.fullmatch(r"/dev/disk\d+", entity.get("dev-entry", ""))
        ]
        if len(devices) != 1:
            message = "Cannot identify the temporary DMG's whole disk"
            raise RuntimeError(message)
        # This image belongs to the failed attempt and will be discarded, never published.
        detached = subprocess.run([hdiutil, "detach", "-force", devices[0]], check=False)  # noqa: S603 -- Validated disk identity.
        if detached.returncode == errno.EBUSY:
            # Keep the mounted backing file intact, but outside the directory
            # that create-dmg copies on the next attempt. Rename without copying.
            quarantine = Path(tempfile.mkdtemp(prefix="busy-dmg-", dir=artifacts))
            return image.rename(quarantine / image.name)
        detached.check_returncode()
    # A stale raw image must not be copied into the next DMG's source directory.
    image.unlink()
    return None


def main() -> int:
    artifacts = DESKTOP / ".artifacts"
    artifacts.mkdir(exist_ok=True)
    pnpm = shutil.which("pnpm")
    if not pnpm:
        sys.exit("pnpm is required for macOS packaging")
    command = [pnpm, "tauri", "build", "--ci", "--verbose", "--", "--locked"]
    with (artifacts / "macos-build.log").open("w", encoding="utf-8") as log:
        for attempt in range(1, 4):
            print(f"macOS packaging attempt {attempt}/3", flush=True)
            output = []
            with subprocess.Popen(  # noqa: S603 -- Fixed Tauri command, no shell.
                command, cwd=DESKTOP, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
            ) as process:
                if process.stdout is None:
                    message = "Missing packaging output stream"
                    raise RuntimeError(message)
                for line in process.stdout:
                    print(line, end="", flush=True)
                    log.write(line)
                    log.flush()
                    output.append(line)
                status = process.wait()
            if status == 0:
                return 0
            failure = "".join(output)
            (artifacts / f"macos-build-attempt-{attempt}.log").write_text(failure, encoding="utf-8")
            capture_macos_state(artifacts, attempt)
            busy_detach = bool(re.search(r'hdiutil: couldn.t eject "disk\d+" - Resource busy', failure))
            busy_create = "hdiutil: create failed - Resource busy" in failure
            if (
                attempt == 3
                or not (busy_detach or busy_create)
                or "failed to bundle project: error running bundle_dmg.sh" not in failure
            ):
                return status if status > 0 else 1
            if busy_detach:
                preserved = discard_busy_image(failure, artifacts)
                if preserved is not None:
                    warning = f"DMG detach returned exit {errno.EBUSY}; preserved mounted image at {preserved}."
                    print(warning, flush=True)
                    log.write(warning + "\n")
                    log.flush()
            if github_output := os.environ.get("GITHUB_OUTPUT"):
                with Path(github_output).open("a", encoding="utf-8") as output_file:
                    output_file.write("retried=true\n")
            print("Retrying busy DMG packaging without recompilation.", flush=True)
            time.sleep(attempt * 5)
            command = [pnpm, "tauri", "bundle", "--ci", "--verbose"]
    return 1


if __name__ == "__main__":
    sys.exit(main())
