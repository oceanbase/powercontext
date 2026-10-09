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

"""Native installer conformance with real uv and offline synthetic distributions."""

import csv
import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    case
    for path in sorted((ROOT / "tests/fixtures/installation").glob("*.json"))
    for case in json.loads(path.read_text())["cases"]
]
CLI = """import importlib.util
import json
import os
from pathlib import Path
import sys

def main():
    args = sys.argv[1:]
    if args == ["--version"]:
        print(os.environ.get("CONTRACT_REPORTED_VERSION", "1.2.0"))
    elif not args or args[-1] == "--help":
        command = " ".join(args[:-1]) if args else ""
        missing = json.loads(os.environ.get("CONTRACT_MISSING_COMMANDS", "[]"))
        if command in missing:
            return 2
        if command == "server run":
            import contract_server
        print("Synthetic installation contract CLI")
    elif args[:2] == ["config", "init"]:
        if not sys.stdin.isatty():
            return 2
        status = int(os.environ.get("CONTRACT_CONFIG_EXIT", "0"))
        if status == 0:
            Path(args[args.index("--output") + 1]).write_text("POWERCONTEXT_SERVER_HTTP_PORT=18000\\n")
        return status
    elif args[:2] == ["config", "validate"]:
        return int(os.environ.get("CONTRACT_VALIDATE_EXIT", "0"))
    elif args[:2] == ["service", "install"]:
        env_file = args[args.index("--env-file") + 1]
        Path(os.environ["CONTRACT_SERVICE_RESULT"]).write_text(json.dumps({"env_file": env_file}))
        return int(os.environ.get("CONTRACT_SERVICE_EXIT", "0"))
    elif args[:2] == ["service", "status"]:
        return 0
    elif args and args[0] == "doctor":
        env_file = args[args.index("--env-file") + 1]
        Path(os.environ["CONTRACT_DOCTOR_RESULT"]).write_text(json.dumps({"env_file": env_file}))
        return int(os.environ.get("CONTRACT_DOCTOR_EXIT", "0"))
    elif args and args[0] == "setup" and "select" in args:
        hosts = [args[i + 1] for i, arg in enumerate(args) if arg == "--host"]
        ref = args[args.index("--ref") + 1]
        selected = {"hosts": hosts, "ref": ref}
        if "--env-file" in args:
            selected["env_file"] = args[args.index("--env-file") + 1]
        Path(os.environ["CONTRACT_SETUP_RESULT"]).write_text(json.dumps(selected))
        return int(os.environ.get("CONTRACT_SETUP_EXIT", "0"))
    elif args == ["contract-capabilities"]:
        print(json.dumps({"server_extra": importlib.util.find_spec("contract_server") is not None}))
    else:
        print("Synthetic installation contract CLI")
    return 0
"""


