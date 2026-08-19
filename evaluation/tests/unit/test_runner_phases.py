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
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from powercontext_eval.artifacts import ArtifactStore, SecretDetected
from powercontext_eval.benchmarks.swebench_pro.adapter import SweBenchProInstance
from powercontext_eval.benchmarks.swebench_pro.evaluator import OfficialEvaluation
from powercontext_eval.benchmarks.swebench_pro.gold_overrides import (
    SOURCE559_DATASET_PATCH_SHA256,
    SOURCE559_INSTANCE_ID,
    SOURCE559_REFERENCE_DATASET,
    SOURCE559_REFERENCE_FILE_OID,
    SOURCE559_REFERENCE_PATCH,
    SOURCE559_REFERENCE_PATCH_SHA256,
    SOURCE559_REFERENCE_REVISION,
    GoldValidationSelection,
)
from powercontext_eval.codex import CodexOutcome
from powercontext_eval.errors import CommandFailed
from powercontext_eval.models import Arm
from powercontext_eval.powercontext_sut import ProxyRelayConfig, SutConfig
from powercontext_eval.process import CommandResult, ProcessRunner
from powercontext_eval.report import ReportBundle
from powercontext_eval.runner import (
    MinimalRunResult,
    PhaseCallback,
    RunConfig,
    RunPhase,
    _evaluator_test_requirements,
    _resolve_task_image,
    run_swebench_pro_instance,
)
from powercontext_eval.tokensflow import TokensFlowInfrastructureError

INSTANCE_ID = "instance_owner__repo-b"
OPENLIBRARY_DYNAMIC_YEAR_INSTANCE_ID = (
    "instance_internetarchive__openlibrary-1351c59fd43689753de1fca32c78d539a116ffc1-"
    "v29f82c9cf21d57b242f8d8b0e541525d259e2d63"
)
OTHER_OPENLIBRARY_DYNAMIC_YEAR_INSTANCE_ID = (
    "instance_internetarchive__openlibrary-43f9e7f9d13888d6303d6a4ae7142f48e94f0d2a-"
    "v0f5ae00000000000000000000000000000000000"
)
SOURCE595_INSTANCE_ID = (
    "instance_ansible__ansible-de5858f48dc9e1ce9117034e0d7e76806f420ca8-v1055803c3a812189a1133297f7f5468579283f86"
)
SOURCE595_SELECTED_TEST_FILES = (
    '["test/integration/targets/ansible-galaxy-collection/library/setup_collections.py", '
    '"test/units/galaxy/test_api.py"]'
)
SOURCE595_EFFECTIVE_TEST_FILES = '["test/units/galaxy/test_api.py"]'
SOURCE595_LEGACY_CACHE_INVALID_TEST = 'test/units/galaxy/test_api.py::test_cache_invalid_cache_content[{"de'
SOURCE595_PARSED_CACHE_INVALID_TEST = f'{SOURCE595_LEGACY_CACHE_INVALID_TEST}"'
SOURCE621_INSTANCE_ID = (
    "instance_future-architect__vuls-bff6b7552370b55ff76d474860eead4ab5de785a-v1151a6325649aaf997cd541ebe533b53fddf1b07"
)
SOURCE621_LEGACY_TEST_NAMES = (
    'Test_redhatBase_parseUpdatablePacksLine/centos_7.0:_"zlib"_"0"_"1.2.7"_"17.el7"_"rhui-REGION-rhel-server-releases',
    (
        'Test_redhatBase_parseUpdatablePacksLine/centos_7.0:_"shadow-utils"_"2"_"4.1.5.1_24.el7"_'
        '"rhui-REGION-rhel-server-releases'
    ),
    (
        'Test_redhatBase_parseUpdatablePacksLine/amazon_2023:_Is_this_ok_[y/N]:_"dnf"_"0"_"4.14.0"_'
        '"1.amzn2023.0.6"_"amazonlinux'
    ),
)
OPENLIBRARY_DYNAMIC_YEAR_PREFIX = (
    "openlibrary/catalog/add_book/tests/test_add_book.py::TestNormalizeImportRecord::"
    "test_future_publication_dates_are_deleted"
)
NODEBB_DIGEST_TEST_PREFIX = (
    "test/user.js | User Digest.getSubscribers should accurately build digest list given ACP default "
)
NODEBB_BIG_ARRAY_TEST = (
    "test/database.js | Test database test/database/sorted.js::Sorted Set methods "
    "test/database/sorted.js::getSortedSetRange() should work with big arrays (length > 100)"
)
IMAGE_ID = "sha256:" + "d" * 64
ProcessCall = tuple[tuple[str, ...], dict[str, object]]
EvaluatorCall = dict[str, object]


