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

from __future__ import annotations

import json
import os
import stat
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import BinaryIO, cast

import pytest

import powercontext_eval.powercontext_sut as sut_module
from powercontext_eval.benchmarks.base import GoldCheckFailed, GoldResult, run_after_gold
from powercontext_eval.benchmarks.swebench_pro.adapter import (
    DATASET_REVISION,
    HARNESS_COMMIT,
    DatasetSchemaError,
    SweBenchProInstance,
)
from powercontext_eval.benchmarks.swebench_pro.evaluator import (
    OfficialEvaluator,
    OfficialResultError,
    TestGroupResult,
)
from powercontext_eval.benchmarks.swebench_pro.prediction import encode_predictions
from powercontext_eval.powercontext_sut import LOOPBACK_NO_PROXY, ProxyRelayConfig, direct_egress_environment
from powercontext_eval.process import CommandResult, ProcessRunner

INSTANCE_ID = "instance_flipt-io__flipt-518ec324b66a07fdd95464a5e9ca5fe7681ad8f9"
DATASET_FIELDS = {
    "repo",
    "instance_id",
    "base_commit",
    "patch",
    "test_patch",
    "problem_statement",
    "requirements",
    "interface",
    "repo_language",
    "fail_to_pass",
    "pass_to_pass",
    "issue_specificity",
    "issue_categories",
    "before_repo_set_cmd",
    "selected_test_files_to_run",
    "dockerhub_tag",
}
UPSTREAM_PROXY_URL = "http://127.0.0.1:18080"
RELAY_URL = "http://172.17.0.1:45678"


def raw_instance() -> dict[str, object]:
    return {
        "repo": "flipt-io/flipt",
        "instance_id": INSTANCE_ID,
        "base_commit": "0018c5df774444117b107dfe3fe503d4c7126d73",
        "patch": "diff --git a/gold b/gold\n",
        "test_patch": "diff --git a/hidden b/hidden\n",
        "problem_statement": "parse CORS origins",
        "requirements": "split whitespace-separated values",
        "interface": "No new interfaces.",
        "repo_language": "go",
        "fail_to_pass": '["TestLoad"]',
        "pass_to_pass": "[]",
        "issue_specificity": '["regression_bug"]',
        "issue_categories": '["back_end_knowledge"]',
        "before_repo_set_cmd": "git reset --hard",
        "selected_test_files_to_run": '["TestLoad"]',
        "dockerhub_tag": "flipt-io.flipt-flipt-io__flipt-518ec324b66a07fdd95464a5e9ca5fe7681ad8f9",
    }


def test_pins_public_harness_and_dataset() -> None:
    assert HARNESS_COMMIT == "ca10a60a5fcae51e6948ffe1485d4153d421e6c5"
    assert DATASET_REVISION == "7ab5114912baf22bb098818e604c02fe7ad2c11f"


def test_instance_requires_exact_pinned_dataset_schema_and_manifest_digest() -> None:
    assert set(raw_instance()) == DATASET_FIELDS
    instance = SweBenchProInstance.from_raw(raw_instance(), docker_manifest_digest="sha256:" + "a" * 64)
    assert instance.docker_manifest_digest == "sha256:" + "a" * 64

    missing = raw_instance()
    missing.pop("requirements")
    with pytest.raises(DatasetSchemaError, match="missing.*requirements"):
        SweBenchProInstance.from_raw(missing, docker_manifest_digest="sha256:" + "a" * 64)

    extra = raw_instance()
    extra["unknown"] = "value"
    with pytest.raises(DatasetSchemaError, match="unexpected.*unknown"):
        SweBenchProInstance.from_raw(extra, docker_manifest_digest="sha256:" + "a" * 64)

    with pytest.raises(DatasetSchemaError, match="manifest digest"):
        SweBenchProInstance.from_raw(raw_instance(), docker_manifest_digest="")