def wheel(directory: Path, name: str, files: dict[str, str], extra_metadata: str = "") -> None:
    """Build a tiny standards-shaped wheel using only stdlib ZIP/CSV."""
    info = f"{name}-1.2.0.dist-info"
    entries = files | {
        f"{info}/METADATA": f"Metadata-Version: 2.3\nName: {name}\nVersion: 1.2.0\n{extra_metadata}\n",
        f"{info}/WHEEL": "Wheel-Version: 1.0\nGenerator: installation-contract\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    record = io.StringIO()
    writer = csv.writer(record)
    writer.writerows((path, "", "") for path in entries)
    writer.writerow((f"{info}/RECORD", "", ""))
    entries[f"{info}/RECORD"] = record.getvalue()
    with zipfile.ZipFile(directory / f"{name}-1.2.0-py3-none-any.whl", "w") as archive:
        for path, content in entries.items():
            archive.writestr(path, content)


def executable_path(directory: Path, uv: str, windows: bool, include_git: bool) -> str:
    """Expose real bootstrap prerequisites, optionally excluding Git."""
    tools = directory / "tools"
    tools.mkdir()
    # Real executables only; uv creates the platform-native console launcher.
    executables = {"uv": uv} if windows else {"uv": uv, "python": sys.executable}
    for name, executable in executables.items():
        destination = tools / (name + ".exe" if windows else name)
        if windows:
            shutil.copy2(executable, destination)
        else:
            destination.symlink_to(executable)
    git = shutil.which("git")
    if include_git and not git:
        pytest.skip("explicit-host contract requires Git discovery")
    if not windows:
        for name in ("curl", "wget", "uname", "mktemp", "rm", "dirname", "cat", "sh", "readlink"):
            executable = shutil.which(name)
            if executable:
                (tools / name).symlink_to(executable)
        if include_git and git:
            (tools / "git").symlink_to(git)
    elif include_git and git:
        # Presence check only: synthetic host setup never executes Git.
        shutil.copy2(git, tools / "git.exe")
    if windows:
        return os.pathsep.join((
            str(tools),
            str(Path(sys.executable).parent),
            str(Path(os.environ["SYSTEMROOT"]) / "System32"),
        ))
    return str(tools)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_installation_contract(case: dict[str, Any], tmp_path: Path) -> None:
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("native installation contract requires real uv")
    windows = sys.platform == "win32"
    if case.get("terminal") and windows:
        pytest.skip("controlling-terminal pipe regression requires POSIX")
    shell = shutil.which("powershell") or shutil.which("pwsh") if windows else shutil.which("bash")
    if not shell:
        pytest.skip("native installer shell is unavailable")
    path = executable_path(tmp_path, uv, windows, case.get("git", False))
    packages = tmp_path / "packages"
    packages.mkdir()
    wheel(packages, "contract_server", {"contract_server.py": ""})
    wheel(
        packages,
        "powercontext",
        {
            "powercontext_contract.py": CLI,
            "powercontext-1.2.0.dist-info/entry_points.txt": "[console_scripts]\npowercontext = powercontext_contract:main\n",
        },
        "Provides-Extra: cli\nProvides-Extra: server\nRequires-Dist: contract-server==1.2.0; extra == 'server'\n",
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("UV_", "POWERCONTEXT_", "PIP_", "PYTHON", "CONTRACT_")) and key != "VIRTUAL_ENV"
    }
    environment.update({
        "PYTHONIOENCODING": "utf-8",
        "HOME": str(tmp_path),
        "USERPROFILE": str(tmp_path),
        "TMPDIR": str(tmp_path),
        "PATH": path,
        "UV_TOOL_DIR": str(tmp_path / "tool"),
        "UV_TOOL_BIN_DIR": str(tmp_path / "bin"),
        "UV_CACHE_DIR": str(tmp_path / "cache"),
        "UV_NO_CONFIG": "1",
        "UV_NO_INDEX": "1",
        "UV_FIND_LINKS": str(packages),
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_OFFLINE": "1",
        "CONTRACT_SETUP_RESULT": str(tmp_path / "setup.json"),
        "CONTRACT_SETUP_EXIT": str(case.get("setup_exit", 0)),
        "CONTRACT_REPORTED_VERSION": case.get("reported_version", "1.2.0"),
        "CONTRACT_MISSING_COMMANDS": json.dumps(case.get("missing_commands", [])),
        "CONTRACT_VALIDATE_EXIT": str(case.get("validate_exit", 0)),
        "CONTRACT_CONFIG_EXIT": str(case.get("config_exit", 0)),
        "CONTRACT_SERVICE_EXIT": str(case.get("service_exit", 0)),
        "CONTRACT_DOCTOR_EXIT": str(case.get("doctor_exit", 0)),
        "CONTRACT_SERVICE_RESULT": str(tmp_path / "service.json"),
        "CONTRACT_DOCTOR_RESULT": str(tmp_path / "doctor.json"),
    })
    env_file = tmp_path / "existing configuration.env"
    if case.get("environment_file"):
        env_file.write_text("POWERCONTEXT_SERVER_HTTP_PORT=18000\n", encoding="utf-8")
        env_file.chmod(0o600)
    arguments = [str(env_file) if value == "{env_file}" else value for value in case["args"]]
    script = ROOT / "website/public" / ("install.ps1" if windows else "install.sh")
    command = (
        [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)] if windows else [shell, str(script)]
    )
    if case.get("terminal"):
        controller = """import errno, os, pty, sys
pid, terminal = pty.fork()
if pid == 0:
    shell, script, *args = sys.argv[1:]
    os.execv(shell, [shell, '-o', 'pipefail', '-c',
        'cat "$1" | "$2" -s -- "${@:3}"', 'installation', script, shell, *args])
try:
    while True:
        try:
            data = os.read(terminal, 4096)
        except OSError as error:
            if error.errno == errno.EIO:
                break
            raise
        if not data:
            break
        os.write(1, data)
finally:
    os.close(terminal)
_, status = os.waitpid(pid, 0)
sys.exit(os.waitstatus_to_exitcode(status))
"""
        command = [sys.executable, "-c", controller, *command]
    result = subprocess.run(
        [*command, "--region", "global", *arguments], env=environment, capture_output=True, text=True, timeout=60
    )
    assert_installation_outcome(case, tmp_path, result, environment)


def assert_installation_outcome(case, tmp_path, result, environment) -> None:
    expected = case["expected"]
    windows = sys.platform == "win32"
    env_file = tmp_path / "existing configuration.env"
    if "configuration_saved" in expected:
        assert env_file.exists() == expected["configuration_saved"]
    assert result.returncode == expected["exit_code"], result.stdout + result.stderr
    executable = tmp_path / "bin" / ("powercontext.exe" if windows else "powercontext")
    assert executable.exists() == expected["runtime_installed"]
    setup = tmp_path / "setup.json"
    observed = json.loads(setup.read_text()) if setup.exists() else {"hosts": []}
    assert observed["hosts"] == expected.get("selected_hosts", [])
    if "setup_ref" in expected:
        assert observed["ref"] == expected["setup_ref"]
    if expected.get("setup_env_file"):
        assert observed["env_file"] == str(env_file)
    for action in ("service", "doctor"):
        observed_file = tmp_path / f"{action}.json"
        assert observed_file.exists() == expected.get(action, False)
        if observed_file.exists():
            assert json.loads(observed_file.read_text())["env_file"] == str(env_file)
    if case.get("environment_file"):
        assert env_file.read_text() == "POWERCONTEXT_SERVER_HTTP_PORT=18000\n"
    if expected.get("verification_failed"):
        assert "Runtime installed:" not in result.stdout
    if "error_contains" in expected:
        assert expected["error_contains"] in result.stdout + result.stderr
    if expected["runtime_installed"] and "server_extra" in expected:
        capabilities = subprocess.check_output([str(executable), "contract-capabilities"], env=environment, text=True)
        assert json.loads(capabilities)["server_extra"] == expected["server_extra"]