def _instance() -> SweBenchProInstance:
    return SweBenchProInstance.from_public_raw(
        {
            "FAIL_TO_PASS": ["test_fix"],
            "PASS_TO_PASS": '["test_regression"]',
            "base_commit": "b" * 40,
            "base_dockerfile": "FROM ubuntu:24.04",
            "before_repo_set_cmd": "git reset --hard",
            "created_at": "2026-01-01T00:00:00Z",
            "hints_text": "",
            "image_name": ("084828598639.dkr.ecr.us-west-2.amazonaws.com/sweap-images/owner.repo:owner__repo-b"),
            "instance_dockerfile": "RUN true",
            "instance_id": INSTANCE_ID,
            "is_remote_image": True,
            "parsing_script": "parse",
            "patch": "gold patch",
            "problem_statement": "Fix arbitrary instance B",
            "repo": "owner/repo",
            "repo_name": "repo",
            "run_script": "pytest",
            "selected_test_files_to_run": '["test_fix", "test_regression"]',
            "test_patch": "test patch",
            "version": "v2",
        }
    )


def _openlibrary_dynamic_year_instance(
    *, instance_id: str = OPENLIBRARY_DYNAMIC_YEAR_INSTANCE_ID
) -> SweBenchProInstance:
    raw = _instance().official_row()
    raw["instance_id"] = instance_id
    raw["PASS_TO_PASS"] = [
        "test_regression",
        f"{OPENLIBRARY_DYNAMIC_YEAR_PREFIX}[2025-True]",
        f"{OPENLIBRARY_DYNAMIC_YEAR_PREFIX}[2026-False]",
    ]
    return SweBenchProInstance.from_public_raw(raw)


def test_run_phases_have_stable_order_and_values() -> None:
    assert list(RunPhase) == [
        RunPhase.PREPARING,
        RunPhase.VALIDATING_GOLD,
        RunPhase.RUNNING_OFF,
        RunPhase.RUNNING_ON,
        RunPhase.OFFICIAL_EVALUATION,
        RunPhase.GENERATING_REPORT,
    ]
    assert [phase.value for phase in RunPhase] == [
        "preparing",
        "validating_gold",
        "running_off",
        "running_on",
        "official_evaluation",
        "generating_report",
    ]


def test_task_image_uses_an_existing_local_image_without_registry_access(tmp_path: Path) -> None:
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    class Process:
        def run(self, argv: tuple[str, ...], **kwargs: object) -> SimpleNamespace:
            calls.append((argv, kwargs))
            return SimpleNamespace(returncode=0, stdout=IMAGE_ID + "\n")

    image_id = _resolve_task_image(
        cast(ProcessRunner, Process()),
        "owner/image:tag",
        cwd=tmp_path,
        registry_binary=tmp_path / "regctl",
        proxy_url="http://127.0.0.1:18080",
    )

    assert image_id == IMAGE_ID
    assert [call[0] for call in calls] == [("docker", "image", "inspect", "--format={{.Id}}", "owner/image:tag")]
    assert calls[0][1]["check"] is False


def test_missing_task_image_is_exported_through_proxy_loaded_and_verified(tmp_path: Path) -> None:
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    inspect_count = 0

    class Process:
        def run(self, argv: tuple[str, ...], **kwargs: object) -> SimpleNamespace:
            nonlocal inspect_count
            calls.append((argv, kwargs))
            if argv[:3] == ("docker", "image", "inspect"):
                inspect_count += 1
                if inspect_count == 1:
                    return SimpleNamespace(returncode=1, stdout="")
                return SimpleNamespace(returncode=0, stdout=IMAGE_ID + "\n")
            if argv[:3] == (str(tmp_path / "regctl"), "image", "export"):
                Path(argv[-1]).write_bytes(b"docker archive")
            return SimpleNamespace(returncode=0, stdout="")

    image_id = _resolve_task_image(
        cast(ProcessRunner, Process()),
        "owner/image:tag",
        cwd=tmp_path,
        registry_binary=tmp_path / "regctl",
        proxy_url="http://127.0.0.1:18080",
    )

    assert image_id == IMAGE_ID
    commands = [call[0] for call in calls]
    assert commands[0] == ("docker", "image", "inspect", "--format={{.Id}}", "owner/image:tag")
    assert commands[1][:7] == (
        str(tmp_path / "regctl"),
        "image",
        "export",
        "--platform",
        "linux/amd64",
        "--name",
        "owner/image:tag",
    )
    assert commands[2][0:3] == ("docker", "load", "-i")
    assert commands[3] == ("docker", "image", "inspect", "--format={{.Id}}", "owner/image:tag")
    assert calls[1][1]["env"] == {
        "HTTPS_PROXY": "http://127.0.0.1:18080",
        "HTTP_PROXY": "http://127.0.0.1:18080",
        "https_proxy": "http://127.0.0.1:18080",
        "http_proxy": "http://127.0.0.1:18080",
        "NO_PROXY": "127.0.0.1,localhost,::1",
        "no_proxy": "127.0.0.1,localhost,::1",
    }
    assert not list(tmp_path.glob(".task-image-*.tar"))


def test_failed_task_image_export_removes_the_partial_archive(tmp_path: Path) -> None:
    class Process:
        def run(self, argv: tuple[str, ...], **kwargs: object) -> SimpleNamespace:
            if argv[:3] == ("docker", "image", "inspect"):
                return SimpleNamespace(returncode=1, stdout="")
            Path(argv[-1]).write_bytes(b"partial")
            raise RuntimeError("registry failed")

    with pytest.raises(RuntimeError, match="registry failed"):
        _resolve_task_image(
            cast(ProcessRunner, Process()),
            "owner/image:tag",
            cwd=tmp_path,
            registry_binary=tmp_path / "regctl",
            proxy_url="http://127.0.0.1:18080",
        )

    assert not list(tmp_path.glob(".task-image-*.tar"))