def test_prompt_exposes_only_public_task_fields() -> None:
    instance = SweBenchProInstance.from_raw(raw_instance(), docker_manifest_digest="sha256:" + "a" * 64)
    prompt = instance.codex_prompt()
    assert instance.problem_statement in prompt
    assert instance.requirements in prompt
    assert instance.interface in prompt
    assert instance.patch not in prompt
    assert instance.test_patch not in prompt
    assert all(test_name not in prompt for test_name in instance.fail_to_pass)
    assert instance.selected_test_files_to_run not in prompt


def test_prediction_is_official_json_array_and_preserves_patch_bytes() -> None:
    patch = "diff --git a/a b/a\n--- a/a\n+++ b/a\n@@ -1 +1 @@\n-old\r\n+new\n"
    encoded = encode_predictions(INSTANCE_ID, patch, "codex-0.145.0")
    assert json.loads(encoded) == [{"instance_id": INSTANCE_ID, "patch": patch, "prefix": "codex-0.145.0"}]
    assert encoded.encode().decode() == encoded


@pytest.mark.parametrize("marker", ["GIT binary patch", "Binary files a/x and b/x differ"])
def test_prediction_preserves_binary_patch(marker: str) -> None:
    patch = f"diff --git a/x b/x\n{marker}\n"
    encoded = encode_predictions(INSTANCE_ID, patch, "codex-0.145.0")
    assert json.loads(encoded) == [{"instance_id": INSTANCE_ID, "patch": patch, "prefix": "codex-0.145.0"}]


def test_gold_failure_prevents_arm_factory_from_being_called() -> None:
    called = False

    def arms() -> None:
        nonlocal called
        called = True

    with pytest.raises(GoldCheckFailed):
        run_after_gold(GoldResult(instance_id=INSTANCE_ID, resolved=False), arms)
    assert not called


def test_official_evaluator_uses_exact_cli_and_retains_raw_output(tmp_path: Path) -> None:
    harness = tmp_path / "harness"
    harness.mkdir()
    fake = Path(__file__).parent / "fixtures" / "fake_evaluator.py"
    (harness / "swe_bench_pro_eval.py").write_bytes(fake.read_bytes())
    (harness / "run_scripts").mkdir()
    raw_path = tmp_path / "instance.jsonl"
    raw_path.write_text(json.dumps(raw_instance(), separators=(",", ":")) + "\n")
    prediction_path = tmp_path / "predictions.json"
    prediction_path.write_text(encode_predictions(INSTANCE_ID, "diff --git a/a b/a\n", "codex-0.145.0"))
    output_dir = tmp_path / "output"

    result = OfficialEvaluator(ProcessRunner(), python_executable=sys.executable).evaluate(
        harness_root=harness,
        raw_sample_path=raw_path,
        prediction_path=prediction_path,
        output_dir=output_dir,
        instance_id=INSTANCE_ID,
    )

    assert result.resolved is True
    assert "FAKE OFFICIAL EVALUATOR" in result.raw_stdout
    invocation = json.loads((output_dir / "invocation.json").read_text())
    assert invocation == {
        "raw_sample_path": str(raw_path),
        "patch_path": str(prediction_path),
        "output_dir": str(output_dir),
        "dockerhub_username": "jefzda",
        "scripts_dir": str(harness / "run_scripts"),
        "num_workers": "1",
        "use_local_docker": True,
        "docker_platform": "linux/amd64",
        "redo": True,
        "block_network": False,
    }


