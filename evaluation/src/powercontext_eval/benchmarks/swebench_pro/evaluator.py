"""Adapter for the pinned official SWE-bench Pro evaluator."""

from __future__ import annotations

import json
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from powercontext_eval import docker_pressure
from powercontext_eval.errors import PowerContextEvalError
from powercontext_eval.powercontext_sut import (
    LOOPBACK_NO_PROXY,
    ProxyRelay,
    ProxyRelayConfig,
    SocatProxyRelay,
    default_docker_bridge_gateway,
    direct_egress_environment,
)
from powercontext_eval.process import CommandResult, ProcessRunner

_SAFE_PREFIX = re.compile(r"[A-Za-z0-9._-]+")
_TEST_STATUSES = frozenset({"PASSED", "FAILED", "SKIPPED", "ERROR"})
_LOG_EXCERPT_LIMIT = 4_000


class OfficialResultError(PowerContextEvalError):
    """Official evaluator output is absent or ambiguous."""


@dataclass(frozen=True)
class TestGroupResult:
    """Observed pass/fail detail for one required official test group."""

    __test__ = False

    passed: int
    total: int
    failed: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.passed < 0 or self.total < 0 or self.passed > self.total:
            raise ValueError("Official test counts are invalid")
        if len(self.failed) != self.total - self.passed:
            raise ValueError("Official failed-test names do not match the counts")


@dataclass(frozen=True)
class OfficialEvaluation:
    """Strict official outcome, test details, and raw process evidence."""

    instance_id: str
    resolved: bool
    raw_stdout: str
    raw_stderr: str
    patch_applied: bool | None = None
    fail_to_pass: TestGroupResult = field(default_factory=lambda: TestGroupResult(0, 0, ()))
    pass_to_pass: TestGroupResult = field(default_factory=lambda: TestGroupResult(0, 0, ()))
    log_excerpt: str | None = None


class OfficialEvaluator:
    """Invoke, but never reimplement, the official evaluator."""

    def __init__(
        self,
        runner: ProcessRunner,
        *,
        python_executable: str,
        proxy: ProxyRelayConfig | None = None,
        extra_no_proxy_hosts: tuple[str, ...] = (),
        relay_factory: Callable[[], ProxyRelay] = SocatProxyRelay,
        gateway_resolver: Callable[[ProcessRunner, Path], str] = default_docker_bridge_gateway,
    ) -> None:
        self._runner = runner
        self._python = python_executable
        self._proxy = proxy
        self._extra_no_proxy_hosts = extra_no_proxy_hosts
        self._relay_factory = relay_factory
        self._gateway_resolver = gateway_resolver

    def evaluate(
        self,
        *,
        harness_root: Path,
        raw_sample_path: Path,
        prediction_path: Path,
        output_dir: Path,
        instance_id: str,
        required_fail_to_pass: tuple[str, ...] = (),
        required_pass_to_pass: tuple[str, ...] = (),
        patch_applied: bool | None = None,
    ) -> OfficialEvaluation:
        output_dir.mkdir(parents=True, exist_ok=True)
        argv = (
            self._python,
            "swe_bench_pro_eval.py",
            "--raw_sample_path",
            str(raw_sample_path),
            "--patch_path",
            str(prediction_path),
            "--output_dir",
            str(output_dir),
            "--dockerhub_username",
            "jefzda",
            "--scripts_dir",
            str(harness_root / "run_scripts"),
            "--num_workers",
            "1",
            "--use_local_docker",
            "--docker_platform",
            "linux/amd64",
            "--redo",
        )
        environment = {
            key: os.environ[key]
            for key in (
                "FAKE_EVAL_OUTPUT",
                "FAKE_EVAL_RESULT",
                "FAKE_EVAL_STDERR",
                "FAKE_EVAL_STDOUT",
            )
            if key in os.environ
        }
        environment.update(direct_egress_environment())
        if self._proxy is None:
            result = self._run_harness(argv, harness_root, environment)
        else:
            relay = self._relay_factory()
            try:
                gateway = self._gateway_resolver(self._runner, output_dir.parent)
                relay_url = relay.start(gateway, self._proxy)
                with tempfile.TemporaryDirectory(prefix=".official-docker-", dir=output_dir.parent) as temporary:
                    docker_config = Path(temporary)
                    os.chmod(docker_config, 0o700)
                    _write_docker_proxy_config(docker_config / "config.json", relay_url, self._extra_no_proxy_hosts)
                    environment["DOCKER_CONFIG"] = temporary
                    result = self._run_harness(argv, harness_root, environment)
            finally:
                relay.stop()
        (output_dir / "evaluator.stdout.log").write_text(result.stdout)
        (output_dir / "evaluator.stderr.log").write_text(result.stderr)
        result_path = output_dir / "eval_results.json"
        try:
            payload = json.loads(result_path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise OfficialResultError("Official result is missing or malformed") from error
        if not isinstance(payload, dict) or instance_id not in payload:
            raise OfficialResultError("Official result is missing the requested instance")
        if set(payload) != {instance_id}:
            raise OfficialResultError("Official result must contain the exact instance")
        resolved = payload[instance_id]
        if type(resolved) is not bool:
            raise OfficialResultError("Official result must be a boolean")

        if patch_applied is None and not required_fail_to_pass and not required_pass_to_pass:
            return OfficialEvaluation(instance_id, resolved, result.stdout, result.stderr)
        fail_names = _required_names(required_fail_to_pass, "FAIL_TO_PASS")
        pass_names = _required_names(required_pass_to_pass, "PASS_TO_PASS")
        if set(fail_names) & set(pass_names):
            raise OfficialResultError("Official required test groups must not overlap")
        prefix = _prediction_prefix(prediction_path, instance_id)
        instance_dir = output_dir / instance_id
        test_statuses = _load_test_statuses(instance_dir / f"{prefix}_output.json")
        fail_result = _group_result(fail_names, test_statuses)
        pass_result = _group_result(pass_names, test_statuses)
        computed_resolved = fail_result.passed == fail_result.total and pass_result.passed == pass_result.total
        if resolved is not computed_resolved:
            raise OfficialResultError("Official boolean conflicts with the required test details")
        excerpt = None if resolved else _log_excerpt(instance_dir, prefix)
        return OfficialEvaluation(
            instance_id=instance_id,
            resolved=resolved,
            raw_stdout=result.stdout,
            raw_stderr=result.stderr,
            patch_applied=patch_applied,
            fail_to_pass=fail_result,
            pass_to_pass=pass_result,
            log_excerpt=excerpt,
        )

    def _run_harness(
        self,
        argv: tuple[str, ...],
        harness_root: Path,
        environment: dict[str, str],
    ) -> CommandResult:
        # The official harness uses Docker SDK attached exec streams internally.
        # Share the same process-wide budget as workspace extraction and Codex
        # attaches so a 20-task wave cannot overload the Docker daemon socket.
        with docker_pressure.heavy_operation():
            return self._runner.run(
                argv,
                cwd=harness_root,
                timeout=4_200,
                env=environment,
            )


def _write_docker_proxy_config(path: Path, relay_url: str, extra_no_proxy_hosts: tuple[str, ...] = ()) -> None:
    no_proxy = ",".join((LOOPBACK_NO_PROXY, *extra_no_proxy_hosts))
    payload = {
        "proxies": {
            "default": {
                "httpProxy": relay_url,
                "httpsProxy": relay_url,
                "noProxy": no_proxy,
            }
        }
    }
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, separators=(",", ":"))
        stream.flush()
        os.fsync(stream.fileno())