def _config(tmp_path: Path) -> RunConfig:
    auth_json = tmp_path / "auth.json"
    auth_json.write_text('{"api_key":"runner-secret-value"}')
    tokensflow_config = tmp_path / "tokensflow-profile" / ".tokensflow"
    tokensflow_config.mkdir(parents=True)
    (tokensflow_config / "credentials.json").write_text('{"access_token":"tokensflow-secret-value"}')
    return RunConfig(
        root=tmp_path / "eval",
        powercontext_source=tmp_path / "source",
        powercontext_ref="latest",
        harness_root=tmp_path / "harness",
        harness_python=tmp_path / "python",
        codex_binary=tmp_path / "codex",
        tokensflow_enabled=True,
        tokensflow_binary=tmp_path / "tokensflow",
        tokensflow_user_home=tmp_path / "tokensflow-profile",
        tokensflow_egress_network="bridge",
        uv_binary=tmp_path / "uv",
        registry_binary=tmp_path / "regctl",
        auth_json=auth_json,
        proxy_url="http://127.0.0.1:18080",
        run_id="run-test",
    )


def _run_with_fakes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    events: list[object],
    on_phase: PhaseCallback | None = None,
    *,
    image_present: bool = True,
    evaluator_failure: Exception | None = None,
    image_cleanup_conflicts: int = 0,
    image_cleanup_failure: Exception | None = None,
    observed: dict[str, object] | None = None,
    instance: SweBenchProInstance | None = None,
    config_mutator: Callable[[RunConfig], None] | None = None,
    model: str | None = None,
    docker_network_pool: str | None = None,
    extra_no_proxy_hosts: tuple[str, ...] | None = None,
) -> tuple[RunConfig, MinimalRunResult, dict[str, object]]:
    config = _config(tmp_path)
    if model is not None:
        config = replace(config, model=model)
    if docker_network_pool is not None:
        config = replace(config, docker_network_pool=docker_network_pool)
    if extra_no_proxy_hosts is not None:
        config = replace(config, extra_no_proxy_hosts=extra_no_proxy_hosts)
    if config_mutator is not None:
        config_mutator(config)
    instance = _instance() if instance is None else instance
    materialized = tmp_path / "materialized"
    materialized.mkdir()
    resolved = SimpleNamespace(sha="a" * 40)
    observed = observed if observed is not None else {}
    process_calls: list[ProcessCall] = []
    evaluator_calls: list[EvaluatorCall] = []
    evaluator_initializations: list[dict[str, object]] = []
    observed.update(
        {
            "process_calls": process_calls,
            "evaluator_calls": evaluator_calls,
            "evaluator_initializations": evaluator_initializations,
        }
    )
    image_loaded = image_present
    image_cleanup_attempts = 0

    class FakeProcess:
        def __init__(self, *, default_cancel_event: object = None) -> None:
            observed["default_cancel_event"] = default_cancel_event

        def run(self, argv: tuple[str, ...], **kwargs: object) -> SimpleNamespace:
            nonlocal image_cleanup_attempts, image_loaded
            process_calls.append((argv, kwargs))
            if argv[:3] == ("docker", "image", "inspect"):
                return (
                    SimpleNamespace(returncode=0, stdout=IMAGE_ID + "\n")
                    if image_loaded
                    else SimpleNamespace(returncode=1, stdout="")
                )
            if argv[:3] == (str(config.registry_binary), "image", "export"):
                Path(argv[-1]).write_bytes(b"docker archive")
                return SimpleNamespace(returncode=0, stdout="")
            if argv[:2] == ("docker", "load"):
                image_loaded = True
                return SimpleNamespace(returncode=0, stdout="")
            if argv[:3] == ("docker", "image", "rm"):
                image_cleanup_attempts += 1
                if image_cleanup_attempts <= image_cleanup_conflicts:
                    raise CommandFailed(
                        "image cleanup conflict",
                        CommandResult(
                            argv=argv,
                            cwd=str(kwargs["cwd"]),
                            returncode=1,
                            stdout="",
                            stderr=(
                                "conflict: unable to remove repository reference - "
                                "container abc123 is using its referenced image"
                            ),
                        ),
                    )
                if image_cleanup_failure is not None:
                    raise image_cleanup_failure
                image_loaded = False
                return SimpleNamespace(returncode=0, stdout="")
            if argv[:2] == ("docker", "run"):
                return SimpleNamespace(stdout="", returncode=0)
            assert argv == ("git", "diff", "--binary", "--full-index", instance.base_commit, "--")
            return SimpleNamespace(stdout="candidate patch")

    class FakeSource:
        def resolve(self, *args: object, **kwargs: object) -> object:
            events.append("prepare")
            return resolved

        def materialize(self, *args: object, **kwargs: object) -> Path:
            return materialized

    class FakeEvaluator:
        def __init__(self, *args: object, **kwargs: object) -> None:
            evaluator_initializations.append({"args": args, "kwargs": kwargs})

        def evaluate(self, **kwargs: object) -> OfficialEvaluation:
            if evaluator_failure is not None:
                raise evaluator_failure
            prediction_path = kwargs["prediction_path"]
            raw_sample_path = kwargs["raw_sample_path"]
            assert isinstance(prediction_path, Path)
            assert isinstance(raw_sample_path, Path)
            evaluator_calls.append(kwargs)
            retained = json.loads(raw_sample_path.read_text())
            assert raw_sample_path.name == "evaluator-instance.jsonl"
            assert retained["instance_id"] == instance.instance_id
            assert json.loads(retained["fail_to_pass"]) == list(cast(tuple[str, ...], kwargs["required_fail_to_pass"]))
            assert json.loads(retained["pass_to_pass"]) == list(cast(tuple[str, ...], kwargs["required_pass_to_pass"]))
            events.append("gold" if prediction_path.parent.name == "gold" else "official")
            return OfficialEvaluation(instance.instance_id, True, "", "")

    class FakeSut:
        def run_pair(
            self,
            sut_config: object,
            *,
            prompts: dict[Arm, bytes],
            before_arm: Callable[[Arm], None] | None = None,
            **kwargs: object,
        ) -> dict[Arm, object]:
            observed["sut_config"] = sut_config
            observed["prompts"] = prompts
            stores = cast(dict[Arm, ArtifactStore], kwargs["stores"])
            observed["stores"] = stores
            assert before_arm is not None
            for arm in (Arm.OFF, Arm.ON):
                before_arm(arm)
                events.append(arm)
                observed_at = datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
                stores[arm].write_text(
                    "context/codex-observed.jsonl",
                    f'{{"sequence":1,"observed_at":"{observed_at}",'
                    '"event":{"type":"agent_message","message":"done"}}\n',
                )
            outcome = SimpleNamespace(codex=CodexOutcome("", None))
            return {Arm.OFF: outcome, Arm.ON: outcome}

    monkeypatch.setattr("powercontext_eval.runner.ProcessRunner", FakeProcess)
    monkeypatch.setattr("powercontext_eval.runner.GitSource", lambda **kwargs: FakeSource())
    monkeypatch.setattr("powercontext_eval.runner.OfficialEvaluator", FakeEvaluator)
    monkeypatch.setattr("powercontext_eval.runner.DockerSut", lambda *args, **kwargs: FakeSut())
    callback = on_phase if on_phase is not None else lambda phase: events.append(phase)
    result = run_swebench_pro_instance(config, instance=instance, on_phase=callback)
    return config, result, observed


