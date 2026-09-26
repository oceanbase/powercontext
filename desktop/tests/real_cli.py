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

"""Exercise the registered native adapter against a real CLI with an isolated home and PATH."""

# Fixed local test programs; this harness never runs a renderer-supplied command.
# ruff: noqa: S603
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[2]
    artifacts = root / "desktop/.artifacts"
    (wheel,) = (artifacts / "server-wheel").glob("*.whl")
    with tempfile.TemporaryDirectory(prefix="desktop-real-cli-") as directory:
        fixture = Path(directory)
        package = fixture / "package"
        with zipfile.ZipFile(wheel) as archive:
            archive.extractall(package)
        (metadata,) = package.glob("*.dist-info/METADATA")
        version = next(
            line.removeprefix("Version: ")
            for line in metadata.read_text(encoding="utf-8").splitlines()
            if line.startswith("Version: ")
        )
        executable = fixture / "powercontext.exe"
        shutil.copyfile(root / ".venv/Scripts/powercontext.exe", executable)
        registration = fixture / "diagnostic-cli.json"
        digest = hashlib.sha256(executable.read_bytes()).hexdigest()
        registration.write_text(
            json.dumps({
                "executable": str(executable),
                "sha256": digest,
                "version": version,
                "source": "explicit_local_installation",
            }),
            encoding="utf-8",
        )
        system = os.environ["SYSTEMROOT"]
        environment = {
            "SYSTEMROOT": system,
            "WINDIR": system,
            "PATH": str(Path(system) / "System32"),
            "USERPROFILE": str(fixture),
            "HOME": str(fixture),
            "APPDATA": str(fixture / "roaming"),
            "LOCALAPPDATA": str(fixture / "local"),
            "TEMP": str(fixture),
            "TMP": str(fixture),
            "PYTHONPATH": str(package),
            "PYTHONNOUSERSITE": "1",
            "PYTHONUTF8": "1",
        }
        result = subprocess.run(
            [str(root / "desktop/src-tauri/target/debug/examples/diagnostic_cli_probe.exe"), str(registration)],
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=110,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode:
            raise RuntimeError(result.stderr[-2000:])
        report = {
            "version": version,
            "wheelSha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
            "launcherSha256": digest,
            "environment": "Isolated home and system-only PATH; wheel extracted on explicit test PYTHONPATH",
            "result": json.loads(result.stdout),
        }
        (artifacts / "real-cli.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
