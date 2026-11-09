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

"""CI process budgets and migration coverage, independent of a live database."""

from __future__ import annotations

import json
import math
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml
from check_atomic_memory_migration_shards import verify_group
from oceanbase_ci import collect, run_command, wait_ready
from run_atomic_memory_clean_migration import SUITES, assigned_nodes, read_durations


@pytest.fixture
def docker_probe(tmp_path, monkeypatch):
    """Exercise real subprocess deadlines with a controlled Docker command boundary."""
    if sys.platform == "win32":
        pytest.skip("OceanBase CI runs on POSIX runners")
    executable = tmp_path / "docker"
    executable.write_text(
        f"#!{sys.executable}\n"
        "import os, sys, time\n"
        "mode = os.environ.get('CI_PROBE_MODE', 'ready')\n"
        "args = sys.argv[1:]\n"
        "if mode == 'hang': time.sleep(60)\n"
        "if args[0] == 'inspect':\n"
        "    print('false' if mode == 'exited' else 'true')\n"
        "elif args[0] == 'exec':\n"
        "    if mode == 'exited': sys.exit(1)\n"
        "    if 'SHOW PARAMETERS' in args[-1]:\n"
        "        for name, value in [('datafile_size', '4G'), ('datafile_maxsize', '8G'), ('datafile_next', '512M')]:\n"
        "            print('server\\t2882\\tzone\\tscope\\t'+name+'\\ttype\\t'+value)\n"
        "    else: print('1')\n"
    )
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ["PATH"])
    return tmp_path / "diagnostics"


def test_startup_checks_configuration_and_exits_when_the_container_stops(docker_probe, monkeypatch):
    assert wait_ready("test", "test", docker_probe, 5) == 0
    assert (docker_probe / "engine.txt").read_text().strip() == "1"
    monkeypatch.setenv("CI_PROBE_MODE", "exited")
    assert wait_ready("test", "test", docker_probe, 5) == 1


def test_unresponsive_startup_and_diagnostics_respect_overall_deadlines(docker_probe, monkeypatch):
    monkeypatch.setenv("CI_PROBE_MODE", "hang")
    started = time.monotonic()
    assert wait_ready("test", "test", docker_probe, 0.2) == 124
    collect("test", docker_probe / "failure", budget=0.2)
    assert time.monotonic() - started < 3
    snapshot = json.loads((docker_probe / "failure/snapshot.json").read_text())
    assert set(snapshot["exit_codes"].values()) == {124}


def test_one_unwritable_probe_does_not_discard_other_diagnostics(docker_probe):
    (docker_probe / "sessions.txt").mkdir(parents=True)
    collect("test", docker_probe)
    snapshot = json.loads((docker_probe / "snapshot.json").read_text())
    assert snapshot["exit_codes"]["sessions"] != 0
    assert snapshot["exit_codes"]["transactions"] == 0
    assert (docker_probe / "transactions.txt").read_text().strip() == "1"


@pytest.mark.parametrize(
    ("exit_code", "delay", "blocked_path", "expected"),
    [(0, 0, "directory", 0), (7, 0, "execution.json", 7), (0, 60, "before-timeout", 124)],
)
def test_diagnostic_storage_failure_preserves_the_command_result(
    docker_probe, exit_code, delay, blocked_path, expected
):
    timeout = shutil.which("timeout") or shutil.which("gtimeout")
    if timeout is None:
        pytest.skip("GNU timeout is required by the Linux CI command supervisor")
    (docker_probe.parent / "timeout").symlink_to(timeout)
    if blocked_path == "directory":
        docker_probe.write_text("A file prevents creating the diagnostic directory")
    elif blocked_path == "execution.json":
        (docker_probe / blocked_path).mkdir(parents=True)
    else:
        docker_probe.mkdir()
        (docker_probe / blocked_path).write_text("A file prevents creating the snapshot directory")
    assert (
        run_command(
            [sys.executable, "-c", f"import sys,time; time.sleep({delay}); sys.exit({exit_code})"],
            container="test",
            output=docker_probe,
            timeout=4,
        )
        == expected
    )