def _required_names(names: tuple[str, ...], group: str) -> tuple[str, ...]:
    if any(not isinstance(name, str) or not name for name in names):
        raise OfficialResultError(f"{group} contains an invalid test name")
    if len(set(names)) != len(names):
        raise OfficialResultError(f"{group} contains duplicate test names")
    return names


def _prediction_prefix(path: Path, instance_id: str) -> str:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise OfficialResultError("Official prediction is missing or malformed") from error
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise OfficialResultError("Official prediction must contain exactly one object")
    prediction = payload[0]
    if prediction.get("instance_id") != instance_id:
        raise OfficialResultError("Official prediction instance does not match")
    prefix = prediction.get("prefix")
    if not isinstance(prefix, str) or _SAFE_PREFIX.fullmatch(prefix) is None:
        raise OfficialResultError("Official prediction prefix is unsafe")
    return prefix


def _load_test_statuses(path: Path) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as error:
        raise OfficialResultError("Official per-instance output is missing or malformed") from error
    if not isinstance(payload, dict) or set(payload) != {"tests"} or not isinstance(payload["tests"], list):
        raise OfficialResultError("Official per-instance output must contain exactly one tests array")
    statuses: dict[str, str] = {}
    for test in payload["tests"]:
        if not isinstance(test, dict):
            raise OfficialResultError("Official test output contains a non-object")
        name = test.get("name")
        status = test.get("status")
        if not isinstance(name, str) or not name or not isinstance(status, str) or status not in _TEST_STATUSES:
            raise OfficialResultError("Official test output contains an invalid name or status")
        if name in statuses and statuses[name] != status:
            raise OfficialResultError("Official test output contains conflicting statuses for one test name")
        statuses[name] = status
    return statuses


def _group_result(required: tuple[str, ...], statuses: dict[str, str]) -> TestGroupResult:
    failed = tuple(name for name in required if statuses.get(name) != "PASSED")
    return TestGroupResult(passed=len(required) - len(failed), total=len(required), failed=failed)


def _log_excerpt(instance_dir: Path, prefix: str) -> str | None:
    chunks: list[str] = []
    for suffix in ("stdout.log", "stderr.log"):
        try:
            content = (instance_dir / f"{prefix}_{suffix}").read_text(errors="replace")
        except OSError:
            continue
        if content:
            chunks.append(content.replace("\x00", "\N{REPLACEMENT CHARACTER}"))
    combined = "\n".join(chunks).strip()
    return combined[:_LOG_EXCERPT_LIMIT] or None