def test_runner_propagates_batch_model_to_codex_pair_and_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, result, observed = _run_with_fakes(
        tmp_path,
        monkeypatch,
        [],
        model="gpt-5.6-luna",
    )

    sut_config = cast(SutConfig, observed["sut_config"])
    report = ReportBundle.model_validate_json(
        (config.root / "runs" / result.run_id / "report.json").read_text(),
        strict=True,
    )
    assert (sut_config.model, sut_config.reasoning_effort) == ("gpt-5.6-luna", "medium")
    assert report.configuration["model"] == "gpt-5.6-luna"
    assert report.configuration["reasoning_effort"] == "medium"


def test_runner_propagates_portable_network_configuration_to_sut_evaluator_and_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, result, observed = _run_with_fakes(
        tmp_path,
        monkeypatch,
        [],
        docker_network_pool="10.72.0.0/20",
        extra_no_proxy_hosts=("mirror.example.test",),
    )

    sut_config = cast(SutConfig, observed["sut_config"])
    report = ReportBundle.model_validate_json(
        (config.root / "runs" / result.run_id / "report.json").read_text(), strict=True
    )
    initializations = cast(list[dict[str, object]], observed["evaluator_initializations"])
    evaluator_kwargs = cast(dict[str, object], initializations[0]["kwargs"])
    assert sut_config.docker_network_pool == "10.72.0.0/20"
    assert sut_config.extra_no_proxy_hosts == ("mirror.example.test",)
    assert evaluator_kwargs["extra_no_proxy_hosts"] == ("mirror.example.test",)
    assert report.configuration["docker_network_pool"] == "10.72.0.0/20"
    assert report.configuration["extra_no_proxy_hosts"] == "mirror.example.test"


def test_runner_snapshots_tokensflow_per_arm_and_blocks_both_credential_sets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, result, observed = _run_with_fakes(tmp_path, monkeypatch, [])

    for arm in (Arm.OFF, Arm.ON):
        snapshot = config.root / "work" / result.run_id / arm.value / "runtime/root-home"
        assert (snapshot / ".tokensflow/credentials.json").read_text() == (
            config.tokensflow_user_home / ".tokensflow/credentials.json"
        ).read_text()
        store = cast(dict[Arm, ArtifactStore], observed["stores"])[arm]
        with pytest.raises(SecretDetected):
            store.write_text("leak-codex.txt", "runner-secret-value")
        with pytest.raises(SecretDetected):
            store.write_text("leak-tokensflow.txt", "tokensflow-secret-value")

    retained = (config.root / "runs" / result.run_id / "manifest.json").read_text()
    assert os.fspath(config.auth_json) not in retained
    assert os.fspath(config.tokensflow_user_home) not in retained