def test_official_evaluator_and_codex_exec_share_docker_pressure_budget(tmp_path: Path) -> None:
    guard = threading.Lock()
    start = threading.Barrier(10)
    budget_entered = threading.Event()
    release = threading.Event()
    current = 0
    maximum = 0
    errors: list[BaseException] = []

    class BlockingProcess:
        def __init__(self, instance_id: str | None = None) -> None:
            self.instance_id = instance_id

        def run(self, argv: Sequence[str], **kwargs: object) -> CommandResult:
            nonlocal current, maximum
            with guard:
                current += 1
                maximum = max(maximum, current)
                if current == 4:
                    budget_entered.set()
            try:
                assert release.wait(timeout=5)
                if self.instance_id is not None:
                    arguments = tuple(argv)
                    output_dir = Path(arguments[arguments.index("--output_dir") + 1])
                    output_dir.mkdir(parents=True, exist_ok=True)
                    (output_dir / "eval_results.json").write_text(json.dumps({self.instance_id: True}))
                return CommandResult(tuple(argv), os.fspath(kwargs.get("cwd", "")), 0, "", "")
            finally:
                with guard:
                    current -= 1

    jobs: list[Callable[[], object]] = []
    for index in range(5):
        root = tmp_path / f"official-{index}"
        harness = root / "harness"
        harness.mkdir(parents=True)
        raw = root / "instance.jsonl"
        prediction = root / "predictions.json"
        raw.write_text("{}\n")
        prediction.write_text("[]")
        instance_id = f"instance-{index}"
        evaluator = OfficialEvaluator(BlockingProcess(instance_id), python_executable=sys.executable)
        jobs.append(
            lambda evaluator=evaluator, harness=harness, raw=raw, prediction=prediction, root=root, instance_id=instance_id: (
                evaluator.evaluate(
                    harness_root=harness,
                    raw_sample_path=raw,
                    prediction_path=prediction,
                    output_dir=root / "output",
                    instance_id=instance_id,
                )
            )
        )
    for index in range(4):
        runner = sut_module._DockerExecRunner(BlockingProcess(), f"container-{index}")
        jobs.append(lambda runner=runner: runner.run(("codex", "exec"), cwd=tmp_path))

    def run_job(job: Callable[[], object]) -> None:
        try:
            start.wait()
            job()
        except BaseException as error:  # noqa: BLE001 - thread failures must reach the assertion
            errors.append(error)

    threads = [threading.Thread(target=run_job, args=(job,)) for job in jobs]
    for thread in threads:
        thread.start()
    start.wait()
    try:
        assert budget_entered.wait(timeout=2)
        time.sleep(0.05)
        assert maximum == 4
    finally:
        release.set()
        for thread in threads:
            thread.join(timeout=5)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []


def _official_evaluator_inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    harness = tmp_path / "harness"
    harness.mkdir()
    fake = Path(__file__).parent / "fixtures" / "fake_evaluator.py"
    (harness / "swe_bench_pro_eval.py").write_bytes(fake.read_bytes())
    (harness / "run_scripts").mkdir()
    raw_path = tmp_path / "instance.jsonl"
    raw_path.write_text(json.dumps(raw_instance(), separators=(",", ":")) + "\n")
    prediction_path = tmp_path / "predictions.json"
    prediction_path.write_text(encode_predictions(INSTANCE_ID, "diff --git a/a b/a\n", "codex-0.145.0"))
    return harness, raw_path, prediction_path, tmp_path / "output"


class _InspectingProcess(ProcessRunner):
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.failure = failure
        self.argv: tuple[str, ...] | None = None
        self.environment: dict[str, str] | None = None
        self.docker_config: dict[str, object] | None = None
        self.docker_config_dir: Path | None = None
        self.directory_mode: int | None = None
        self.file_mode: int | None = None

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str | Path,
        timeout: float | None = None,
        cancel_event: threading.Event | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = True,
        secrets: Sequence[str] = (),
        input_bytes: bytes | None = None,
        stdout_sink: BinaryIO | None = None,
    ) -> CommandResult:
        self.argv = tuple(argv)
        assert env is not None
        environment = dict(env)
        self.environment = dict(environment)
        if docker_config := environment.get("DOCKER_CONFIG"):
            config_dir = Path(docker_config)
            config_path = config_dir / "config.json"
            self.docker_config_dir = config_dir
            self.docker_config = json.loads(config_path.read_text())
            self.directory_mode = stat.S_IMODE(config_dir.stat().st_mode)
            self.file_mode = stat.S_IMODE(config_path.stat().st_mode)
        if self.failure is not None:
            raise self.failure
        return super().run(
            argv,
            cwd=cwd,
            timeout=timeout,
            cancel_event=cancel_event,
            env=env,
            check=check,
            secrets=secrets,
            input_bytes=input_bytes,
            stdout_sink=stdout_sink,
        )