@pytest.mark.parametrize(("exit_code", "delay", "expected"), [(0, 0, 0), (7, 0, 7), (0, 60, 124)])
def test_command_preserves_failures_and_collects_before_timeout(docker_probe, monkeypatch, exit_code, delay, expected):
    timeout = shutil.which("timeout") or shutil.which("gtimeout")
    if timeout is None:
        pytest.skip("GNU timeout is required by the Linux CI command supervisor")
    (docker_probe.parent / "timeout").symlink_to(timeout)
    # Diagnostic failure must not change the test exit status or prevent termination.
    monkeypatch.setenv("CI_PROBE_MODE", "hang")
    report = docker_probe / "command"
    started = time.monotonic()
    code = run_command(
        [sys.executable, "-c", f"import sys,time; time.sleep({delay}); sys.exit({exit_code})"],
        container="test",
        output=report,
        timeout=2,
    )
    assert code == expected
    assert time.monotonic() - started < 5
    assert json.loads((report / "execution.json").read_text())["exit_code"] == expected
    assert (report / "before-timeout/snapshot.json").exists() is (expected == 124)


def test_cancellation_reaches_the_command_without_waiting_for_its_deadline(docker_probe):
    timeout = shutil.which("timeout") or shutil.which("gtimeout")
    if timeout is None:
        pytest.skip("GNU timeout is required by the Linux CI command supervisor")
    (docker_probe.parent / "timeout").symlink_to(timeout)
    marker = docker_probe.parent / "started"
    process = subprocess.Popen([
        sys.executable,
        str(Path(__file__).parents[1] / "scripts/oceanbase_ci.py"),
        "run",
        "--container",
        "test",
        "--output",
        str(docker_probe),
        "--timeout",
        "60",
        "--",
        sys.executable,
        "-c",
        f"from pathlib import Path; import time; Path({str(marker)!r}).touch(); time.sleep(60)",
    ])
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists()
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=5) == 143
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()


def test_duration_assignment_balances_work_and_includes_unknown_nodes():
    nodes = ["slow-a", "slow-b", "fast-a", "fast-b", "new-case"]
    durations = {"slow-a": 100.0, "slow-b": 90.0, "fast-a": 5.0, "fast-b": 5.0}
    assignments = [assigned_nodes(nodes, index, 2, durations) for index in range(2)]
    assert sorted(node for group in assignments for node in group) == sorted(nodes)
    assert not set(assignments[0]) & set(assignments[1])
    assert ("slow-a" in assignments[0]) != ("slow-b" in assignments[0])
    assert assignments == [assigned_nodes(nodes, index, 2, durations) for index in range(2)]
    # No duration file preserves the seekDB/default round-robin contract.
    assert assigned_nodes(nodes, 1, 3, None) == nodes[1::3]


@pytest.mark.parametrize("shards", [2, 3])
def test_duration_estimates_cannot_exceed_the_per_shard_case_budget(shards):
    nodes = ["slow", *(f"fast-{index}" for index in range(11)), "unmeasured"]
    durations = dict.fromkeys(nodes[:-1], 1.0)
    durations["slow"] = 1000.0
    groups = [assigned_nodes(nodes, index, shards, durations) for index in range(shards)]
    assert max(map(len, groups)) <= math.ceil(len(nodes) / shards)
    assert sorted(node for group in groups for node in group) == sorted(nodes)


@pytest.mark.parametrize("corruption", [None, "missing", "duplicate", "incomplete", "collection"])
def test_balanced_shards_must_cover_every_collected_node_exactly_once(tmp_path, corruption):
    nodes = ["slow", "fast", "new"]
    durations = {"slow": 100.0, "fast": 1.0}
    paths = []
    for index in range(2):
        passed = assigned_nodes(nodes, index, 2, durations)
        report = {
            "backend": "oceanbase",
            "shard_index": index,
            "shard_count": 2,
            "complete": True,
            "collected_nodes": nodes.copy(),
            "passed_nodes": passed,
        }
        if index == 0:
            if corruption == "missing":
                passed.pop()
            elif corruption == "duplicate":
                passed.append(passed[0])
            elif corruption == "incomplete":
                report["complete"] = False
            elif corruption == "collection":
                report["collected_nodes"] = [*nodes, "unexpected"]
        path = tmp_path / f"{index}.json"
        path.write_text(json.dumps(report))
        paths.append(path)
    if corruption:
        with pytest.raises(ValueError):
            verify_group(paths, 2, durations)
    else:
        assert verify_group(paths, 2, durations) == len(nodes)


