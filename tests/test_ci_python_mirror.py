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

"""Exercise CI installation routing through disposable installer subprocesses."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/ci_python_mirror.py"
MIRROR = "https://mirrors.aliyun.com/pypi/simple/"
pytestmark = pytest.mark.skipif(os.name == "nt", reason="The CI installer probe uses POSIX executables")


def test_configure_exposes_installer_on_hosted_runner(tmp_path: Path):
    github_env = tmp_path / "github-env"
    env: dict[str, str] = {
        **os.environ,
        "GITHUB_ENV": str(github_env),
        "RUNNER_OS": "Linux",
        "RUNNER_ENVIRONMENT": "github-hosted",
    }
    env.pop("PC_CI_PYTHON_MIRROR", None)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "configure"],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    values = dict(line.split("=", 1) for line in github_env.read_text().splitlines())
    assert Path(values["PC_CI_PYTHON_INSTALLER"]) == SCRIPT.resolve()
    assert "PC_CI_PYTHON_MIRROR" not in values


LOCK = b"""version = 1
[[package]]
name = "demo"
version = "1.2.3"
source = { registry = "https://pypi.org/simple" }
sdist = { url = "https://files.pythonhosted.org/packages/demo.tar.gz", hash = "sha256:abcdef", size = 42 }
wheels = [
    { url = "https://files.pythonhosted.org/packages/demo.whl", hash = "sha256:123456", size = 64 },
]
[[package]]
name = "direct"
source = { url = "https://files.pythonhosted.org/packages/direct.whl#sha256=789abc" }
"""


@pytest.fixture
def probe(tmp_path):
    uv = tmp_path / "bin" / "uv"
    uv.parent.mkdir()
    uv.write_text(
        f"#!{sys.executable}\n"
        + """import json, os, pathlib, sys, time
args = sys.argv[1:]
lock = pathlib.Path(os.environ['PROBE_LOCK'])
index = os.environ.get('UV_INDEX') or os.environ.get('PIP_INDEX_URL')
stage = 'install'
if args[0] == 'run' and '--' in args:
    command = args[args.index('--') + 1:]
    stage = 'prepare' if command == ['python', '-c', 'pass'] else 'actual'
if stage == 'actual' and os.environ.get('PROBE_IMPLICIT_RELOCK') and '--frozen' not in args:
    lock.write_text('actual relocked project\\n')
record = {'args': args, 'index': index, 'stage': stage, 'lock': lock.read_text()}
with open(os.environ['PROBE_RECORDS'], 'a') as output:
    output.write(json.dumps(record) + '\\n')
if stage == 'actual':
    sys.exit(int(os.environ.get('PROBE_ACTUAL_EXIT', '0')))
if os.environ.get('PROBE_MUTATE_LOCK'):
    lock.write_text('installer changed lock\\n')
if os.environ.get('PROBE_WAIT'):
    pathlib.Path(os.environ['PROBE_WAIT']).touch()
    time.sleep(30)
