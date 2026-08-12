from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_REPOSITORY_ROOT = Path(__file__).parents[3]
_RUN_SCRIPT = _REPOSITORY_ROOT / "e2e" / "bub" / "run.sh"


@pytest.mark.parametrize(
    ("failed_command", "exit_code"),
    [
        ("build", 31),
        ("up", 32),
        ("run", 33),
    ],
)
def test_failure_cleans_compose_resources_and_preserves_exit_code(
    tmp_path: Path,
    failed_command: str,
    exit_code: int,
) -> None:
    result, state = _run_with_fake_docker(
        tmp_path,
        failed_command=failed_command,
        exit_code=exit_code,
    )

    assert result.returncode == exit_code
    assert list(state.iterdir()) == []


def test_cleanup_failure_does_not_mask_harness_exit_code(tmp_path: Path) -> None:
    result, state = _run_with_fake_docker(
        tmp_path,
        failed_command="run",
        exit_code=33,
        cleanup_exit_code=71,
    )

    assert result.returncode == 33
    assert list(state.iterdir()) == []
    assert "Compose cleanup failed with exit code 71" in result.stderr


def test_success_uses_the_same_cleanup_path(tmp_path: Path) -> None:
    result, state = _run_with_fake_docker(
        tmp_path,
        failed_command="none",
        exit_code=0,
    )

    assert result.returncode == 0
    assert list(state.iterdir()) == []


def test_cleanup_failure_makes_a_successful_run_fail(tmp_path: Path) -> None:
    result, state = _run_with_fake_docker(
        tmp_path,
        failed_command="none",
        exit_code=0,
        cleanup_exit_code=71,
    )

    assert result.returncode == 71
    assert list(state.iterdir()) == []
    assert "Compose cleanup failed with exit code 71" in result.stderr


def test_startup_failure_reports_compose_diagnostics(tmp_path: Path) -> None:
    result, _ = _run_with_fake_docker(
        tmp_path,
        failed_command="up",
        exit_code=32,
    )

    assert result.returncode == 32
    assert "Compose startup failed; service state:" in result.stderr
    assert "fake compose state" in result.stderr
    assert "fake compose logs" in result.stderr


def test_oceanbase_startup_retries_once(tmp_path: Path) -> None:
    result, state = _run_with_fake_docker(
        tmp_path,
        failed_command="up",
        exit_code=32,
        database="oceanbase",
        startup_failures=1,
    )

    assert result.returncode == 0
    assert list(state.iterdir()) == []
    assert result.stderr.count("Compose startup failed; service state:") == 1
    assert "Retrying OceanBase Compose startup (attempt 2 of 2)." in result.stderr


def test_persistent_oceanbase_startup_failure_preserves_exit_code(tmp_path: Path) -> None:
    result, state = _run_with_fake_docker(
        tmp_path,
        failed_command="up",
        exit_code=32,
        database="oceanbase",
        startup_failures=2,
    )

    assert result.returncode == 32
    assert list(state.iterdir()) == []
    assert result.stderr.count("Compose startup failed; service state:") == 2
    assert result.stderr.count("Retrying OceanBase Compose startup") == 1


def _run_with_fake_docker(
    tmp_path: Path,
    *,
    failed_command: str,
    exit_code: int,
    cleanup_exit_code: int = 0,
    database: str = "sqlite",
    startup_failures: int = 1000,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    docker = fake_bin / "docker"
    docker.write_text(
        """#!/bin/sh
set -eu

command=
for argument in "$@"; do
    case "$argument" in
        build | config | down | logs | ps | run | up)
            command=$argument
            break
            ;;
    esac
done

case "$command" in
    build | run)
        touch "$FAKE_DOCKER_STATE/container" "$FAKE_DOCKER_STATE/network" "$FAKE_DOCKER_STATE/volume"
        if [ "$FAKE_DOCKER_FAIL" = "$command" ]; then
            exit "$FAKE_DOCKER_EXIT"
        fi
        ;;
    up)
        touch "$FAKE_DOCKER_STATE/container" "$FAKE_DOCKER_STATE/network" "$FAKE_DOCKER_STATE/volume"
        count=0
        if [ -f "$FAKE_DOCKER_UP_COUNT" ]; then
            count=$(cat "$FAKE_DOCKER_UP_COUNT")
        fi
        count=$((count + 1))
        echo "$count" > "$FAKE_DOCKER_UP_COUNT"
        if [ "$FAKE_DOCKER_FAIL" = up ] && [ "$count" -le "$FAKE_DOCKER_STARTUP_FAILURES" ]; then
            exit "$FAKE_DOCKER_EXIT"
        fi
        ;;
    ps)
        echo "fake compose state"
        ;;
    logs)
        echo "fake compose logs"
        ;;
    down)
        rm -f "$FAKE_DOCKER_STATE"/*
        exit "$FAKE_DOCKER_CLEANUP_EXIT"
        ;;
esac
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    environment = os.environ.copy()
    environment.update({
        "FAKE_DOCKER_CLEANUP_EXIT": str(cleanup_exit_code),
        "FAKE_DOCKER_EXIT": str(exit_code),
        "FAKE_DOCKER_FAIL": failed_command,
        "FAKE_DOCKER_STATE": str(state),
        "FAKE_DOCKER_STARTUP_FAILURES": str(startup_failures),
        "FAKE_DOCKER_UP_COUNT": str(tmp_path / "up-count"),
        "GITHUB_SHA": "test-revision",
        "PATH": f"{fake_bin}{os.pathsep}{environment['PATH']}",
        "POWERCONTEXT_E2E_DATABASE": database,
        "POWERCONTEXT_E2E_OUTPUT": str(tmp_path / "evidence"),
    })
    result = subprocess.run(  # noqa: S603 - executes the repository script with an isolated fake Docker binary.
        ["/bin/sh", str(_RUN_SCRIPT), "acceptance"],
        cwd=_REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    return result, state