def test_invalid_duration_baseline_cannot_silently_change_scheduling(tmp_path):
    path = tmp_path / "durations.json"
    path.write_text(json.dumps({"durations_seconds": {"test": -1}}))
    with pytest.raises(ValueError, match="positive"):
        read_durations(path)


def test_all_oceanbase_migration_step_budgets_cover_collected_cases_and_cleanup():
    root = Path(__file__).parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/e2e-harness.yml").read_text())
    budgets = []
    for job in workflow["jobs"].values():
        steps = [step for step in job.get("steps", []) if "run_atomic_memory_clean_migration.py" in step.get("run", "")]
        for step in steps:
            command = next(
                line
                for line in step["run"].replace("\\\n", "").splitlines()
                if "run_atomic_memory_clean_migration.py" in line
            )
            args = shlex.split(command)
            suites = args[args.index("--tests") + 1 :] if "--tests" in args else list(SUITES)
            case_timeout = int(args[args.index("--test-timeout") + 1]) if "--test-timeout" in args else 180
            shard_count = len(job["strategy"]["matrix"]["shard"]) if "--shard" in args else 1
            budgets.append((step, suites, case_timeout, shard_count))
        if steps:
            # Keep bounded pull/startup and ten minutes for other setup/evidence outside test ceilings.
            setup = [
                step
                for step in job["steps"]
                if step.get("name") in {"Pull the OceanBase image", "Start the isolated OceanBase test instance"}
            ]
            assert job["timeout-minutes"] >= sum(step["timeout-minutes"] for step in [*steps, *setup]) + 10
    files = sorted({suite for _, suites, _, _ in budgets for suite in suites})
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *files, "-k", "oceanbase"],
        cwd=root,
        env={**os.environ, "PYTEST_ADDOPTS": ""},
        capture_output=True,
        text=True,
        timeout=180,
        check=True,
    )
    nodes = [line for line in result.stdout.splitlines() if line.startswith(tuple(f"{suite}::" for suite in files))]
    for step, suites, case_timeout, shard_count in budgets:
        selected = [node for node in nodes if node.split("::")[0] in suites]
        assert selected, step["name"]
        largest_shard = math.ceil(len(selected) / shard_count)
        # Collection, termination grace, and every slow passing case must fit regardless of timing estimates.
        assert step["timeout-minutes"] * 60 >= largest_shard * (case_timeout + 10) + 190, step["name"]


