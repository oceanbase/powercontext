"""Measure real-Codex coding-task success with and without approved Experience recall."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Never

from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    ApproveArtifactCandidateRequest,
    BuiltinConfig,
    CaptureSource,
    PrepareContextRequest,
    PreparedContext,
    ProposeExperienceRequest,
    open_builtin_runtime,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_ROOT = Path(tempfile.gettempdir()) / "powercontext-experience-recall-evaluation"


class RecallEvaluationError(RuntimeError):
    """Report a broken experiment setup rather than a scored task failure."""


@dataclass(frozen=True, slots=True)
class EvaluationTask:
    name: str
    query: str
    prompt: str
    experience: ExperienceContent
    files: Mapping[str, str]
    expected_json: Mapping[str, Mapping[str, object]]
    expected_text: Mapping[str, str]
    expected_contains: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ArmResult:
    task: str
    arm: str
    passed: bool
    failures: tuple[str, ...]
    prepared_context_bytes: int
    returncode: int | None
    timed_out: bool
    duration_seconds: float
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    changed_files: tuple[str, ...]


def _tasks() -> tuple[EvaluationTask, ...]:
    return (
        EvaluationTask(
            name="strict-config-marker",
            query="repair strict configuration marker regression",
            prompt="Repair the strict configuration marker regression. Make the required repository changes.",
            experience=ExperienceContent(
                situation="A strict configuration marker regression returned after a permissive fixture was copied.",
                action=('Set config.json to {"mode":"strict","marker":"POWERCONTEXT_STRICT_V2"}.'),
                outcome="The strict configuration marker validation passed in the prior repository.",
                lesson="Preserve strict mode and the exact POWERCONTEXT_STRICT_V2 marker for this regression.",
            ),
            files={"config.json": '{"mode":"permissive","marker":""}\n'},
            expected_json={
                "config.json": {"mode": "strict", "marker": "POWERCONTEXT_STRICT_V2"},
            },
            expected_text={},
            expected_contains={},
        ),
        EvaluationTask(
            name="retry-budget-schema",
            query="fix retry budget compatibility regression",
            prompt="Fix the retry budget compatibility regression in retry.json.",
            experience=ExperienceContent(
                situation="A retry budget compatibility regression used the retired seconds-based schema.",
                action="Replace retry_budget and retry_unit with retry_budget_ms set to 3000.",
                outcome="The retry budget compatibility check accepted the migrated schema.",
                lesson="For this regression, remove both legacy keys and preserve the 3000 millisecond budget.",
            ),
            files={"retry.json": '{"retry_budget":3,"retry_unit":"seconds"}\n'},
            expected_json={"retry.json": {"retry_budget_ms": 3000}},
            expected_text={},
            expected_contains={},
        ),
        EvaluationTask(
            name="generated-client-flag",
            query="correct generated client feature flag regression",
            prompt="Correct the generated client feature flag regression.",
            experience=ExperienceContent(
                situation="A generated client feature flag regression restored the legacy channel.",
                action=(
                    'Set FEATURE_FLAG to "stable-v3" in client.py and create generated-client-v3.txt containing '
                    "GENERATED_CLIENT_V3 followed by a newline."
                ),
                outcome="The generated client feature flag check passed with both synchronized markers.",
                lesson="Keep the stable-v3 flag and GENERATED_CLIENT_V3 marker together when repairing this regression.",
            ),
            files={"client.py": 'FEATURE_FLAG = "legacy"\n'},
            expected_json={},
            expected_text={"generated-client-v3.txt": "GENERATED_CLIENT_V3\n"},
            expected_contains={"client.py": 'FEATURE_FLAG = "stable-v3"'},
        ),
    )


async def _approved_contexts(database: Path) -> dict[str, PreparedContext]:
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"))
    prepared: dict[str, PreparedContext] = {}
    async with open_builtin_runtime(config) as runtime:
        for task in _tasks():
            scope_id = f"experience-recall-eval:{task.name}"
            source = await runtime.sources.for_scope(scope_id).capture(
                CaptureSource(
                    source_id="prior-task-outcome",
                    content=task.experience.model_dump_json(),
                    metadata={"kind": "task-outcome", "validation_status": "passed"},
                )
            )
            candidate = await runtime.experience.for_scope(scope_id).propose(
                ProposeExperienceRequest(proposal=task.experience, sources=(source.source_ref,))
            )
            pending = await runtime.context.for_scope(scope_id).prepare(PrepareContextRequest(query=task.query))
            if pending.status != "empty":
                _fail(f"pending Experience entered PreparedContext for {task.name}")
            approved = await runtime.review.for_scope(scope_id).approve(
                ApproveArtifactCandidateRequest(
                    candidate_id=candidate.candidate_id,
                    expected_version=candidate.version,
                )
            )
            if approved.result_artifact is None:
                _fail(f"Experience approval wrote no Artifact for {task.name}")
            value = await runtime.context.for_scope(scope_id).prepare(PrepareContextRequest(query=task.query))
            if value.status != "ready" or value.content is None or '"kind":"experience"' not in value.content:
                _fail(f"approved Experience was not recalled for {task.name}")
            prepared[task.name] = value
    return prepared


def _prepare_repository(root: Path, task: EvaluationTask, git: str) -> None:
    root.mkdir(parents=True)
    (root / "AGENTS.md").write_text(
        "# Evaluation repository\n\nFollow the user task. Do not modify AGENTS.md. Make the best concrete fix.\n",
        encoding="utf-8",
    )
    for relative, content in task.files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    _run([git, "init", "-q"], cwd=root)
    _run([git, "config", "user.email", "evaluation@powercontext.invalid"], cwd=root)
    _run([git, "config", "user.name", "PowerContext Evaluation"], cwd=root)
    _run([git, "add", "AGENTS.md", *task.files], cwd=root)
    _run([git, "commit", "-qm", "baseline"], cwd=root)


def _prompt(task: EvaluationTask, prepared: PreparedContext | None) -> str:
    context = "" if prepared is None else f"\n\nHost-supplied PowerContext context:\n{prepared.content}"
    return (
        f"{task.prompt}{context}\n\n"
        "Work directly in the repository. Do not ask questions. Do not merely describe a fix. "
        "Finish after making the best concrete changes you can infer."
    )


def _run_arm(
    *,
    task: EvaluationTask,
    arm: str,
    repository: Path,
    prepared: PreparedContext | None,
    codex: str,
    git: str,
    environment: Mapping[str, str],
    timeout: int,
    session_directory: Path,
) -> ArmResult:
    session_directory.mkdir(parents=True, exist_ok=True)
    stdout_path = session_directory / f"{task.name}-{arm}.jsonl"
    stderr_path = session_directory / f"{task.name}-{arm}.stderr"
    command = [
        codex,
        "-a",
        "never",
        "--disable",
        "memories",
        "--disable",
        "plugins",
        "--disable",
        "remote_plugin",
        "--disable",
        "shell_snapshot",
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--json",
        "-s",
        "workspace-write",
        "-C",
        str(repository),
        _prompt(task, prepared),
    ]
    started = time.monotonic()
    timed_out = False
    returncode: int | None = None
    stdout = ""
    stderr = ""
    try:
        completed = subprocess.run(
            command,
            cwd=repository,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        returncode = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as error:
        timed_out = True
        stdout = _process_output(error.stdout)
        stderr = _process_output(error.stderr)
    duration = time.monotonic() - started
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")

    failures = list(_validate(task, repository))
    if returncode not in {0, None}:
        failures.append(f"codex exited with {returncode}")
    if timed_out:
        failures.append("codex timed out")
    usage = _usage(stdout)
    changed = tuple(
        line[3:]
        for line in _run([git, "status", "--short", "--untracked-files=all"], cwd=repository).stdout.splitlines()
    )
    return ArmResult(
        task=task.name,
        arm=arm,
        passed=not failures,
        failures=tuple(failures),
        prepared_context_bytes=0 if prepared is None else prepared.content_bytes,
        returncode=returncode,
        timed_out=timed_out,
        duration_seconds=round(duration, 3),
        input_tokens=usage.get("input_tokens"),
        cached_input_tokens=usage.get("cached_input_tokens"),
        output_tokens=usage.get("output_tokens"),
        changed_files=changed,
    )


def _validate(task: EvaluationTask, repository: Path) -> tuple[str, ...]:
    failures: list[str] = []
    failures.extend(_validate_json(task.expected_json, repository))
    failures.extend(_validate_text(task.expected_text, repository))
    failures.extend(_validate_contains(task.expected_contains, repository))
    return tuple(failures)


def _validate_json(expectations: Mapping[str, Mapping[str, object]], repository: Path) -> tuple[str, ...]:
    failures: list[str] = []
    for relative, expected in expectations.items():
        path = repository / relative
        try:
            actual = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            failures.append(f"{relative}: unreadable JSON ({type(error).__name__})")
        else:
            if actual != expected:
                failures.append(f"{relative}: expected {expected!r}, got {actual!r}")
    return tuple(failures)


def _validate_text(expectations: Mapping[str, str], repository: Path) -> tuple[str, ...]:
    failures: list[str] = []
    for relative, expected in expectations.items():
        path = repository / relative
        try:
            actual = path.read_text(encoding="utf-8")
        except OSError as error:
            failures.append(f"{relative}: unreadable ({type(error).__name__})")
        else:
            if actual != expected:
                failures.append(f"{relative}: exact content mismatch")
    return tuple(failures)


def _validate_contains(expectations: Mapping[str, str], repository: Path) -> tuple[str, ...]:
    failures: list[str] = []
    for relative, expected in expectations.items():
        path = repository / relative
        try:
            actual = path.read_text(encoding="utf-8")
        except OSError as error:
            failures.append(f"{relative}: unreadable ({type(error).__name__})")
        else:
            if expected not in actual:
                failures.append(f"{relative}: missing {expected!r}")
    return tuple(failures)


def _usage(output: str) -> dict[str, int | None]:
    latest: dict[str, int | None] = {}
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        usage = event.get("usage")
        if not isinstance(usage, dict):
            continue
        latest = {
            key: int(value) if isinstance(value, int) else None
            for key, value in usage.items()
            if key in {"input_tokens", "cached_input_tokens", "output_tokens"}
        }
    return latest


def _summary(results: Sequence[ArmResult]) -> dict[str, object]:
    by_arm = {arm: tuple(result for result in results if result.arm == arm) for arm in ("control", "treatment")}
    counts = {arm: sum(result.passed for result in values) for arm, values in by_arm.items()}
    total = len(_tasks())
    rates = {arm: counts[arm] / total for arm in counts}
    return {
        "schema": "powercontext.experience-recall-evaluation.v1",
        "task_count": total,
        "control": {"passed": counts["control"], "success_rate": rates["control"]},
        "treatment": {"passed": counts["treatment"], "success_rate": rates["treatment"]},
        "absolute_success_rate_delta": rates["treatment"] - rates["control"],
        "prepared_context_bytes": sum(result.prepared_context_bytes for result in by_arm["treatment"]),
        "usage": {
            arm: {
                key: sum(value for result in values if (value := getattr(result, key)) is not None)
                for key in ("input_tokens", "cached_input_tokens", "output_tokens")
            }
            for arm, values in by_arm.items()
        },
        "results": [asdict(result) for result in results],
    }


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _arguments(argv)
    codex = _required_executable("codex")
    git = _required_executable("git")
    real_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).resolve()
    auth_file = real_home / "auth.json"
    if not auth_file.is_file():
        _fail(f"real Codex auth is unavailable at {auth_file}")
    user_state_before = _user_state(real_home)

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output = arguments.output_root.resolve() / run_id
    output.mkdir(parents=True)
    results: list[ArmResult] = []
    isolated_home_path: Path | None = None
    isolated_work_path: Path | None = None
    try:
        runtime_database = output / "runtime.db"
        prepared = asyncio.run(_approved_contexts(runtime_database))
        _remove_runtime_database(runtime_database)
        with (
            tempfile.TemporaryDirectory(prefix="powercontext-experience-recall-codex-") as isolated_home_value,
            tempfile.TemporaryDirectory(prefix="powercontext-experience-recall-work-") as isolated_work_value,
        ):
            isolated_home_path = Path(isolated_home_value)
            isolated_work_path = Path(isolated_work_value)
            isolated_auth = isolated_home_path / "auth.json"
            shutil.copyfile(auth_file, isolated_auth)
            isolated_auth.chmod(0o600)
            environment = {**os.environ, "CODEX_HOME": str(isolated_home_path), "NO_COLOR": "1"}
            for task in _tasks():
                for arm in ("control", "treatment"):
                    repository = isolated_work_path / task.name / arm
                    _prepare_repository(repository, task, git)
                    result = _run_arm(
                        task=task,
                        arm=arm,
                        repository=repository,
                        prepared=None if arm == "control" else prepared[task.name],
                        codex=codex,
                        git=git,
                        environment=environment,
                        timeout=arguments.timeout,
                        session_directory=output / "sessions",
                    )
                    results.append(result)
                    shutil.rmtree(repository)
                    print(
                        f"[{task.name}/{arm}] {'PASS' if result.passed else 'FAIL'} "
                        f"context_bytes={result.prepared_context_bytes} failures={list(result.failures)}",
                        flush=True,
                    )
        report = _summary(results)
        report["environment"] = {
            "codex_version": _run([codex, "--version"], cwd=PROJECT_ROOT).stdout.strip(),
            "codex_home": "isolated temporary directory with a mode-0600 auth copy only",
            "arms": "paired control then treatment with the same task and Codex settings",
            "delivery": "Runtime-produced content is appended to the initial task as host-supplied context",
            "scoring": "independent exact file and JSON checks outside the task repository",
            "worktree_isolation": (
                "dedicated system temporary root; runtime database and each arm repository are removed before Codex "
                "can inspect another arm"
            ),
        }
        report_path = output / "report.json"
        report_path.write_text(f"{json.dumps(report, ensure_ascii=False, indent=2)}\n", encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        delta = report["absolute_success_rate_delta"]
        if not isinstance(delta, int | float) or delta <= 0:
            _fail("treatment did not improve coding-task success rate")
        return 0
    finally:
        isolated_removed = isolated_home_path is None or not isolated_home_path.exists()
        work_removed = isolated_work_path is None or not isolated_work_path.exists()
        user_state_unchanged = user_state_before == _user_state(real_home)
        if arguments.cleanup:
            shutil.rmtree(output, ignore_errors=False)
        cleanup = {
            "isolated_codex_home_removed": isolated_removed,
            "isolated_work_root_removed": work_removed,
            "user_codex_state_unchanged": user_state_unchanged,
            "output_removed": arguments.cleanup and not output.exists(),
        }
        print(f"Cleanup audit: {json.dumps(cleanup, ensure_ascii=False)}", flush=True)
        if (
            not isolated_removed
            or not work_removed
            or not user_state_unchanged
            or (arguments.cleanup and output.exists())
        ):
            _fail(f"cleanup audit failed: {cleanup}")


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=600, help="Per-Codex-session timeout in seconds.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--cleanup", action="store_true", help="Remove isolated repos, database, and report after audit."
    )
    return parser.parse_args(argv)


def _required_executable(name: str) -> str:
    value = shutil.which(name)
    if value is None:
        _fail(f"required executable is unavailable: {name}")
    return value


def _run(command: Sequence[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        _fail(f"command failed ({' '.join(command)}): {completed.stderr.strip()}")
    return completed


def _process_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value


def _user_state(root: Path) -> dict[str, str | None]:
    return {
        name: hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        for name in ("auth.json", "config.toml")
        if (path := root / name)
    }


def _remove_runtime_database(database: Path) -> None:
    for path in (database, database.with_name(f"{database.name}-shm"), database.with_name(f"{database.name}-wal")):
        path.unlink(missing_ok=True)


def _fail(message: str) -> Never:
    raise RecallEvaluationError(message)


if __name__ == "__main__":
    raise SystemExit(main())
