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

from fixture_qualification import fixture_profile


def main():
    root = Path(__file__).resolve().parents[2]
    artifacts = root / "desktop/.artifacts"
    qualification = fixture_profile()
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
        node = shutil.which("node")
        node_directory = str(Path(node).parent) if node else ""
        if os.name == "nt":
            executable = fixture / "powercontext.exe"
            source_executable = root / ".venv/Scripts/powercontext.exe"
            probe = root / "desktop/src-tauri/target/debug/examples/diagnostic_cli_probe.exe"
            system = os.environ["SYSTEMROOT"]
            environment = {
                "SYSTEMROOT": system,
                "WINDIR": system,
                "PATH": os.pathsep.join(value for value in (node_directory, str(Path(system) / "System32")) if value),
                "USERPROFILE": str(fixture),
                "HOME": str(fixture),
                "APPDATA": str(fixture / "roaming"),
                "LOCALAPPDATA": str(fixture / "local"),
                "KIMI_CODE_HOME": str(fixture / "kimi"),
                "TEMP": str(fixture),
                "TMP": str(fixture),
                "PYTHONPATH": str(package),
                "PYTHONNOUSERSITE": "1",
                "PYTHONUTF8": "1",
            }
        else:
            executable = fixture / "powercontext"
            source_executable = root / ".venv/bin/powercontext"
            probe = root / "desktop/src-tauri/target/debug/examples/diagnostic_cli_probe"
            environment = {
                "PATH": os.pathsep.join(
                    filter(None, [node_directory, "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"])
                ),
                "HOME": str(fixture),
                "XDG_CONFIG_HOME": str(fixture / "config"),
                "XDG_DATA_HOME": str(fixture / "data"),
                "XDG_CACHE_HOME": str(fixture / "cache"),
                "KIMI_CODE_HOME": str(fixture / "kimi"),
                "TMPDIR": str(fixture / "tmp"),
                "PYTHONPATH": str(package),
                "PYTHONNOUSERSITE": "1",
                "PYTHONUTF8": "1",
            }
        for name in (
            "HOME",
            "USERPROFILE",
            "APPDATA",
            "LOCALAPPDATA",
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
            "XDG_CACHE_HOME",
            "TEMP",
            "TMP",
            "TMPDIR",
        ):
            if name in environment:
                Path(environment[name]).mkdir(parents=True, exist_ok=True)
        fake_kimi = fixture / "bin" / ("kimi.cmd" if os.name == "nt" else "kimi")
        fake_kimi.parent.mkdir(parents=True, exist_ok=True)
        fake_kimi.write_text(
            "@echo off\r\nexit /b 0\r\n" if os.name == "nt" else "#!/bin/sh\nexit 0\n", encoding="utf-8"
        )
        if os.name != "nt":
            fake_kimi.chmod(0o700)
        environment["KIMI_CLI_BIN"] = str(fake_kimi)
        setup = subprocess.run(
            [
                str(source_executable),
                "setup",
                "kimi-code",
                "--source",
                str(root),
                "--scope-id",
                "desktop-ci-scope",
                "--server-url",
                "http://127.0.0.1:1",
                "--allow-insecure-http",
                "--profile",
                "full",
                "--json",
            ],
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=45,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
        configured = Path(environment["KIMI_CODE_HOME"]) / "plugins/managed/powercontext/powercontext.json"
        if not configured.is_file():
            raise RuntimeError(
                "configured Kimi fixture setup failed: "
                + (setup.stderr[-2000:] or setup.stdout[-2000:] or f"exit code {setup.returncode}")
            )
        shutil.copyfile(source_executable, executable)
        registration = fixture / "diagnostic-cli.json"
        digest = hashlib.sha256(executable.read_bytes()).hexdigest()
        registration.write_text(
            json.dumps({
                "executable": str(executable),
                "sha256": digest,
                "source": "explicit_local_installation",
            }),
            encoding="utf-8",
        )
        result = subprocess.run(
            [str(probe), str(registration)],
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=110,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
        )
        if result.returncode:
            raise RuntimeError(result.stderr[-2000:])
        native_result = json.loads(result.stdout)
        if any(native_result[kind].get("cliVersion") not in (None, version) for kind in ("service", "integrations")):
            raise RuntimeError("observed_cli_version_mismatch")
        report = {
            "version": version,
            "serverCommit": qualification["serverCommit"],
            "wheelSha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
            "launcherSha256": digest,
            "environment": "Isolated home and system-only PATH; wheel extracted on explicit test PYTHONPATH",
            "result": native_result,
        }
        (artifacts / "real-cli.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