@pytest.mark.parametrize("caller", ["recall-effort", "atomic-memory-clean-oceanbase-shards", "upgrade"])
@pytest.mark.parametrize("cache_state", ["cold", "warm", "pull_failure"])
def test_cold_image_download_does_not_consume_the_container_start_deadline(tmp_path, caller, cache_state):
    if sys.platform == "win32":
        pytest.skip("OceanBase CI runs on POSIX runners")
    root = Path(__file__).parents[1]
    if caller == "upgrade":
        action = yaml.safe_load((root / ".github/actions/setup-upgrade-oceanbase/action.yml").read_text())
        steps = action["runs"]["steps"]
    else:
        workflow = yaml.safe_load((root / ".github/workflows/e2e-harness.yml").read_text())
        steps = workflow["jobs"][caller]["steps"]
    startup = "\n".join(
        step["run"]
        for step in steps
        if step.get("name")
        in {"Pull the OceanBase image", "Start the isolated OceanBase test instance", "Start and verify OceanBase"}
    )
    binaries = tmp_path / "bin"
    binaries.mkdir()
    # Model a cold image requiring five minutes, without spending that time in the test.
    (binaries / "timeout").write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "args = sys.argv[1:]\n"
        "while args[0].startswith('--'): args.pop(0)\n"
        "os.environ['PC_DOCKER_DEADLINE'] = args.pop(0).removesuffix('s')\n"
        "os.execvp(args[0], args)\n"
    )
    (binaries / "docker").write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "root = Path(os.environ['PC_DOCKER_STATE'])\n"
        "image = root / 'image'\n"
        "if args[:2] == ['image', 'inspect']: sys.exit(0 if image.exists() else 1)\n"
        "if args[0] == 'pull':\n"
        "    (root / 'pull').touch()\n"
        "    if os.environ['PC_CACHE_STATE'] == 'pull_failure': sys.exit(124)\n"
        "    if int(os.environ['PC_DOCKER_DEADLINE']) < 300: sys.exit(124)\n"
        "    image.touch()\n"
        "if args[0] == 'run':\n"
        "    if not image.exists():\n"
        "        if '--pull' in args and args[args.index('--pull') + 1] == 'never': sys.exit(125)\n"
        "        if int(os.environ['PC_DOCKER_DEADLINE']) < 300: sys.exit(124)\n"
        "        image.touch()\n"
        "    (root / 'container').touch()\n"
    )
    (binaries / "python").write_text("#!/bin/sh\nexit 0\n")
    for executable in binaries.iterdir():
        executable.chmod(0o755)
    if cache_state == "warm":
        (tmp_path / "image").touch()
    env = {
        **os.environ,
        "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
        "PC_DOCKER_STATE": str(tmp_path),
        "PC_CACHE_STATE": cache_state,
        "OCEANBASE_IMAGE": "fixture-image",
        "RUNNER_TEMP": str(tmp_path),
        "MIGRATION_REPORTS": str(tmp_path / "reports"),
    }
    bash = shutil.which("bash")
    assert bash is not None
    result = subprocess.run(
        [bash, "-e", "-o", "pipefail", "-c", startup],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == (124 if cache_state == "pull_failure" else 0), result.stderr
    assert (tmp_path / "container").exists() is (cache_state != "pull_failure")
    assert (tmp_path / "pull").exists() is (cache_state != "warm")


def test_recall_job_preserves_startup_and_cleanup_beyond_command_deadlines():
    root = Path(__file__).parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/e2e-harness.yml").read_text())
    job = workflow["jobs"]["recall-effort"]
    commands = [step["run"] for step in job["steps"] if "scripts/oceanbase_ci.py run" in step.get("run", "")]
    deadlines = [int(value) for command in commands for value in re.findall(r"--timeout (\d+)", command)]
    assert len(deadlines) == len(commands)
    test_seconds = sum(deadline + 10 for deadline in deadlines)
    setup_minutes = sum(
        step["timeout-minutes"]
        for step in job["steps"]
        if step["name"] in {"Pull the OceanBase image", "Start the isolated OceanBase test instance"}
    )
    assert job["timeout-minutes"] >= test_seconds / 60 + setup_minutes + 10


@pytest.mark.parametrize("backend", ["oceanbase", "sqlite", "seekdb"])
def test_candidate_replay_deadline_leaves_time_for_diagnostics_and_cleanup(backend):
    root = Path(__file__).parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/candidate-schema-replay.yml").read_text())
    job = workflow["jobs"]["replay"]
    step = next(step for step in job["steps"] if step["name"].startswith("Replay and recover"))
    command = step["run"]
    process_deadlines = [int(value) for value in re.findall(r"--timeout (\d+)", command) if value]
    process_deadlines += [int(value) for value in re.findall(r"--kill-after=10s (\d+)s", command)]
    assert len(process_deadlines) == 2
    assert step["timeout-minutes"] * 60 >= max(process_deadlines) + 30
    budgets = re.search(r"&& (\d+) \|\| (\d+)", job["timeout-minutes"])
    assert budgets is not None
    job_minutes = int(budgets[1 if backend == "oceanbase" else 2])
    # OceanBase startup allows 25m; namespace and diagnostics get 2m each,
    # leaving another 5m for Python setup and artifact/cache publication.
    reserve = 34 if backend == "oceanbase" else 10
    assert job_minutes >= step["timeout-minutes"] + reserve


def test_native_acceptance_reserves_startup_and_evidence_outside_test_deadline():
    root = Path(__file__).parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/e2e-harness.yml").read_text())
    job = workflow["jobs"]["acceptance"]

    def native_minutes(value):
        if isinstance(value, int):
            return value
        match = re.search(r"&& (\d+) \|\|", value)
        assert match is not None
        return int(match[1])

    names = {
        "Start and configure the shared OceanBase instance",
        "Verify OceanBase Atomic Memory, HTTP and archive regressions",
        "Run deterministic scenarios",
    }
    workload = [step for step in job["steps"] if step["name"] in names]
    assert len(workload) == len(names)
    assert (
        native_minutes(job["timeout-minutes"]) >= sum(native_minutes(step["timeout-minutes"]) for step in workload) + 10
    )