@pytest.mark.parametrize("profile", ["missing", "symlink"])
def test_runner_translates_unsafe_tokensflow_profile_to_sanitized_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    profile: str,
) -> None:
    def break_profile(config: RunConfig) -> None:
        credentials = config.tokensflow_user_home / ".tokensflow/credentials.json"
        credentials.unlink()
        if profile == "symlink":
            external = tmp_path / "external-credentials.json"
            external.write_text('{"access_token":"do-not-retain"}')
            credentials.symlink_to(external)

    with pytest.raises(TokensFlowInfrastructureError) as captured:
        _run_with_fakes(tmp_path, monkeypatch, [], config_mutator=break_profile)

    assert str(captured.value) == "TokensFlow profile snapshot failed"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is not None
    assert os.fspath(tmp_path) not in str(captured.value)


def test_runner_reuses_one_proxy_configured_official_evaluator_for_gold_off_and_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, _result, observed = _run_with_fakes(tmp_path, monkeypatch, [])

    initializations = cast(list[dict[str, object]], observed["evaluator_initializations"])
    calls = cast(list[EvaluatorCall], observed["evaluator_calls"])
    assert len(initializations) == 1
    assert initializations[0]["kwargs"] == {
        "python_executable": str(config.harness_python),
        "proxy": ProxyRelayConfig(config.proxy_url),
        "extra_no_proxy_hosts": (),
    }
    assert len(calls) == 3


def test_runner_uses_arbitrary_instance_prompt_image_and_base_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, result, observed = _run_with_fakes(tmp_path, monkeypatch, [])
    instance = _instance()

    manifest = json.loads((config.root / "runs" / result.run_id / "manifest.json").read_text())
    assert manifest["instance_id"] == instance.instance_id
    assert manifest["task_image"] == instance.task_image
    assert manifest["task_image_id"] == IMAGE_ID
    retained = json.loads((config.root / "runs" / result.run_id / "instance.jsonl").read_text())
    assert retained == instance.official_row()
    assert "fail_to_pass" not in retained
    assert "pass_to_pass" not in retained
    sut_config = cast(SutConfig, observed["sut_config"])
    assert sut_config.task_image == IMAGE_ID
    assert sut_config.tokensflow_binary == config.tokensflow_binary
    prompts = cast(dict[Arm, bytes], observed["prompts"])
    assert isinstance(prompts, dict)
    assert prompts[Arm.OFF] == prompts[Arm.ON]
    assert instance.problem_statement.encode() in prompts[Arm.OFF]
    calls = cast(list[ProcessCall], observed["process_calls"])
    assert isinstance(calls, list)
    assert calls[0][0] == ("docker", "image", "inspect", "--format={{.Id}}", instance.task_image)
    patch_checks = [call for call in calls if call[0][:2] == ("docker", "run")]
    assert len(patch_checks) == 3
    assert patch_checks[0][1]["input_bytes"] == instance.patch.encode()
    evaluator_calls = cast(list[EvaluatorCall], observed["evaluator_calls"])
    assert isinstance(evaluator_calls, list)
    assert [call["patch_applied"] for call in evaluator_calls] == [True, True, True]
    assert {call["required_fail_to_pass"] for call in evaluator_calls} == {instance.fail_to_pass}
    assert {call["required_pass_to_pass"] for call in evaluator_calls} == {instance.pass_to_pass}
    assert [call[0] for call in calls].count(
        ("git", "diff", "--binary", "--full-index", instance.base_commit, "--")
    ) == 2
    assert len(evaluator_calls) == 3
    assert {call["instance_id"] for call in evaluator_calls} == {instance.instance_id}
    for arm in (Arm.OFF, Arm.ON):
        timeline = config.root / "runs" / result.run_id / "arms" / arm.value / "context" / "timeline.jsonl"
        assert [json.loads(line)["actor"] for line in timeline.read_text().splitlines()] == [
            "benchmark",
            "codex",
            "official_evaluator",
        ]
    assert not any(call[0][:3] == ("docker", "image", "rm") for call in calls)