class _FakeRelay:
    def __init__(self) -> None:
        self.starts: list[tuple[str, ProxyRelayConfig]] = []
        self.stop_count = 0

    def start(self, gateway: str, upstream: ProxyRelayConfig) -> str:
        self.starts.append((gateway, upstream))
        return RELAY_URL

    def stop(self) -> None:
        self.stop_count += 1


def test_official_evaluator_uses_private_container_reachable_proxy_config_and_cleans_up(tmp_path: Path) -> None:
    harness, raw_path, prediction_path, output_dir = _official_evaluator_inputs(tmp_path)
    process = _InspectingProcess()
    relay = _FakeRelay()

    result = OfficialEvaluator(
        process,
        python_executable=sys.executable,
        proxy=ProxyRelayConfig(UPSTREAM_PROXY_URL),
        relay_factory=lambda: relay,
        gateway_resolver=lambda _process, _cwd: "172.17.0.1",
    ).evaluate(
        harness_root=harness,
        raw_sample_path=raw_path,
        prediction_path=prediction_path,
        output_dir=output_dir,
        instance_id=INSTANCE_ID,
    )

    assert result.resolved is True
    assert process.docker_config == {
        "proxies": {
            "default": {
                "httpProxy": RELAY_URL,
                "httpsProxy": RELAY_URL,
                "noProxy": LOOPBACK_NO_PROXY,
            }
        }
    }
    assert process.directory_mode == 0o700
    assert process.file_mode == 0o600
    assert process.environment is not None
    assert process.environment == {**direct_egress_environment(), "DOCKER_CONFIG": str(process.docker_config_dir)}
    assert process.argv is not None
    assert all(UPSTREAM_PROXY_URL not in argument for argument in process.argv)
    assert process.docker_config_dir is not None
    assert not process.docker_config_dir.exists()
    assert relay.starts == [("172.17.0.1", ProxyRelayConfig(UPSTREAM_PROXY_URL))]
    assert relay.stop_count == 1
    retained = "\n".join(path.read_text(errors="replace") for path in output_dir.rglob("*") if path.is_file())
    assert UPSTREAM_PROXY_URL not in retained


def test_official_evaluator_cleans_proxy_config_and_relay_after_process_failure(tmp_path: Path) -> None:
    harness, raw_path, prediction_path, output_dir = _official_evaluator_inputs(tmp_path)
    process = _InspectingProcess(failure=RuntimeError("official process failed"))
    relay = _FakeRelay()

    with pytest.raises(RuntimeError, match="official process failed"):
        OfficialEvaluator(
            process,
            python_executable=sys.executable,
            proxy=ProxyRelayConfig(UPSTREAM_PROXY_URL),
            relay_factory=lambda: relay,
            gateway_resolver=lambda _process, _cwd: "172.17.0.1",
        ).evaluate(
            harness_root=harness,
            raw_sample_path=raw_path,
            prediction_path=prediction_path,
            output_dir=output_dir,
            instance_id=INSTANCE_ID,
        )

    assert process.docker_config_dir is not None
    assert not process.docker_config_dir.exists()
    assert relay.stop_count == 1
    retained = "\n".join(path.read_text(errors="replace") for path in output_dir.rglob("*") if path.is_file())
    assert UPSTREAM_PROXY_URL not in retained