key = 'PROBE_MIRROR_EXIT' if index == os.environ.get('PC_CI_PYTHON_MIRROR') else 'PROBE_OFFICIAL_EXIT'
sys.exit(int(os.environ.get(key, '0')))
""",
        encoding="utf-8",
    )
    uv.chmod(0o755)
    lock = tmp_path / "uv.lock"
    lock.write_bytes(LOCK)
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith(("UV_", "PIP_", "PC_CI_", "RUNNER_"))
    }
    environment.update(
        PATH=f"{uv.parent}{os.pathsep}{environment['PATH']}",
        PC_CI_PYTHON_MIRROR=MIRROR,
        PROBE_LOCK=str(lock),
        PROBE_RECORDS=str(tmp_path / "records.jsonl"),
    )
    return tmp_path, environment, uv


def invoke(probe, *arguments, **changes):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        env={**probe[1], **changes},
        cwd=probe[0],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


def records(probe):
    return [json.loads(line) for line in Path(probe[1]["PROBE_RECORDS"]).read_text().splitlines()]


@pytest.mark.parametrize("runner_os", ["Linux", "macOS"])
def test_configure_enables_only_internal_linux(tmp_path: Path, runner_os: str):
    destination = tmp_path / "github-env"
    environment: dict[str, str] = {
        **os.environ,
        "GITHUB_ENV": str(destination),
        "RUNNER_OS": runner_os,
        "RUNNER_ENVIRONMENT": "self-hosted",
    }
    environment.pop("PC_CI_PYTHON_MIRROR", None)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "configure"], env=environment, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    values = dict(line.split("=", 1) for line in destination.read_text().splitlines())
    assert Path(values["PC_CI_PYTHON_INSTALLER"]) == SCRIPT.resolve()
    assert values.get("PC_CI_PYTHON_MIRROR") == (MIRROR if runner_os == "Linux" else None)
    assert not any(key.startswith(("UV_", "PIP_")) for key in values)


def test_sync_changes_only_artifact_locations_and_restores_bytes(probe):
    result = invoke(probe, "sync", "--locked", "--extra", "server")
    assert result.returncode == 0, result.stderr
    calls = records(probe)
    assert len(calls) == 1
    assert calls[0]["args"] == ["sync", "--locked", "--extra", "server"]
    assert calls[0]["index"] == MIRROR
    expected = LOCK.replace(
        b"https://files.pythonhosted.org/packages/demo", b"https://mirrors.aliyun.com/pypi/packages/demo"
    )
    assert calls[0]["lock"].encode() == expected
    assert Path(probe[1]["PROBE_LOCK"]).read_bytes() == LOCK


@pytest.mark.parametrize("official_exit", [0, 17])
def test_sync_fallback_restores_command_environment_and_lock(probe, official_exit):
    result = invoke(
        probe, "sync", "--frozen", PROBE_MIRROR_EXIT="9", PROBE_OFFICIAL_EXIT=str(official_exit), PROBE_MUTATE_LOCK="1"
    )
    assert result.returncode == official_exit
    calls = records(probe)
    assert len(calls) == 2
    assert calls[0]["args"] == calls[1]["args"] == ["sync", "--frozen"]
    assert calls[1]["index"] is None
    assert calls[1]["lock"].encode() == LOCK
    assert Path(probe[1]["PROBE_LOCK"]).read_bytes() == LOCK


@pytest.mark.parametrize("flags", [["--directory", "work", "--project", "nested"], ["-Cwork", "--project=nested"]])
def test_sync_finds_selected_project_lock(probe, flags):
    lock = probe[0] / "work" / "nested" / "uv.lock"
    lock.parent.mkdir(parents=True)
    lock.write_bytes(LOCK)
    result = invoke(probe, "sync", *flags, "--locked", PROBE_LOCK=str(lock))
    assert result.returncode == 0, result.stderr
    assert "mirrors.aliyun.com/pypi/packages/demo.whl" in records(probe)[0]["lock"]
    assert lock.read_bytes() == Path(probe[1]["PROBE_LOCK"]).read_bytes() == LOCK


def test_run_executes_failed_actual_program_once(probe):
    result = invoke(probe, "run", "--locked", "--with", "demo==1.2.3", "--", "pytest", "-q", PROBE_ACTUAL_EXIT="23")
    assert result.returncode == 23
    calls = records(probe)
    assert [call["stage"] for call in calls] == ["prepare", "actual"]
    assert calls[1]["args"] == ["run", "--with", "demo==1.2.3", "--frozen", "--offline", "--", "pytest", "-q"]
    assert calls[1]["index"] == MIRROR
    assert calls[1]["lock"].encode() == LOCK


@pytest.mark.parametrize("official_exit", [0, 17])
def test_run_retries_only_preparation(probe, official_exit):
    result = invoke(
        probe,
        "run",
        "--isolated",
        "--no-project",
        "--with",
        "demo==1.2.3",
        "--",
        "pytest",
        PROBE_MIRROR_EXIT="9",
        PROBE_OFFICIAL_EXIT=str(official_exit),
    )
    assert result.returncode == official_exit
    calls = records(probe)
    expected = ["prepare", "prepare"] + (["actual"] if official_exit == 0 else [])
    assert [call["stage"] for call in calls] == expected
    assert [call["index"] for call in calls] == [MIRROR] + [None] * (len(calls) - 1)
    assert Path(probe[1]["PROBE_LOCK"]).read_bytes() == LOCK


def test_hosted_run_is_unmodified_passthrough(probe):
    result = invoke(
        probe, "run", "--locked", "--", "pytest", PC_CI_PYTHON_MIRROR="", RUNNER_ENVIRONMENT="github-hosted"
    )
    assert result.returncode == 0
    calls = records(probe)
    assert len(calls) == 1
    assert calls[0]["args"] == ["run", "--locked", "--", "pytest"]
    assert calls[0]["index"] is None
    assert calls[0]["lock"].encode() == LOCK


@pytest.mark.parametrize("flags", [["--index", "https://custom.example/simple/"], ["--no-index"], ["--offline"]])
def test_explicit_source_boundary_passes_through(probe, flags):
    result = invoke(probe, "sync", "--frozen", *flags)
    assert result.returncode == 0
    calls = records(probe)
    assert len(calls) == 1
    assert calls[0]["index"] is None
    assert calls[0]["args"] == ["sync", "--frozen", *flags]
    assert calls[0]["lock"].encode() == LOCK


def test_explicit_index_environment_is_preserved(probe):
    result = invoke(probe, "sync", "--locked", UV_DEFAULT_INDEX="https://custom.example/simple/")
    assert result.returncode == 0
    assert len(records(probe)) == 1
    assert records(probe)[0]["index"] is None
    assert records(probe)[0]["lock"].encode() == LOCK


@pytest.mark.parametrize("operation", ["build", "uv-pip"])
def test_installer_modes_retry_without_changing_project_lock(probe, operation):
    args = ["--wheel"] if operation == "build" else ["install", "--no-config", "demo==1.2.3"]
    result = invoke(probe, operation, *args, PROBE_MIRROR_EXIT="9")
    assert result.returncode == 0
    calls = records(probe)
    expected = ["build" if operation == "build" else "pip", *args]
    assert [call["args"] for call in calls] == [expected, expected]
    assert [call["index"] for call in calls] == [MIRROR, None]
    assert all(call["lock"].encode() == LOCK for call in calls)


def test_hashed_direct_requirement_retains_hash_and_original_fallback(probe):
    requirement = "demo[extra] @ https://files.pythonhosted.org/packages/demo.whl#sha256=abc123"
    result = invoke(probe, "uv-pip", "install", requirement, PROBE_MIRROR_EXIT="9")
    assert result.returncode == 0
    calls = records(probe)
    assert calls[0]["args"][-1] == requirement.replace(
        "https://files.pythonhosted.org/packages/", "https://mirrors.aliyun.com/pypi/packages/"
    )
    assert calls[1]["args"][-1] == requirement


def test_pip_download_retains_interpreter_and_hash_options(probe):
    result = invoke(
        probe,
        "pip",
        "--python",
        str(probe[2]),
        "download",
        "--require-hashes",
        "-r",
        "requirements.txt",
        PROBE_MIRROR_EXIT="9",
    )
    assert result.returncode == 0
    calls = records(probe)
    expected = ["-m", "pip", "download", "--require-hashes", "-r", "requirements.txt"]
    assert [call["args"] for call in calls] == [expected, expected]
    assert [call["index"] for call in calls] == [MIRROR, None]


@pytest.mark.parametrize("dry_run", [True, False])
def test_lock_routes_only_dry_run_resolution(probe, dry_run):
    args = ["--upgrade", "--dry-run"] if dry_run else ["--locked"]
    result = invoke(probe, "lock", *args)
    assert result.returncode == 0
    assert records(probe)[0]["index"] == (MIRROR if dry_run else None)
    assert Path(probe[1]["PROBE_LOCK"]).read_bytes() == LOCK


def test_sigterm_restores_lock_without_retry(probe):
    import signal
    import time

    ready = probe[0] / "ready"
    process = subprocess.Popen(
        [sys.executable, str(SCRIPT), "sync", "--locked"],
        cwd=probe[0],
        env={**probe[1], "PROBE_WAIT": str(ready)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists()
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=5)
        assert process.returncode == 128 + signal.SIGTERM, stdout + stderr
        assert Path(probe[1]["PROBE_LOCK"]).read_bytes() == LOCK
        assert len(records(probe)) == 1
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_hooks_installs_environments_without_running_hooks(probe):
    result = invoke(probe, "hooks", PROBE_MIRROR_EXIT="9")
    assert result.returncode == 0, result.stderr
    calls = records(probe)
    assert [call["args"] for call in calls] == [["run", "--no-sync", "prek", "install-hooks"]] * 2
    assert [call["index"] for call in calls] == [MIRROR, None]
    assert all(call["lock"].encode() == LOCK for call in calls)


def test_project_run_validates_existing_lock_then_consumes_it_frozen(probe):
    result = invoke(probe, "run", "--with", "demo==1.2.3", "--", "pytest", PROBE_IMPLICIT_RELOCK="1")
    assert result.returncode == 0, result.stderr
    calls = records(probe)
    assert "--locked" in calls[0]["args"]
    assert "--frozen" in calls[1]["args"]
    assert calls[1]["lock"].encode() == LOCK
    assert Path(probe[1]["PROBE_LOCK"]).read_bytes() == LOCK


def test_run_accepts_an_immediate_command_separator(probe):
    result = invoke(probe, "run", "--", "python", "-c", "print('actual')")
    assert result.returncode == 0, result.stderr
    calls = records(probe)
    assert [call["stage"] for call in calls] == ["prepare", "actual"]
    assert calls[1]["args"][-4:] == ["--", "python", "-c", "print('actual')"]