def test_source559_gold_override_preserves_original_row_and_off_on_patches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = _instance().official_row()
    raw["instance_id"] = SOURCE559_INSTANCE_ID
    raw["patch"] = "original dataset Gold patch"
    instance = SweBenchProInstance.from_public_raw(raw)
    selection = GoldValidationSelection(
        instance_id=SOURCE559_INSTANCE_ID,
        mode="verified_override",
        dataset_patch_sha256=SOURCE559_DATASET_PATCH_SHA256,
        validation_patch=SOURCE559_REFERENCE_PATCH,
        validation_patch_sha256=SOURCE559_REFERENCE_PATCH_SHA256,
        dataset_patch_status="known_failed",
        reference_validation_status="passed",
        official_evaluation_transport="proxy_bypassed_for_test_isolation",
        source_dataset=SOURCE559_REFERENCE_DATASET,
        source_revision=SOURCE559_REFERENCE_REVISION,
        source_file_oid=SOURCE559_REFERENCE_FILE_OID,
        source_kind="verified_reference_submission",
    )
    monkeypatch.setattr("powercontext_eval.runner.select_gold_validation", lambda *_args: selection)
    config, result, _observed = _run_with_fakes(tmp_path, monkeypatch, [], instance=instance)

    run_root = config.root / "runs" / result.run_id
    retained = json.loads((run_root / "instance.jsonl").read_text())
    evaluator_row = json.loads((run_root / "evaluator-instance.jsonl").read_text())
    assert retained == instance.official_row()
    assert evaluator_row["patch"] == instance.patch
    original_prediction = json.loads((run_root / "gold/original-predictions.json").read_text())[0]
    gold_prediction = json.loads((run_root / "gold/predictions.json").read_text())[0]
    assert original_prediction["patch"] == instance.patch
    assert gold_prediction["patch"] == SOURCE559_REFERENCE_PATCH
    for arm in (Arm.OFF, Arm.ON):
        prediction = json.loads((run_root / "arms" / arm.value / "prediction.json").read_text())[0]
        assert prediction["patch"] == "candidate patch"
    validation = json.loads((run_root / "gold/validation.json").read_text())
    assert validation["attempt_gold_validation_status"] == "passed"
    report = ReportBundle.model_validate_json((run_root / "report.json").read_text(), strict=True)
    assert report.gold_validation is not None
    assert report.gold_validation.mode == "verified_override"
    assert report.gold_validation.official_evaluation_transport == "proxy_bypassed_for_test_isolation"
    initializations = cast(list[dict[str, object]], _observed["evaluator_initializations"])
    assert len(initializations) == 1
    assert initializations[0]["kwargs"] == {"python_executable": str(config.harness_python)}


def test_source595_uses_only_required_unit_file_for_all_official_evaluations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = _instance().official_row()
    raw["instance_id"] = SOURCE595_INSTANCE_ID
    raw["patch"] = "source595 Gold patch"
    raw["selected_test_files_to_run"] = SOURCE595_SELECTED_TEST_FILES
    instance = SweBenchProInstance.from_public_raw(raw)
    source595_patch_hash = "f984e4a44cf8ce42671e5a4740656f99da379f829d312c6885f9d13ffb875c22"
    selection = GoldValidationSelection(
        instance_id=SOURCE595_INSTANCE_ID,
        mode="dataset_patch",
        dataset_patch_sha256=source595_patch_hash,
        validation_patch=instance.patch,
        validation_patch_sha256=source595_patch_hash,
        dataset_patch_status="unverified",
        reference_validation_status="not_applicable",
        official_evaluation_test_selection="required_unit_tests_only_for_invalid_integration_target",
        evaluator_selected_test_files_to_run=SOURCE595_EFFECTIVE_TEST_FILES,
    )
    monkeypatch.setattr("powercontext_eval.runner.select_gold_validation", lambda *_args: selection)

    config, result, _observed = _run_with_fakes(tmp_path, monkeypatch, [], instance=instance)

    run_root = config.root / "runs" / result.run_id
    retained = json.loads((run_root / "instance.jsonl").read_text())
    evaluator_row = json.loads((run_root / "evaluator-instance.jsonl").read_text())
    assert retained["selected_test_files_to_run"] == SOURCE595_SELECTED_TEST_FILES
    assert evaluator_row["selected_test_files_to_run"] == SOURCE595_EFFECTIVE_TEST_FILES
    report = ReportBundle.model_validate_json((run_root / "report.json").read_text(), strict=True)
    assert report.gold_validation is not None
    assert report.gold_validation.official_evaluation_test_selection == (
        "required_unit_tests_only_for_invalid_integration_target"
    )


def test_runner_reconciles_the_pinned_openlibrary_dynamic_year_test_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = _openlibrary_dynamic_year_instance()
    current_year = datetime.now(UTC).year
    _config, _result, observed = _run_with_fakes(tmp_path, monkeypatch, [], instance=instance)

    calls = cast(list[EvaluatorCall], observed["evaluator_calls"])
    expected_pass_to_pass = (
        "test_regression",
        f"{OPENLIBRARY_DYNAMIC_YEAR_PREFIX}[{current_year}-True]",
        f"{OPENLIBRARY_DYNAMIC_YEAR_PREFIX}[{current_year + 1}-False]",
    )
    assert {call["required_pass_to_pass"] for call in calls} == {expected_pass_to_pass}
    retained = json.loads(cast(Path, calls[0]["raw_sample_path"]).read_text())
    assert retained["PASS_TO_PASS"] == list(expected_pass_to_pass)
    assert json.loads(retained["pass_to_pass"]) == list(expected_pass_to_pass)
    original = json.loads((_config.root / "runs" / _result.run_id / "instance.jsonl").read_text())
    assert original == instance.official_row()


def test_runner_reconciles_openlibrary_dynamic_year_test_ids_for_another_instance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance = _openlibrary_dynamic_year_instance(instance_id=OTHER_OPENLIBRARY_DYNAMIC_YEAR_INSTANCE_ID)
    current_year = datetime.now(UTC).year
    _config, _result, observed = _run_with_fakes(tmp_path, monkeypatch, [], instance=instance)

    calls = cast(list[EvaluatorCall], observed["evaluator_calls"])
    expected_pass_to_pass = (
        "test_regression",
        f"{OPENLIBRARY_DYNAMIC_YEAR_PREFIX}[{current_year}-True]",
        f"{OPENLIBRARY_DYNAMIC_YEAR_PREFIX}[{current_year + 1}-False]",
    )
    assert {call["required_pass_to_pass"] for call in calls} == {expected_pass_to_pass}
    retained = json.loads(cast(Path, calls[0]["raw_sample_path"]).read_text())
    assert retained["PASS_TO_PASS"] == list(expected_pass_to_pass)
    assert json.loads(retained["pass_to_pass"]) == list(expected_pass_to_pass)
    original = json.loads((_config.root / "runs" / _result.run_id / "instance.jsonl").read_text())
    assert original == instance.official_row()