def test_official_evaluator_without_proxy_clears_proxy_environment_and_skips_relay(tmp_path: Path) -> None:
    harness, raw_path, prediction_path, output_dir = _official_evaluator_inputs(tmp_path)
    process = _InspectingProcess()
    relay = _FakeRelay()

    result = OfficialEvaluator(
        process,
        python_executable=sys.executable,
        relay_factory=lambda: relay,
        gateway_resolver=lambda _process, _cwd: pytest.fail("gateway must not be resolved"),
    ).evaluate(
        harness_root=harness,
        raw_sample_path=raw_path,
        prediction_path=prediction_path,
        output_dir=output_dir,
        instance_id=INSTANCE_ID,
    )

    assert result.resolved is True
    assert process.environment == direct_egress_environment()
    assert process.docker_config_dir is None
    assert relay.starts == []
    assert relay.stop_count == 0


def test_default_docker_bridge_gateway_uses_read_only_inspect_and_validates_result(tmp_path: Path) -> None:
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    class Process:
        def run(self, argv: tuple[str, ...], **kwargs: object) -> SimpleNamespace:
            calls.append((argv, kwargs))
            return SimpleNamespace(stdout="172.17.0.1\n")

    gateway = sut_module.default_docker_bridge_gateway(cast(ProcessRunner, Process()), tmp_path)

    assert gateway == "172.17.0.1"
    assert calls == [
        (
            ("docker", "network", "inspect", "bridge", "--format={{(index .IPAM.Config 0).Gateway}}"),
            {"cwd": tmp_path, "timeout": 30},
        )
    ]


def test_official_evaluator_retains_required_test_details_and_bounded_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = tmp_path / "harness"
    harness.mkdir()
    fake = Path(__file__).parent / "fixtures" / "fake_evaluator.py"
    (harness / "swe_bench_pro_eval.py").write_bytes(fake.read_bytes())
    (harness / "run_scripts").mkdir()
    raw_path = tmp_path / "instance.jsonl"
    raw_path.write_text(json.dumps(raw_instance(), separators=(",", ":")) + "\n")
    prediction_path = tmp_path / "predictions.json"
    prediction_path.write_text(encode_predictions(INSTANCE_ID, "diff --git a/a b/a\n", "codex-0.145.0"))
    output_dir = tmp_path / "output"
    monkeypatch.setenv("FAKE_EVAL_RESULT", json.dumps({INSTANCE_ID: False}))
    monkeypatch.setenv(
        "FAKE_EVAL_OUTPUT",
        json.dumps(
            {
                "tests": [
                    {"name": "TestLoad", "status": "FAILED"},
                    {"name": "TestRegression", "status": "PASSED"},
                    {"name": "UnscoredExtra", "status": "FAILED"},
                ]
            }
        ),
    )
    monkeypatch.setenv("FAKE_EVAL_STDERR", "failure detail\n" + "x" * 10_000)

    result = OfficialEvaluator(ProcessRunner(), python_executable=sys.executable).evaluate(
        harness_root=harness,
        raw_sample_path=raw_path,
        prediction_path=prediction_path,
        output_dir=output_dir,
        instance_id=INSTANCE_ID,
        required_fail_to_pass=("TestLoad",),
        required_pass_to_pass=("TestRegression",),
        patch_applied=True,
    )

    assert result.resolved is False
    assert result.patch_applied is True
    assert result.fail_to_pass == TestGroupResult(passed=0, total=1, failed=("TestLoad",))
    assert result.pass_to_pass == TestGroupResult(passed=1, total=1, failed=())
    assert result.log_excerpt is not None
    assert "failure detail" in result.log_excerpt
    assert len(result.log_excerpt) <= 4_000


