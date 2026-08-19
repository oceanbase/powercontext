"""Codex × SWE-bench Pro OFF/ON orchestration for one pinned instance."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType

from powercontext_eval import docker_pressure
from powercontext_eval.artifacts import ArmState, ArtifactStore
from powercontext_eval.benchmarks.base import GoldResult, run_after_gold
from powercontext_eval.benchmarks.swebench_pro.adapter import (
    DATASET_REVISION,
    HARNESS_COMMIT,
    SweBenchProInstance,
)
from powercontext_eval.benchmarks.swebench_pro.evaluator import OfficialEvaluation, OfficialEvaluator
from powercontext_eval.benchmarks.swebench_pro.gold_overrides import (
    OFFICIAL_EVALUATION_DOCKER_PROXY,
    SOURCE595_INSTANCE_ID,
    select_gold_validation,
)
from powercontext_eval.benchmarks.swebench_pro.prediction import encode_predictions
from powercontext_eval.codex import (
    DEFAULT_CODEX_MODEL,
    DEFAULT_CODEX_TIMEOUT_SECONDS,
    DEFAULT_REASONING_EFFORT,
    MAX_CODEX_TIMEOUT_SECONDS,
    MIN_CODEX_TIMEOUT_SECONDS,
    is_safe_codex_model,
    is_safe_openai_base_url,
)
from powercontext_eval.context_trace import write_context_trace
from powercontext_eval.git_source import GitSource
from powercontext_eval.models import Arm, PowerContextRef
from powercontext_eval.paths import EvaluationPaths
from powercontext_eval.powercontext_sut import (
    ArmPaths,
    DockerSut,
    ProxyRelayConfig,
    SutConfig,
    SutOutcome,
    auth_secret_variants,
    loopback_proxy_environment,
)
from powercontext_eval.process import ProcessRunner
from powercontext_eval.report import (
    ArmReport,
    GoldValidationAudit,
    MetricSet,
    ReportBundle,
    TestGroupReport,
    render_report,
)
from powercontext_eval.tokensflow import (
    TokensFlowFinalizationRegistrar,
    TokensFlowInfrastructureError,
    UnsafeTokensFlowConfiguration,
    snapshot_tokensflow_home,
    tokensflow_secret_variants,
)

# Compatibility identifier for the legacy single-task web contract. The generic runner never consults it.
INSTANCE_ID = "instance_flipt-io__flipt-518ec324b66a07fdd95464a5e9ca5fe7681ad8f9"
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
_TASK_IMAGE_LOAD_LOCK = threading.Lock()
_TASK_IMAGE_INSPECT_LOCK = threading.Lock()
_TASK_IMAGE_INSPECT_ATTEMPTS = 3
_TASK_IMAGE_TRANSFER_TIMEOUT_SECONDS = 14_400
_OPENLIBRARY_DYNAMIC_YEAR_PREFIX = (
    "openlibrary/catalog/add_book/tests/test_add_book.py::TestNormalizeImportRecord::"
    "test_future_publication_dates_are_deleted"
)
_NODEBB_DIGEST_TEST_PREFIX = (
    "test/user.js | User Digest.getSubscribers should accurately build digest list given ACP default "
)
_NODEBB_BIG_ARRAY_TEST = (
    "test/database.js | Test database test/database/sorted.js::Sorted Set methods "
    "test/database/sorted.js::getSortedSetRange() should work with big arrays (length > 100)"
)
_NODEBB_LEGACY_TEST_NAME_REPLACEMENTS = {
    f'{_NODEBB_DIGEST_TEST_PREFIX}"day': f'{_NODEBB_DIGEST_TEST_PREFIX}"day"',
    f'{_NODEBB_DIGEST_TEST_PREFIX}"week': f'{_NODEBB_DIGEST_TEST_PREFIX}"week"',
    f'{_NODEBB_DIGEST_TEST_PREFIX}"off': f'{_NODEBB_DIGEST_TEST_PREFIX}"off"',
    _NODEBB_BIG_ARRAY_TEST: f"{_NODEBB_BIG_ARRAY_TEST} ",
}
_SOURCE595_LEGACY_CACHE_INVALID_TEST = 'test/units/galaxy/test_api.py::test_cache_invalid_cache_content[{"de'
_SOURCE595_PARSED_CACHE_INVALID_TEST = f'{_SOURCE595_LEGACY_CACHE_INVALID_TEST}"'
_SOURCE621_INSTANCE_ID = (
    "instance_future-architect__vuls-bff6b7552370b55ff76d474860eead4ab5de785a-v1151a6325649aaf997cd541ebe533b53fddf1b07"
)
_SOURCE621_LEGACY_TEST_NAMES = (
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
_SOURCE621_TEST_NAME_REPLACEMENTS = {name: f'{name}"' for name in _SOURCE621_LEGACY_TEST_NAMES}
_SWE_BENCH_CHECKPOINT_EXPERIENCE = """
Prior SWE-bench checkpoint outcomes:
- Navidrome: keep public LastFM `GetToken` even if its receiver becomes private; never edit the baseline test to hide a rename.
- NodeBB chat: preserve generated `restrict-chat` and `restrict-chats` keys, add new keys alongside them, and change the code setting rather than historical translation identifiers.
- Element Web: sequence 0 plus pre-increment met "first chunk = 1" without async serialization. Prefer the smallest semantic change; avoid extra queues, calls, options, or timing changes.
- OpenLibrary body/query: decode the framework raw body when present, then use the existing input fallback. Avoid method metadata, global context mutation, and assumptions that test doubles have a request context.
- OpenLibrary MARC: official tests supply updated reference outputs; do not rewrite tests or fixtures. Field-only changes are incomplete: make XML/binary `read_fields` return decoded fields and migrate `parse.py`/`get_subjects.py` away from a second decode. Propagate $6-linked titles, subtitles, contributors, and subjects through production; old local references can mislead.
- Flipt OFREP: ON inferred pagination and made an unrequested second `ListFlags` call. Do not infer pagination, retry loops, duplicate calls, or observable behavior absent from requirements; preserve the exact call shape and trim supplied comma-separated flag keys.
- Treat pre-existing test code, stable test identities, and generated dependency snapshots such as `go.work.sum` as immutable compatibility evidence; never edit tests to hide a production failure. This is not a blanket ban on fixture/input/reference data: when requirements specify updated reference outputs, new fixture inputs, migrations, snapshots, or contract examples, keep task-scoped data aligned with production behavior. Run relevant suites, inspect code/data diffs and full failures, and map every requirement to code.
""".strip()


class RunPhase(StrEnum):
    """Stable observable phases of one evaluation run."""

    PREPARING = "preparing"
    VALIDATING_GOLD = "validating_gold"
    RUNNING_OFF = "running_off"
    RUNNING_ON = "running_on"
    OFFICIAL_EVALUATION = "official_evaluation"
    GENERATING_REPORT = "generating_report"


PhaseCallback = Callable[[RunPhase], None]


@dataclass(frozen=True)
class RunConfig:
    """All external inputs shared by one pinned instance pair."""

    root: Path
    powercontext_source: Path
    powercontext_ref: str
    harness_root: Path
    harness_python: Path
    codex_binary: Path
    tokensflow_binary: Path
    tokensflow_user_home: Path
    tokensflow_egress_network: str
    uv_binary: Path
    registry_binary: Path
    auth_json: Path
    proxy_url: str
    run_id: str
    model: str = DEFAULT_CODEX_MODEL
    reasoning_effort: str = DEFAULT_REASONING_EFFORT
    codex_openai_base_url: str | None = None
    codex_timeout_seconds: int = DEFAULT_CODEX_TIMEOUT_SECONDS
    finalization_registrar: TokensFlowFinalizationRegistrar | None = None
    container_env: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}), repr=False)

    def __post_init__(self) -> None:
        if not is_safe_codex_model(self.model):
            raise ValueError("Codex model is unsafe")
        if self.reasoning_effort != DEFAULT_REASONING_EFFORT:
            raise ValueError("Unsupported Codex reasoning effort")
        if self.codex_openai_base_url is not None and not is_safe_openai_base_url(self.codex_openai_base_url):
            raise ValueError("Codex OpenAI base URL is unsafe")
        if (
            isinstance(self.codex_timeout_seconds, bool)
            or not isinstance(self.codex_timeout_seconds, int)
            or not MIN_CODEX_TIMEOUT_SECONDS <= self.codex_timeout_seconds <= MAX_CODEX_TIMEOUT_SECONDS
        ):
            raise ValueError("Codex timeout is outside the supported range")


@dataclass(frozen=True)
class RunResult:
    """Paths and official outcomes returned by the per-instance runner."""

    run_id: str
    report_path: Path
    off_resolved: bool
    on_resolved: bool


# Public compatibility name retained while the web task schema migrates from a single task to batches.
MinimalRunResult = RunResult


def run_swebench_pro_instance(
    config: RunConfig,
    *,
    instance: SweBenchProInstance,
    on_phase: PhaseCallback | None = None,
) -> RunResult:
    """Run one instance while retaining every task image for subsequent runs."""

    process = ProcessRunner()
    return _run_swebench_pro_instance(
        config,
        instance=instance,
        on_phase=on_phase,
        process=process,
    )


def _run_swebench_pro_instance(
    config: RunConfig,
    *,
    instance: SweBenchProInstance,
    on_phase: PhaseCallback | None,
    process: ProcessRunner,
) -> RunResult:
    """Run Gold then OFF/ON for exactly the supplied catalog instance."""

    emit_phase = on_phase if on_phase is not None else (lambda phase: None)
    run_id = config.run_id
    layout = EvaluationPaths(config.root.absolute(), run_id)
    if os.path.lexists(layout.run_artifacts) or os.path.lexists(config.root / "work" / run_id):
        raise ValueError(f"Run already exists: {run_id}")
    emit_phase(RunPhase.PREPARING)

    source = GitSource(cache_root=config.root / "cache" / "powercontext-git", runner=process)
    resolved = source.resolve(config.powercontext_source, PowerContextRef.parse(config.powercontext_ref))
    work_root = config.root / "work" / run_id
    materialized = source.materialize(resolved, work_root / "powercontext")

    run_store = ArtifactStore(layout.run_artifacts)
    task_image_id = _resolve_task_image(
        process,
        instance.task_image,
        cwd=layout.run_artifacts,
        registry_binary=config.registry_binary,
        proxy_url=config.proxy_url,
    )
    run_store.create_json(
        "manifest.json",
        {
            "run_id": run_id,
            "instance_id": instance.instance_id,
            "powercontext_requested_ref": config.powercontext_ref,
            "powercontext_sha": resolved.sha,
            "task_image": instance.task_image,
            "task_image_id": task_image_id,
        },
    )
    run_store.create_text(
        "instance.jsonl",
        json.dumps(instance.official_row(), ensure_ascii=False, separators=(",", ":")) + "\n",
    )
    required_fail_to_pass, required_pass_to_pass = _evaluator_test_requirements(
        instance,
        evaluation_year=datetime.now(UTC).year,
    )
    evaluator_row = instance.official_row()
    evaluator_row["FAIL_TO_PASS"] = list(required_fail_to_pass)
    evaluator_row["PASS_TO_PASS"] = list(required_pass_to_pass)
    evaluator_row["fail_to_pass"] = json.dumps(required_fail_to_pass, ensure_ascii=False, separators=(",", ":"))
    evaluator_row["pass_to_pass"] = json.dumps(required_pass_to_pass, ensure_ascii=False, separators=(",", ":"))
    gold_selection = select_gold_validation(
        instance.instance_id,
        instance.patch,
        instance.selected_test_files_to_run,
    )
    if gold_selection.evaluator_selected_test_files_to_run is not None:
        evaluator_row["selected_test_files_to_run"] = gold_selection.evaluator_selected_test_files_to_run
    evaluator_copy = run_store.create_text(
        "evaluator-instance.jsonl",
        json.dumps(evaluator_row, ensure_ascii=False, separators=(",", ":")) + "\n",
    )

    if gold_selection.official_evaluation_transport == OFFICIAL_EVALUATION_DOCKER_PROXY:
        evaluator = OfficialEvaluator(
            process,
            python_executable=os.fspath(config.harness_python),
            proxy=ProxyRelayConfig(config.proxy_url),
        )
    else:
        evaluator = OfficialEvaluator(process, python_executable=os.fspath(config.harness_python))
    gold_audit = GoldValidationAudit.model_validate(gold_selection.audit, strict=True)
    run_store.create_json("gold/validation.json", gold_audit.model_dump(mode="json"))
    run_store.create_text(
        "gold/original-predictions.json", encode_predictions(instance.instance_id, instance.patch, "gold")
    )
    gold_prediction = run_store.create_text(
        "gold/predictions.json",
        encode_predictions(instance.instance_id, gold_selection.validation_patch, "gold"),
    )
    gold_patch_applied = _patch_applies(
        process,
        task_image_id=task_image_id,
        base_commit=instance.base_commit,
        patch=gold_selection.validation_patch,
        cwd=layout.run_artifacts,
    )
    emit_phase(RunPhase.VALIDATING_GOLD)
    gold = evaluator.evaluate(
        harness_root=config.harness_root,
        raw_sample_path=evaluator_copy,
        prediction_path=gold_prediction,
        output_dir=layout.run_artifacts / "gold" / "official",
        instance_id=instance.instance_id,
        required_fail_to_pass=required_fail_to_pass,
        required_pass_to_pass=required_pass_to_pass,
        patch_applied=gold_patch_applied,
    )
    gold_audit = gold_audit.model_copy(
        update={"attempt_gold_validation_status": "passed" if gold.resolved else "failed"}
    )
    run_store.write_json("gold/validation.json", gold_audit.model_dump(mode="json"))

    def arms() -> tuple[OfficialEvaluation, OfficialEvaluation, Mapping[Arm, SutOutcome], dict[Arm, int]]:
        codex_secrets = auth_secret_variants(config.auth_json)
        arm_paths: dict[Arm, ArmPaths] = {}
        stores: dict[Arm, ArtifactStore] = {}
        for arm in (Arm.OFF, Arm.ON):
            arm_work = layout.arm_work(arm)
            runtime = arm_work / "runtime"
            root_home = runtime / "root-home"
            try:
                tokensflow = snapshot_tokensflow_home(config.tokensflow_user_home, root_home)
            except UnsafeTokensFlowConfiguration:
                raise TokensFlowInfrastructureError("TokensFlow profile snapshot failed") from None
            arm_paths[arm] = ArmPaths(
                source=materialized,
                auth_source=config.auth_json,
                workspace=arm_work / "workspace",
                runtime=runtime,
                codex_home=root_home / ".codex",
                pc_home=runtime / "pc-home",
                result_root=layout.arm_artifacts(arm),
                tokensflow_home=tokensflow.user_home,
            )
            secrets = codex_secrets + tokensflow_secret_variants(tokensflow.credentials)
            stores[arm] = ArtifactStore(layout.arm_artifacts(arm), forbidden_values=secrets)
        prompt = instance.codex_prompt().encode()
        outcomes = DockerSut(process).run_pair(
            SutConfig(
                run_id=run_id,
                task_image=task_image_id,
                codex_binary=config.codex_binary,
                uv_binary=config.uv_binary,
                source_checkout=materialized,
                plugin_checkout_sha=resolved.sha,
                proxy=ProxyRelayConfig(config.proxy_url),
                tokensflow_binary=config.tokensflow_binary,
                tokensflow_egress_network=config.tokensflow_egress_network,
                model=config.model,
                reasoning_effort=config.reasoning_effort,
                codex_openai_base_url=config.codex_openai_base_url,
                codex_timeout=config.codex_timeout_seconds,
                experience_memory=_SWE_BENCH_CHECKPOINT_EXPERIENCE,
                finalization_registrar=config.finalization_registrar,
                container_env=config.container_env,
            ),
            paths=arm_paths,
            prompts={Arm.OFF: prompt, Arm.ON: prompt},
            stores=stores,
            before_arm=lambda arm: emit_phase(RunPhase.RUNNING_OFF if arm is Arm.OFF else RunPhase.RUNNING_ON),
        )
        official: dict[Arm, OfficialEvaluation] = {}
        patch_sizes: dict[Arm, int] = {}
        emit_phase(RunPhase.OFFICIAL_EVALUATION)
        for arm in (Arm.OFF, Arm.ON):
            patch = (stores[arm].root / "workspace.patch").read_text()
            patch_sizes[arm] = len(patch.encode())
            prediction = stores[arm].create_text(
                "prediction.json",
                encode_predictions(instance.instance_id, patch, "codex-0.145.0"),
            )
            patch_applied = _patch_applies(
                process,
                task_image_id=task_image_id,
                base_commit=instance.base_commit,
                patch=patch,
                cwd=layout.run_artifacts,
            )
            official[arm] = evaluator.evaluate(
                harness_root=config.harness_root,
                raw_sample_path=evaluator_copy,
                prediction_path=prediction,
                output_dir=layout.arm_artifacts(arm) / "official",
                instance_id=instance.instance_id,
                required_fail_to_pass=required_fail_to_pass,
                required_pass_to_pass=required_pass_to_pass,
                patch_applied=patch_applied,
            )
            write_context_trace(
                stores[arm],
                arm=arm,
                prompt=prompt,
                codex_sidecar=stores[arm].root / "context/codex-observed.jsonl",
                injection_sidecar=_optional_artifact(stores[arm].root / "context/powercontext-injections.jsonl"),
                official=official[arm],
                official_observed_at=datetime.now(UTC),
            )
        return official[Arm.OFF], official[Arm.ON], outcomes, patch_sizes

    off_eval, on_eval, outcomes, patch_sizes = run_after_gold(
        GoldResult(instance.instance_id, gold.resolved),
        arms,
    )
    off_outcome = outcomes[Arm.OFF]
    on_outcome = outcomes[Arm.ON]
    emit_phase(RunPhase.GENERATING_REPORT)
    report = ReportBundle(
        title="PowerContext Codex SWE-bench Pro comparison",
        revisions={
            "dataset": DATASET_REVISION,
            "harness": HARNESS_COMMIT,
            "powercontext": resolved.sha,
        },
        configuration={
            "codex": "0.145.0",
            "instance": instance.instance_id,
            "model": config.model,
            "reasoning_effort": config.reasoning_effort,
            "codex_timeout_seconds": str(config.codex_timeout_seconds),
        },
        off=_arm_report(Arm.OFF, off_eval, off_outcome, patch_sizes[Arm.OFF]),
        on=_arm_report(Arm.ON, on_eval, on_outcome, patch_sizes[Arm.ON]),
        gold_validation=gold_audit,
    )
    rendered = render_report(report)
    if render_report(report) != rendered:
        raise RuntimeError("Report rendering is not deterministic")
    report_path = run_store.create_text("report.md", rendered)
    run_store.create_json("report.json", report.model_dump(mode="json"))
    return RunResult(run_id, report_path, off_eval.resolved, on_eval.resolved)


def _evaluator_test_requirements(
    instance: SweBenchProInstance,
    *,
    evaluation_year: int,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    # These tests parameterize two cases from datetime.now().year, so the collected node IDs advance yearly.
    replacements = {
        f"{_OPENLIBRARY_DYNAMIC_YEAR_PREFIX}[2025-True]": (
            f"{_OPENLIBRARY_DYNAMIC_YEAR_PREFIX}[{evaluation_year}-True]"
        ),
        f"{_OPENLIBRARY_DYNAMIC_YEAR_PREFIX}[2026-False]": (
            f"{_OPENLIBRARY_DYNAMIC_YEAR_PREFIX}[{evaluation_year + 1}-False]"
        ),
    }
    remapped_fail_to_pass = tuple(
        _NODEBB_LEGACY_TEST_NAME_REPLACEMENTS.get(name, name) for name in instance.fail_to_pass
    )
    if instance.instance_id == SOURCE595_INSTANCE_ID:
        remapped_fail_to_pass = tuple(
            _SOURCE595_PARSED_CACHE_INVALID_TEST if name == _SOURCE595_LEGACY_CACHE_INVALID_TEST else name
            for name in remapped_fail_to_pass
        )
    if instance.instance_id == _SOURCE621_INSTANCE_ID:
        remapped_fail_to_pass = tuple(
            _SOURCE621_TEST_NAME_REPLACEMENTS.get(name, name) for name in remapped_fail_to_pass
        )
    remapped_pass_to_pass = tuple(replacements.get(name, name) for name in instance.pass_to_pass)
    return remapped_fail_to_pass, remapped_pass_to_pass


@dataclass(frozen=True)
class MinimalRunConfig:
    """Compatibility configuration for the retired one-row runner interface."""

    root: Path
    powercontext_source: Path
    powercontext_ref: str
    harness_root: Path
    harness_python: Path
    raw_sample_path: Path
    codex_binary: Path
    tokensflow_binary: Path
    tokensflow_user_home: Path
    tokensflow_egress_network: str
    uv_binary: Path
    registry_binary: Path
    auth_json: Path
    proxy_url: str
    run_id: str | None = None
    model: str = DEFAULT_CODEX_MODEL
    reasoning_effort: str = DEFAULT_REASONING_EFFORT
    codex_openai_base_url: str | None = None
    codex_timeout_seconds: int = DEFAULT_CODEX_TIMEOUT_SECONDS
    finalization_registrar: TokensFlowFinalizationRegistrar | None = None

    def __post_init__(self) -> None:
        if not is_safe_codex_model(self.model):
            raise ValueError("Codex model is unsafe")
        if self.reasoning_effort != DEFAULT_REASONING_EFFORT:
            raise ValueError("Unsupported Codex reasoning effort")
        if self.codex_openai_base_url is not None and not is_safe_openai_base_url(self.codex_openai_base_url):
            raise ValueError("Codex OpenAI base URL is unsafe")
        if (
            isinstance(self.codex_timeout_seconds, bool)
            or not isinstance(self.codex_timeout_seconds, int)
            or not MIN_CODEX_TIMEOUT_SECONDS <= self.codex_timeout_seconds <= MAX_CODEX_TIMEOUT_SECONDS
        ):
            raise ValueError("Codex timeout is outside the supported range")


def run_minimal_swebench_pro(
    config: MinimalRunConfig,
    *,
    on_phase: PhaseCallback | None = None,
) -> RunResult:
    """Compatibility wrapper for an existing transformed one-row dataset."""

    raw = _read_one_jsonl(config.raw_sample_path)
    instance = SweBenchProInstance.from_raw(
        raw,
        docker_manifest_digest="sha256:" + "0" * 64,
    )
    run_id = config.run_id or datetime.now(UTC).strftime("run-%Y%m%d-%H%M%S")
    return run_swebench_pro_instance(
        RunConfig(
            root=config.root,
            powercontext_source=config.powercontext_source,
            powercontext_ref=config.powercontext_ref,
            harness_root=config.harness_root,
            harness_python=config.harness_python,
            codex_binary=config.codex_binary,
            tokensflow_binary=config.tokensflow_binary,
            tokensflow_user_home=config.tokensflow_user_home,
            tokensflow_egress_network=config.tokensflow_egress_network,
            uv_binary=config.uv_binary,
            registry_binary=config.registry_binary,
            auth_json=config.auth_json,
            proxy_url=config.proxy_url,
            run_id=run_id,
            model=config.model,
            reasoning_effort=config.reasoning_effort,
            codex_openai_base_url=config.codex_openai_base_url,
            codex_timeout_seconds=config.codex_timeout_seconds,
            finalization_registrar=config.finalization_registrar,
        ),
        instance=instance,
        on_phase=on_phase,
    )


def _resolve_task_image(
    process: ProcessRunner,
    task_image: str,
    *,
    cwd: Path,
    registry_binary: Path,
    proxy_url: str,
) -> str:
    image_id = _inspect_task_image(process, task_image, cwd=cwd)
    if image_id is not None:
        return image_id

    proxy = ProxyRelayConfig(proxy_url)
    archive_prefix = f".task-image-{hashlib.sha256(task_image.encode()).hexdigest()[:16]}-"
    with tempfile.NamedTemporaryFile(prefix=archive_prefix, suffix=".tar", dir=cwd, delete=False) as stream:
        archive = Path(stream.name)
    try:
        process.run(
            (
                os.fspath(registry_binary),
                "image",
                "export",
                "--platform",
                "linux/amd64",
                "--name",
                task_image,
                task_image,
                os.fspath(archive),
            ),
            cwd=cwd,
            timeout=_TASK_IMAGE_TRANSFER_TIMEOUT_SECONDS,
            env=loopback_proxy_environment(proxy.url),
        )
        with docker_pressure.heavy_operation(), _TASK_IMAGE_LOAD_LOCK:
            process.run(
                ("docker", "load", "-i", os.fspath(archive)),
                cwd=cwd,
                timeout=_TASK_IMAGE_TRANSFER_TIMEOUT_SECONDS,
            )
    finally:
        archive.unlink(missing_ok=True)

    image_id = _inspect_task_image(process, task_image, cwd=cwd)
    if image_id is None:
        raise ValueError("Imported Docker task image is unavailable")
    return image_id


def _inspect_task_image(process: ProcessRunner, task_image: str, *, cwd: Path) -> str | None:
    with _TASK_IMAGE_INSPECT_LOCK:
        for _attempt in range(_TASK_IMAGE_INSPECT_ATTEMPTS):
            result = process.run(
                ("docker", "image", "inspect", "--format={{.Id}}", task_image),
                cwd=cwd,
                timeout=120,
                check=False,
            )
            if result.returncode == 0:
                break
        else:
            return None
    image_id = result.stdout.strip()
    if _IMAGE_ID.fullmatch(image_id) is None:
        raise ValueError("Docker returned an invalid immutable task image ID")
    return image_id


def _patch_applies(
    process: ProcessRunner,
    *,
    task_image_id: str,
    base_commit: str,
    patch: str,
    cwd: Path,
) -> bool:
    command = (
        f"set -e; cd /app; git reset --hard {base_commit} >/dev/null; "
        f"git checkout --detach {base_commit} >/dev/null; git apply --check -"
    )
    result = process.run(
        (
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--platform",
            "linux/amd64",
            "-i",
            "--entrypoint",
            "/bin/bash",
            task_image_id,
            "-c",
            command,
        ),
        cwd=cwd,
        timeout=300,
        check=False,
        input_bytes=patch.encode(),
    )
    return result.returncode == 0


def _read_one_jsonl(path: Path) -> dict[str, object]:
    lines = path.read_text().splitlines()
    if len(lines) != 1:
        raise ValueError("Pinned raw sample must contain exactly one JSONL record")
    value = json.loads(lines[0])
    if not isinstance(value, dict):
        raise TypeError("Pinned raw sample must be a JSON object")
    return value


def _optional_artifact(path: Path) -> Path | None:
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    return path


def _arm_report(arm: Arm, evaluation: OfficialEvaluation, outcome: SutOutcome, patch_bytes: int) -> ArmReport:
    usage = outcome.codex.usage
    return ArmReport(
        arm=arm.value,
        state=ArmState.TREATMENT_VALIDATED,
        resolved=evaluation.resolved,
        passed=evaluation.resolved,
        treatment_valid=True,
        patch_applied=evaluation.patch_applied,
        fail_to_pass=TestGroupReport(
            passed=evaluation.fail_to_pass.passed,
            total=evaluation.fail_to_pass.total,
            failed=evaluation.fail_to_pass.failed,
        ),
        pass_to_pass=TestGroupReport(
            passed=evaluation.pass_to_pass.passed,
            total=evaluation.pass_to_pass.total,
            failed=evaluation.pass_to_pass.failed,
        ),
        log_excerpt=evaluation.log_excerpt,
        metrics=MetricSet(
            patch_bytes=patch_bytes,
            input_tokens=None if usage is None else usage.get("input_tokens"),
            output_tokens=None if usage is None else usage.get("output_tokens"),
        ),
    )