def test_openlibrary_dynamic_year_reconciliation_only_rewrites_exact_legacy_node_ids() -> None:
    instance = _openlibrary_dynamic_year_instance(instance_id=OTHER_OPENLIBRARY_DYNAMIC_YEAR_INSTANCE_ID)
    raw = instance.official_row()
    raw["PASS_TO_PASS"] = [
        "test_fixed[2025-True]",
        f"{OPENLIBRARY_DYNAMIC_YEAR_PREFIX}[2025-True-extra]",
        f"{OPENLIBRARY_DYNAMIC_YEAR_PREFIX}_suffix[2025-True]",
        f"{OPENLIBRARY_DYNAMIC_YEAR_PREFIX}[2025-True]",
        f"{OPENLIBRARY_DYNAMIC_YEAR_PREFIX}[2026-False]",
        f"{OPENLIBRARY_DYNAMIC_YEAR_PREFIX}[2026-false]",
    ]
    instance = SweBenchProInstance.from_public_raw(raw)

    required_fail_to_pass, required_pass_to_pass = _evaluator_test_requirements(instance, evaluation_year=2042)

    assert required_fail_to_pass == instance.fail_to_pass
    assert required_pass_to_pass == (
        "test_fixed[2025-True]",
        f"{OPENLIBRARY_DYNAMIC_YEAR_PREFIX}[2025-True-extra]",
        f"{OPENLIBRARY_DYNAMIC_YEAR_PREFIX}_suffix[2025-True]",
        f"{OPENLIBRARY_DYNAMIC_YEAR_PREFIX}[2042-True]",
        f"{OPENLIBRARY_DYNAMIC_YEAR_PREFIX}[2043-False]",
        f"{OPENLIBRARY_DYNAMIC_YEAR_PREFIX}[2026-false]",
    )


def test_nodebb_legacy_test_name_reconciliation_only_rewrites_exact_names() -> None:
    raw = _instance().official_row()
    raw["FAIL_TO_PASS"] = [
        f'{NODEBB_DIGEST_TEST_PREFIX}"day',
        f'{NODEBB_DIGEST_TEST_PREFIX}"week',
        f'{NODEBB_DIGEST_TEST_PREFIX}"off',
        NODEBB_BIG_ARRAY_TEST,
        f'{NODEBB_DIGEST_TEST_PREFIX}"day-extra',
        f"{NODEBB_BIG_ARRAY_TEST} extra",
    ]
    instance = SweBenchProInstance.from_public_raw(raw)

    required_fail_to_pass, required_pass_to_pass = _evaluator_test_requirements(instance, evaluation_year=2042)

    assert required_fail_to_pass == (
        f'{NODEBB_DIGEST_TEST_PREFIX}"day"',
        f'{NODEBB_DIGEST_TEST_PREFIX}"week"',
        f'{NODEBB_DIGEST_TEST_PREFIX}"off"',
        f"{NODEBB_BIG_ARRAY_TEST} ",
        f'{NODEBB_DIGEST_TEST_PREFIX}"day-extra',
        f"{NODEBB_BIG_ARRAY_TEST} extra",
    )
    assert required_pass_to_pass == instance.pass_to_pass


def test_source595_reconciles_only_the_exact_legacy_cache_invalid_node_id() -> None:
    raw = _instance().official_row()
    raw["instance_id"] = SOURCE595_INSTANCE_ID
    raw["FAIL_TO_PASS"] = [
        SOURCE595_LEGACY_CACHE_INVALID_TEST,
        f"{SOURCE595_LEGACY_CACHE_INVALID_TEST}-extra",
    ]
    instance = SweBenchProInstance.from_public_raw(raw)

    required_fail_to_pass, required_pass_to_pass = _evaluator_test_requirements(instance, evaluation_year=2042)

    assert required_fail_to_pass == (
        SOURCE595_PARSED_CACHE_INVALID_TEST,
        f"{SOURCE595_LEGACY_CACHE_INVALID_TEST}-extra",
    )
    assert required_pass_to_pass == instance.pass_to_pass

    raw["instance_id"] = f"{SOURCE595_INSTANCE_ID}-other"
    other_instance = SweBenchProInstance.from_public_raw(raw)
    other_fail_to_pass, _other_pass_to_pass = _evaluator_test_requirements(other_instance, evaluation_year=2042)
    assert other_fail_to_pass == tuple(raw["FAIL_TO_PASS"])