def test_official_evaluator_accepts_duplicate_test_names_with_the_same_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = tmp_path / "harness"
    harness.mkdir()
    fake = Path(__file__).parent / "fixtures" / "fake_evaluator.py"
    (harness / "swe_bench_pro_eval.py").write_bytes(fake.read_bytes())
    (harness / "run_scripts").mkdir()
    raw_path = tmp_path / "instance.jsonl"
    raw_path.write_text(json.dumps(raw_instance()) + "\n")
    prediction_path = tmp_path / "predictions.json"
    prediction_path.write_text(encode_predictions(INSTANCE_ID, "diff --git a/a b/a\n", "codex-0.145.0"))
    monkeypatch.setenv(
        "FAKE_EVAL_OUTPUT",
        json.dumps(
            {
                "tests": [
                    {"name": "TestLoad", "status": "PASSED"},
                    {"name": "TestLoad", "status": "PASSED"},
                    {"name": "TestRegression", "status": "PASSED"},
                ]
            }
        ),
    )

    result = OfficialEvaluator(ProcessRunner(), python_executable=sys.executable).evaluate(
        harness_root=harness,
        raw_sample_path=raw_path,
        prediction_path=prediction_path,
        output_dir=tmp_path / "output",
        instance_id=INSTANCE_ID,
        required_fail_to_pass=("TestLoad",),
        required_pass_to_pass=("TestRegression",),
        patch_applied=True,
    )

    assert result.resolved is True
    assert result.fail_to_pass == TestGroupResult(passed=1, total=1, failed=())
    assert result.pass_to_pass == TestGroupResult(passed=1, total=1, failed=())


@pytest.mark.parametrize(
    "output",
    [
        {"tests": [{"name": "TestLoad", "status": "PASSED"}, {"name": "TestLoad", "status": "FAILED"}]},
        {"tests": [{"name": "TestLoad", "status": "UNKNOWN"}]},
        {"not_tests": []},
    ],
)
def test_official_evaluator_rejects_ambiguous_test_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    output: dict[str, object],
) -> None:
    harness = tmp_path / "harness"
    harness.mkdir()
    fake = Path(__file__).parent / "fixtures" / "fake_evaluator.py"
    (harness / "swe_bench_pro_eval.py").write_bytes(fake.read_bytes())
    (harness / "run_scripts").mkdir()
    raw_path = tmp_path / "instance.jsonl"
    raw_path.write_text(json.dumps(raw_instance()) + "\n")
    prediction_path = tmp_path / "predictions.json"
    prediction_path.write_text(encode_predictions(INSTANCE_ID, "diff --git a/a b/a\n", "codex-0.145.0"))
    monkeypatch.setenv("FAKE_EVAL_OUTPUT", json.dumps(output))

    with pytest.raises(OfficialResultError):
        OfficialEvaluator(ProcessRunner(), python_executable=sys.executable).evaluate(
            harness_root=harness,
            raw_sample_path=raw_path,
            prediction_path=prediction_path,
            output_dir=tmp_path / "output",
            instance_id=INSTANCE_ID,
            required_fail_to_pass=("TestLoad",),
            required_pass_to_pass=(),
            patch_applied=True,
        )


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "missing"),
        ({INSTANCE_ID: 1}, "boolean"),
        ({INSTANCE_ID: True, "other": False}, "exact instance"),
    ],
)
def test_official_evaluator_rejects_non_exact_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: dict[str, object], message: str
) -> None:
    harness = tmp_path / "harness"
    harness.mkdir()
    fake = Path(__file__).parent / "fixtures" / "fake_evaluator.py"
    (harness / "swe_bench_pro_eval.py").write_bytes(fake.read_bytes())
    (harness / "run_scripts").mkdir()
    raw_path = tmp_path / "instance.jsonl"
    raw_path.write_text("{}\n")
    prediction_path = tmp_path / "predictions.json"
    prediction_path.write_text("[]")
    output_dir = tmp_path / "output"
    monkeypatch.setenv("FAKE_EVAL_RESULT", json.dumps(payload))

    with pytest.raises(OfficialResultError, match=message):
        OfficialEvaluator(ProcessRunner(), python_executable=sys.executable).evaluate(
            harness_root=harness,
            raw_sample_path=raw_path,
            prediction_path=prediction_path,
            output_dir=output_dir,
            instance_id=INSTANCE_ID,
        )