def test_source621_reconciles_only_the_exact_legacy_go_subtest_names() -> None:
    raw = _instance().official_row()
    raw["instance_id"] = SOURCE621_INSTANCE_ID
    raw["FAIL_TO_PASS"] = [
        *SOURCE621_LEGACY_TEST_NAMES,
        f"{SOURCE621_LEGACY_TEST_NAMES[0]}-extra",
    ]
    instance = SweBenchProInstance.from_public_raw(raw)

    required_fail_to_pass, required_pass_to_pass = _evaluator_test_requirements(instance, evaluation_year=2042)

    assert required_fail_to_pass == (
        *(f'{name}"' for name in SOURCE621_LEGACY_TEST_NAMES),
        f"{SOURCE621_LEGACY_TEST_NAMES[0]}-extra",
    )
    assert required_pass_to_pass == instance.pass_to_pass

    raw["instance_id"] = f"{SOURCE621_INSTANCE_ID}-other"
    other_instance = SweBenchProInstance.from_public_raw(raw)
    other_fail_to_pass, _other_pass_to_pass = _evaluator_test_requirements(other_instance, evaluation_year=2042)
    assert other_fail_to_pass == tuple(raw["FAIL_TO_PASS"])


def test_runner_removes_a_task_image_imported_for_a_completed_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config, _result, observed = _run_with_fakes(
        tmp_path,
        monkeypatch,
        [],
        image_present=False,
    )

    calls = cast(list[tuple[tuple[str, ...], dict[str, object]]], observed["process_calls"])
    assert calls[-1][0] == ("docker", "image", "rm", _instance().task_image)


def test_runner_retries_a_transient_container_destroy_race_when_removing_an_imported_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _config, _result, observed = _run_with_fakes(
        tmp_path,
        monkeypatch,
        [],
        image_present=False,
        image_cleanup_conflicts=1,
    )

    calls = cast(list[tuple[tuple[str, ...], dict[str, object]]], observed["process_calls"])
    cleanup_calls = [call for call in calls if call[0][:3] == ("docker", "image", "rm")]
    assert len(cleanup_calls) == 2


def test_runner_surfaces_a_persistent_container_reference_after_five_cleanup_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: dict[str, object] = {}

    with pytest.raises(CommandFailed, match="image cleanup conflict"):
        _run_with_fakes(
            tmp_path,
            monkeypatch,
            [],
            image_present=False,
            image_cleanup_conflicts=5,
            observed=observed,
        )

    calls = cast(list[tuple[tuple[str, ...], dict[str, object]]], observed["process_calls"])
    cleanup_calls = [call for call in calls if call[0][:3] == ("docker", "image", "rm")]
    assert len(cleanup_calls) == 5


def test_runner_removes_an_imported_task_image_when_evaluation_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: dict[str, object] = {}

    with pytest.raises(RuntimeError, match="official evaluator failed"):
        _run_with_fakes(
            tmp_path,
            monkeypatch,
            [],
            image_present=False,
            evaluator_failure=RuntimeError("official evaluator failed"),
            observed=observed,
        )

    calls = cast(list[tuple[tuple[str, ...], dict[str, object]]], observed["process_calls"])
    assert calls[-1][0] == ("docker", "image", "rm", _instance().task_image)


def test_runner_surfaces_imported_task_image_cleanup_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(RuntimeError, match="image cleanup failed"):
        _run_with_fakes(
            tmp_path,
            monkeypatch,
            [],
            image_present=False,
            image_cleanup_failure=RuntimeError("image cleanup failed"),
        )


def test_runner_cleanup_failure_does_not_mask_the_evaluation_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(RuntimeError, match="official evaluator failed"):
        _run_with_fakes(
            tmp_path,
            monkeypatch,
            [],
            image_present=False,
            evaluator_failure=RuntimeError("official evaluator failed"),
            image_cleanup_failure=RuntimeError("image cleanup failed"),
        )


def test_runner_emits_phases_immediately_before_named_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[object] = []

    _run_with_fakes(tmp_path, monkeypatch, events)

    assert events == [
        RunPhase.PREPARING,
        "prepare",
        RunPhase.VALIDATING_GOLD,
        "gold",
        RunPhase.RUNNING_OFF,
        Arm.OFF,
        RunPhase.RUNNING_ON,
        Arm.ON,
        RunPhase.OFFICIAL_EVALUATION,
        "official",
        "official",
        RunPhase.GENERATING_REPORT,
    ]


def test_runner_preserves_falsey_phase_callback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class FalseyPhaseRecorder:
        def __init__(self) -> None:
            self.phases: list[RunPhase] = []

        def __bool__(self) -> bool:
            return False

        def __call__(self, phase: RunPhase) -> None:
            self.phases.append(phase)

    recorder = FalseyPhaseRecorder()

    _run_with_fakes(tmp_path, monkeypatch, [], on_phase=recorder)

    assert recorder.phases == list(RunPhase)


def test_runner_persists_strict_validated_report_json_without_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, result, _observed = _run_with_fakes(tmp_path, monkeypatch, [])

    report_data = json.loads((config.root / "runs" / result.run_id / "report.json").read_text())
    report = ReportBundle.model_validate(report_data, strict=True)
    assert report.model_dump(mode="json") == report_data
    retained = json.dumps(report_data, sort_keys=True)
    assert "api_key" not in retained
    assert "runner-secret-value" not in retained
