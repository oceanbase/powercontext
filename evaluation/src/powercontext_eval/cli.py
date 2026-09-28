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

"""Command-line entry point for the evaluation runner."""

from __future__ import annotations

import json
import os
import signal
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING, Annotated, Any, Protocol, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import typer
from pydantic import ValidationError

from powercontext_eval.benchmarks.longmemeval_v2.adapter import PowerContextMemoryAdapterError
from powercontext_eval.benchmarks.longmemeval_v2.arms import DEFAULT_EXPERIMENT_ARM_ID, ExperimentArmError
from powercontext_eval.benchmarks.longmemeval_v2.catalog import (
    LongMemEvalV2CatalogError,
    LongMemEvalV2EnvironmentError,
    LongMemEvalV2InputError,
)
from powercontext_eval.benchmarks.longmemeval_v2.costs import (
    CostPolicyError,
    ModelPricePolicy,
    parse_cost_policy,
)
from powercontext_eval.benchmarks.longmemeval_v2.prepare_smoke import (
    DEFAULT_PROCESSOR_MODEL,
    PrepareSmokeError,
    prepare_reader_inputs_smoke,
)
from powercontext_eval.benchmarks.longmemeval_v2.reader_smoke import (
    DEFAULT_ANTHROPIC_BASE_URL_ENV,
    ReaderSmokeError,
    run_reader_smoke,
)
from powercontext_eval.benchmarks.longmemeval_v2.replay_score import ReplayScoreError, replay_score_smoke
from powercontext_eval.benchmarks.longmemeval_v2.report import ReportError, build_report
from powercontext_eval.benchmarks.longmemeval_v2.retrieval_smoke import RetrievalSmokeError, run_retrieval_smoke
from powercontext_eval.benchmarks.longmemeval_v2.run_smoke import RunSmokeError, run_smoke
from powercontext_eval.benchmarks.longmemeval_v2.score_smoke import (
    DEFAULT_DEEPSEEK_BASE_URL as SCORE_DEFAULT_DEEPSEEK_BASE_URL,
)
from powercontext_eval.benchmarks.longmemeval_v2.score_smoke import (
    DEFAULT_DEEPSEEK_MODEL as SCORE_DEFAULT_DEEPSEEK_MODEL,
)
from powercontext_eval.benchmarks.longmemeval_v2.score_smoke import (
    DEFAULT_DEEPSEEK_TOKEN_ENV as SCORE_DEFAULT_DEEPSEEK_TOKEN_ENV,
)
from powercontext_eval.benchmarks.longmemeval_v2.score_smoke import (
    ScoreSmokeError,
    run_score_smoke,
)
from powercontext_eval.benchmarks.longmemeval_v2.smoke import prepare_smoke_run
from powercontext_eval.benchmarks.swebench_pro.catalog import PUBLIC_V2_TASK_SET, SweBenchProCatalog, TaskSet
from powercontext_eval.codex import DEFAULT_CODEX_MODEL, DEFAULT_REASONING_EFFORT
from powercontext_eval.models import TreatmentMode

if TYPE_CHECKING:
    from powercontext_eval.web.config import WebConfig

app = typer.Typer(no_args_is_help=True, help="PowerContext evaluation runner.")
swebench_pro_app = typer.Typer(no_args_is_help=True, help="Pinned SWE-bench Pro evaluation.")
longmemeval_v2_app = typer.Typer(no_args_is_help=True, help="Pinned LongMemEval-V2 evaluation.")
app.add_typer(swebench_pro_app, name="swebench-pro")
app.add_typer(longmemeval_v2_app, name="longmemeval-v2")
DEFAULT_DOCKER_NETWORK_POOL = "172.30.0.0/15"


@app.callback()
def root() -> None:
    """Run reproducible PowerContext evaluations."""


class _Stoppable(Protocol):
    def stop(self) -> None: ...


def run_codex_contract_smoke(**kwargs: Any) -> Any:
    """Load the platform-specific contract runner only when its command is used."""

    from powercontext_eval.powercontext_sut import run_codex_contract_smoke as implementation

    return implementation(**kwargs)


def run_swebench_pro_instance(*args: Any, **kwargs: Any) -> Any:
    """Load the platform-specific SWE-bench runner only when its command is used."""

    from powercontext_eval.runner import run_swebench_pro_instance as implementation

    return implementation(*args, **kwargs)


def _parse_price_policy_option(value: str | None, *, label: str) -> ModelPricePolicy | None:
    """Parse an explicitly passed JSON price policy, or keep costs unconfigured as null."""

    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise typer.BadParameter(f"{label} must be a JSON object: {error}") from None
    try:
        return parse_cost_policy(parsed, label=label)
    except CostPolicyError as error:
        raise typer.BadParameter(str(error)) from None


def _request_worker_stop(worker: _Stoppable, _signum: int, _frame: FrameType | None) -> None:
    """Request that a worker exit after its current task finishes."""
    worker.stop()


def _web_config(root_path: Path | None) -> WebConfig:
    from powercontext_eval.web.config import WebConfig

    try:
        environ = dict(os.environ)
        if root_path is not None:
            environ["POWERCONTEXT_EVAL_ROOT"] = os.fspath(root_path)
        return WebConfig.from_environment(environ)
    except (KeyError, TypeError, ValueError, ValidationError):
        raise typer.BadParameter("Invalid evaluation configuration.", param_hint="--root") from None


@contextmanager
def _worker_signal_handlers(worker: _Stoppable) -> Iterator[None]:
    previous: dict[signal.Signals, Any] = {}
    stop_requested = False

    def handler(signum: int, frame: FrameType | None) -> None:
        nonlocal stop_requested
        if stop_requested:
            return
        stop_requested = True
        _request_worker_stop(worker, signum, frame)

    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, handler)
        yield
    finally:
        for signum, prior in previous.items():
            signal.signal(signum, prior)


@app.command("web")
def web(root_path: Annotated[Path | None, typer.Option("--root")] = None) -> None:
    """Serve the evaluation console API and frontend."""
    import uvicorn

    from powercontext_eval.web.api import create_app

    config = _web_config(root_path)
    uvicorn.run(create_app(config), host=config.host, port=config.port)


@app.command("worker")
def worker(root_path: Annotated[Path | None, typer.Option("--root")] = None) -> None:
    """Run queued task pairs at configured parallelism until shutdown is requested."""
    from powercontext_eval.web.store import TaskStore
    from powercontext_eval.web.usage import CodexUsageProbe
    from powercontext_eval.web.worker import EvaluationWorker

    config = _web_config(root_path)
    store = TaskStore(
        config.database_path,
        lease_duration=timedelta(seconds=config.lease_seconds),
        max_attempts=config.max_attempts,
    )
    store.initialize()
    service = EvaluationWorker(
        config,
        store,
        usage_probe=CodexUsageProbe(
            codex_binary=config.codex_binary,
            auth_json=config.auth_json,
            codex_config=config.codex_config,
            proxy_url=config.proxy_url,
            timeout_seconds=config.usage_probe_timeout_seconds,
        ),
    )
    with _worker_signal_handlers(service):
        service.run_forever()


@app.command("codex-contract-smoke")
def codex_contract_smoke(
    run_root: str = typer.Option(...),
    task_image: str = typer.Option(...),
    codex_bin: str = typer.Option(...),
    tokensflow_bin: str = typer.Option(...),
    tokensflow_user_home: str = typer.Option(...),
    tokensflow_egress_network: str = typer.Option(...),
    uv_bin: str = typer.Option(...),
    powercontext_source: str = typer.Option(...),
    powercontext_sha: str = typer.Option(...),
    auth_json: str = typer.Option(...),
    proxy_url: str = typer.Option(...),
    prompt: str = typer.Option("Reply with exactly OK."),
) -> None:
    """Run OFF/ON identity, daemon, bounded-drain, and Codex contract checks."""

    outcome = run_codex_contract_smoke(
        run_root=run_root,
        task_image=task_image,
        codex_bin=codex_bin,
        tokensflow_bin=tokensflow_bin,
        tokensflow_user_home=tokensflow_user_home,
        tokensflow_egress_network=tokensflow_egress_network,
        uv_bin=uv_bin,
        powercontext_source=powercontext_source,
        powercontext_sha=powercontext_sha,
        auth_json=auth_json,
        proxy_url=proxy_url,
        prompt=prompt,
    )
    typer.echo(json.dumps(outcome, ensure_ascii=False, sort_keys=True))


@longmemeval_v2_app.command("smoke")
def longmemeval_v2_smoke(
    data_root: Annotated[Path, typer.Option("--data-root")],
    dataset_lock: Annotated[Path, typer.Option("--dataset-lock")],
    harness_root: Annotated[Path, typer.Option("--harness-root")],
    smoke_manifest: Annotated[Path, typer.Option("--smoke-manifest")],
    output_dir: Annotated[Path, typer.Option("--output-dir")],
) -> None:
    """Validate fixed LongMemEval-V2 inputs and write smoke artifacts without calling a model."""

    try:
        prepared = prepare_smoke_run(
            data_root=data_root,
            dataset_lock=dataset_lock,
            harness_root=harness_root,
            smoke_manifest=smoke_manifest,
            output_dir=output_dir,
        )
    except LongMemEvalV2InputError as error:
        raise typer.BadParameter(str(error)) from None
    except LongMemEvalV2EnvironmentError as error:
        typer.echo(f"LongMemEval-V2 smoke failed: {error}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(
        json.dumps(
            {
                "classification": "smoke-subset",
                "manifest": str(prepared.manifest_path),
                "subset": str(prepared.subset_path),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


@longmemeval_v2_app.command("retrieval-smoke")
def longmemeval_v2_retrieval_smoke(
    data_root: Annotated[Path, typer.Option("--data-root")],
    dataset_lock: Annotated[Path, typer.Option("--dataset-lock")],
    harness_root: Annotated[Path, typer.Option("--harness-root")],
    smoke_manifest: Annotated[Path, typer.Option("--smoke-manifest")],
    output_dir: Annotated[Path, typer.Option("--output-dir")],
    run_id: Annotated[str, typer.Option("--run-id")],
    powercontext_revision: Annotated[str, typer.Option("--powercontext-revision")],
    integration_revision: Annotated[str, typer.Option("--integration-revision")],
    base_url: Annotated[str, typer.Option("--base-url")] = "http://127.0.0.1:8000",
    token_env: Annotated[str, typer.Option("--token-env")] = "POWERCONTEXT_TOKEN",
    experiment_arm: Annotated[str, typer.Option("--experiment-arm")] = DEFAULT_EXPERIMENT_ARM_ID,
    search_limit: Annotated[int, typer.Option("--search-limit", min=1, max=50)] = 10,
    timeout_seconds: Annotated[float, typer.Option("--timeout-seconds", min=0.1)] = 30.0,
) -> None:
    """Run the fixed LongMemEval-V2 subset through Memory retrieval without a model."""

    try:
        result = run_retrieval_smoke(
            data_root=data_root,
            dataset_lock=dataset_lock,
            harness_root=harness_root,
            smoke_manifest=smoke_manifest,
            output_dir=output_dir,
            run_id=run_id,
            powercontext_revision=powercontext_revision,
            integration_revision=integration_revision,
            base_url=base_url,
            token_env=token_env,
            experiment_arm=experiment_arm,
            search_limit=search_limit,
            timeout_seconds=timeout_seconds,
        )
    except ExperimentArmError as error:
        raise typer.BadParameter(str(error)) from None
    except (LongMemEvalV2CatalogError, RetrievalSmokeError, PowerContextMemoryAdapterError) as error:
        typer.echo(f"LongMemEval-V2 retrieval smoke failed: {error}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(
        json.dumps(
            {
                "classification": "smoke-subset-retrieval-only",
                "manifest": str(result.manifest_path),
                "results": str(result.results_path),
                "summary": str(result.summary_path),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


@longmemeval_v2_app.command("prepare-smoke")
def longmemeval_v2_prepare_smoke(
    retrieval_dir: Annotated[Path, typer.Option("--retrieval-dir")],
    harness_root: Annotated[Path, typer.Option("--harness-root")],
    harness_python: Annotated[Path, typer.Option("--harness-python")],
    output_dir: Annotated[Path, typer.Option("--output-dir")],
    processor_revision: Annotated[str, typer.Option("--processor-revision")],
    processor_model: Annotated[str, typer.Option("--processor-model")] = DEFAULT_PROCESSOR_MODEL,
    memory_context_max_tokens: Annotated[int, typer.Option("--memory-context-max-tokens", min=1)] = 200_000,
) -> None:
    """Build pinned, bounded Reader inputs from retrieval-only smoke artifacts."""

    try:
        result = prepare_reader_inputs_smoke(
            retrieval_dir=retrieval_dir,
            harness_root=harness_root,
            harness_python=harness_python,
            output_dir=output_dir,
            processor_model=processor_model,
            processor_revision=processor_revision,
            memory_context_max_tokens=memory_context_max_tokens,
        )
    except PrepareSmokeError as error:
        typer.echo(f"LongMemEval-V2 prompt preparation failed: {error}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(
        json.dumps(
            {
                "classification": "smoke-subset-prepare-only",
                "manifest": str(result.manifest_path),
                "prompts": str(result.prompts_path),
                "summary": str(result.summary_path),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


@longmemeval_v2_app.command("reader-smoke")
def longmemeval_v2_reader_smoke(
    prepared_dir: Annotated[Path, typer.Option("--prepared-dir")],
    output_dir: Annotated[Path, typer.Option("--output-dir")],
    provider: Annotated[str, typer.Option("--provider")] = "anthropic-compatible",
    model: Annotated[str | None, typer.Option("--model")] = None,
    base_url: Annotated[str | None, typer.Option("--base-url")] = None,
    base_url_env: Annotated[str, typer.Option("--base-url-env")] = DEFAULT_ANTHROPIC_BASE_URL_ENV,
    token_env: Annotated[str | None, typer.Option("--token-env")] = None,
    max_tokens: Annotated[int, typer.Option("--max-tokens", min=1)] = 512,
    temperature: Annotated[float, typer.Option("--temperature", min=0.0, max=2.0)] = 0.0,
    timeout_seconds: Annotated[float, typer.Option("--timeout-seconds", min=1.0)] = 120.0,
    max_questions: Annotated[int | None, typer.Option("--max-questions", min=1)] = None,
    price_policy: Annotated[str | None, typer.Option("--price-policy")] = None,
) -> None:
    """Call a configured Reader over prepared smoke prompts without scoring."""

    resolved_price_policy = _parse_price_policy_option(price_policy, label="--price-policy")
    try:
        result = run_reader_smoke(
            prepared_dir=prepared_dir,
            output_dir=output_dir,
            provider=provider,
            model=model,
            base_url=base_url,
            base_url_env=base_url_env,
            token_env=token_env,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
            max_questions=max_questions,
            price_policy=resolved_price_policy,
        )
    except ReaderSmokeError as error:
        typer.echo(f"LongMemEval-V2 Reader failed: {error}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(
        json.dumps(
            {
                "classification": "smoke-subset-reader-only",
                "manifest": str(result.manifest_path),
                "outputs": str(result.outputs_path),
                "summary": str(result.summary_path),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


@longmemeval_v2_app.command("score-smoke")
def longmemeval_v2_score_smoke(
    reader_dir: Annotated[Path, typer.Option("--reader-dir")],
    data_root: Annotated[Path, typer.Option("--data-root")],
    dataset_lock: Annotated[Path, typer.Option("--dataset-lock")],
    smoke_manifest: Annotated[Path, typer.Option("--smoke-manifest")],
    harness_root: Annotated[Path, typer.Option("--harness-root")],
    output_dir: Annotated[Path, typer.Option("--output-dir")],
    judge_model: Annotated[str, typer.Option("--judge-model")] = SCORE_DEFAULT_DEEPSEEK_MODEL,
    judge_token_env: Annotated[str, typer.Option("--judge-token-env")] = SCORE_DEFAULT_DEEPSEEK_TOKEN_ENV,
    judge_base_url: Annotated[str, typer.Option("--judge-base-url")] = SCORE_DEFAULT_DEEPSEEK_BASE_URL,
    judge_max_tokens: Annotated[int, typer.Option("--judge-max-tokens", min=1)] = 256,
    judge_temperature: Annotated[float, typer.Option("--judge-temperature", min=0.0, max=2.0)] = 0.0,
    judge_timeout_seconds: Annotated[float, typer.Option("--judge-timeout-seconds", min=1.0)] = 120.0,
    price_policy: Annotated[str | None, typer.Option("--judge-price-policy")] = None,
) -> None:
    """Score Reader smoke outputs with pinned rules and DeepSeek only where upstream requires a judge."""

    resolved_price_policy = _parse_price_policy_option(price_policy, label="--judge-price-policy")
    try:
        result = run_score_smoke(
            reader_dir=reader_dir,
            data_root=data_root,
            dataset_lock=dataset_lock,
            smoke_manifest=smoke_manifest,
            harness_root=harness_root,
            output_dir=output_dir,
            judge_model=judge_model,
            judge_token_env=judge_token_env,
            judge_base_url=judge_base_url,
            judge_max_tokens=judge_max_tokens,
            judge_temperature=judge_temperature,
            judge_timeout_seconds=judge_timeout_seconds,
            judge_price_policy=resolved_price_policy,
        )
    except ScoreSmokeError as error:
        typer.echo(f"LongMemEval-V2 scoring failed: {error}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(
        json.dumps(
            {
                "classification": "smoke-subset-score-only",
                "manifest": str(result.manifest_path),
                "results": str(result.results_path),
                "summary": str(result.summary_path),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


@longmemeval_v2_app.command("replay-score")
def longmemeval_v2_replay_score(
    score_dir: Annotated[Path, typer.Option("--score-dir")],
    harness_root: Annotated[Path, typer.Option("--harness-root")],
    output_dir: Annotated[Path, typer.Option("--output-dir")],
) -> None:
    """Replay saved deterministic and Judge score decisions without model calls."""

    try:
        result = replay_score_smoke(score_dir=score_dir, harness_root=harness_root, output_dir=output_dir)
    except ReplayScoreError as error:
        typer.echo(f"LongMemEval-V2 score replay failed: {error}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(
        json.dumps(
            {
                "classification": "smoke-subset-score-replay",
                "manifest": str(result.manifest_path),
                "results": str(result.results_path),
                "summary": str(result.summary_path),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


@longmemeval_v2_app.command("run-smoke")
def longmemeval_v2_run_smoke(
    data_root: Annotated[Path, typer.Option("--data-root")],
    dataset_lock: Annotated[Path, typer.Option("--dataset-lock")],
    smoke_manifest: Annotated[Path, typer.Option("--smoke-manifest")],
    harness_root: Annotated[Path, typer.Option("--harness-root")],
    harness_python: Annotated[Path, typer.Option("--harness-python")],
    processor_revision: Annotated[str, typer.Option("--processor-revision")],
    output_dir: Annotated[Path, typer.Option("--output-dir")],
    powercontext_revision: Annotated[str, typer.Option("--powercontext-revision")],
    integration_revision: Annotated[str, typer.Option("--integration-revision")],
    run_id: Annotated[str | None, typer.Option("--run-id")] = None,
    processor_model: Annotated[str, typer.Option("--processor-model")] = DEFAULT_PROCESSOR_MODEL,
    memory_context_max_tokens: Annotated[int, typer.Option("--memory-context-max-tokens", min=1)] = 200_000,
    powercontext_base_url: Annotated[str, typer.Option("--powercontext-base-url")] = "http://127.0.0.1:8000",
    powercontext_token_env: Annotated[str, typer.Option("--powercontext-token-env")] = "POWERCONTEXT_TOKEN",
    experiment_arm: Annotated[str, typer.Option("--experiment-arm")] = DEFAULT_EXPERIMENT_ARM_ID,
    search_limit: Annotated[int, typer.Option("--search-limit", min=1, max=50)] = 10,
    timeout_seconds: Annotated[float, typer.Option("--timeout-seconds", min=0.1)] = 30.0,
    reader_provider: Annotated[str, typer.Option("--reader-provider")] = "deepseek-openai",
    reader_model: Annotated[str | None, typer.Option("--reader-model")] = None,
    reader_base_url: Annotated[str | None, typer.Option("--reader-base-url")] = None,
    reader_base_url_env: Annotated[str, typer.Option("--reader-base-url-env")] = DEFAULT_ANTHROPIC_BASE_URL_ENV,
    reader_token_env: Annotated[str | None, typer.Option("--reader-token-env")] = None,
    reader_max_tokens: Annotated[int, typer.Option("--reader-max-tokens", min=1)] = 512,
    reader_temperature: Annotated[float, typer.Option("--reader-temperature", min=0.0, max=2.0)] = 0.0,
    reader_timeout_seconds: Annotated[float, typer.Option("--reader-timeout-seconds", min=1.0)] = 120.0,
    judge_provider: Annotated[str, typer.Option("--judge-provider")] = "deepseek-openai",
    judge_model: Annotated[str, typer.Option("--judge-model")] = SCORE_DEFAULT_DEEPSEEK_MODEL,
    judge_token_env: Annotated[str, typer.Option("--judge-token-env")] = SCORE_DEFAULT_DEEPSEEK_TOKEN_ENV,
    judge_base_url: Annotated[str, typer.Option("--judge-base-url")] = SCORE_DEFAULT_DEEPSEEK_BASE_URL,
    judge_max_tokens: Annotated[int, typer.Option("--judge-max-tokens", min=1)] = 256,
    judge_temperature: Annotated[float, typer.Option("--judge-temperature", min=0.0, max=2.0)] = 0.0,
    judge_timeout_seconds: Annotated[float, typer.Option("--judge-timeout-seconds", min=1.0)] = 120.0,
    price_policy: Annotated[str | None, typer.Option("--price-policy")] = None,
    judge_price_policy: Annotated[str | None, typer.Option("--judge-price-policy")] = None,
    skip_reader: Annotated[bool, typer.Option("--skip-reader")] = False,
    skip_score: Annotated[bool, typer.Option("--skip-score")] = False,
) -> None:
    """Run the whole LongMemEval-V2 smoke workload into one fail-closed run directory."""

    resolved_price_policy = _parse_price_policy_option(price_policy, label="--price-policy")
    resolved_judge_price_policy = _parse_price_policy_option(judge_price_policy, label="--judge-price-policy")
    try:
        result = run_smoke(
            data_root=data_root,
            dataset_lock=dataset_lock,
            smoke_manifest=smoke_manifest,
            harness_root=harness_root,
            harness_python=harness_python,
            processor_revision=processor_revision,
            output_dir=output_dir,
            powercontext_revision=powercontext_revision,
            integration_revision=integration_revision,
            run_id=run_id,
            processor_model=processor_model,
            memory_context_max_tokens=memory_context_max_tokens,
            powercontext_base_url=powercontext_base_url,
            powercontext_token_env=powercontext_token_env,
            experiment_arm=experiment_arm,
            search_limit=search_limit,
            timeout_seconds=timeout_seconds,
            reader_provider=reader_provider,
            reader_model=reader_model,
            reader_base_url=reader_base_url,
            reader_base_url_env=reader_base_url_env,
            reader_token_env=reader_token_env,
            reader_max_tokens=reader_max_tokens,
            reader_temperature=reader_temperature,
            reader_timeout_seconds=reader_timeout_seconds,
            judge_provider=judge_provider,
            judge_model=judge_model,
            judge_token_env=judge_token_env,
            judge_base_url=judge_base_url,
            judge_max_tokens=judge_max_tokens,
            judge_temperature=judge_temperature,
            judge_timeout_seconds=judge_timeout_seconds,
            reader_price_policy=resolved_price_policy,
            judge_price_policy=resolved_judge_price_policy,
            skip_reader=skip_reader,
            skip_score=skip_score,
        )
    except (RunSmokeError, LongMemEvalV2CatalogError, ExperimentArmError) as error:
        typer.echo(f"LongMemEval-V2 smoke run failed: {error}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(
        json.dumps(
            {
                "classification": "smoke-subset",
                "status": result.status,
                "manifest": str(result.manifest_path),
                "summary": str(result.summary_path),
                "completed_phases": list(result.completed_phases),
                "skipped_phases": list(result.skipped_phases),
                "failed_phase": result.failed_phase,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    if result.status != "completed":
        raise typer.Exit(code=1)


@longmemeval_v2_app.command("report")
def longmemeval_v2_report(
    run_dir: Annotated[Path, typer.Option("--run-dir")],
    output_dir: Annotated[Path | None, typer.Option("--output-dir")] = None,
) -> None:
    """Summarize one saved smoke run into a single unified report without a model or server."""

    try:
        result = build_report(run_dir=run_dir, output_dir=output_dir)
    except ReportError as error:
        typer.echo(f"LongMemEval-V2 report failed: {error}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(
        json.dumps(
            {
                "classification": "smoke-subset",
                "report": str(result.report_path),
                "markdown": str(result.markdown_path),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


@swebench_pro_app.command("run")
def swebench_pro_run(
    root_path: str = typer.Option(..., "--root"),
    powercontext_source: str | None = typer.Option(None),
    powercontext_ref: str = typer.Option("latest"),
    harness_root: str | None = typer.Option(None),
    harness_python: str | None = typer.Option(None),
    dataset_path: str | None = typer.Option(None),
    instance_id: str = typer.Option(...),
    codex_bin: str | None = typer.Option(None),
    tokensflow_enabled: bool = typer.Option(False, "--tokensflow/--no-tokensflow"),
    tokensflow_bin: str | None = typer.Option(None),
    tokensflow_user_home: str | None = typer.Option(None),
    tokensflow_egress_network: str | None = typer.Option(None),
    uv_bin: str | None = typer.Option(None),
    registry_bin: str | None = typer.Option(None),
    auth_json: str | None = typer.Option(None),
    proxy_url: str | None = typer.Option(None),
    docker_network_pool: str = typer.Option(DEFAULT_DOCKER_NETWORK_POOL),
    extra_no_proxy_hosts: str = typer.Option(""),
    model: str = typer.Option(DEFAULT_CODEX_MODEL, "--model"),
    reasoning_effort: str = typer.Option(DEFAULT_REASONING_EFFORT, "--reasoning-effort"),
    run_id: str | None = typer.Option(None),
) -> None:
    """Run Gold, PowerContext OFF/ON, official grading, and report generation."""

    from powercontext_eval.runner import RunConfig

    root = Path(root_path)
    harness = Path(harness_root) if harness_root is not None else root / "cache" / "swebench-pro.git"
    dataset = Path(dataset_path) if dataset_path is not None else harness / "helper_code" / "sweap_eval_full_v2.jsonl"
    binaries = root / "bin"

    catalog = SweBenchProCatalog.load(dataset)
    result = run_swebench_pro_instance(
        RunConfig(
            root=root,
            powercontext_source=(
                Path(powercontext_source) if powercontext_source is not None else root / "source" / "powercontext.git"
            ),
            powercontext_ref=powercontext_ref,
            harness_root=harness,
            harness_python=(
                Path(harness_python)
                if harness_python is not None
                else root / "venvs" / "swebench-pro" / "bin" / "python"
            ),
            codex_binary=Path(codex_bin) if codex_bin is not None else binaries / "codex",
            tokensflow_enabled=tokensflow_enabled,
            tokensflow_binary=(
                Path(tokensflow_bin)
                if tokensflow_bin is not None
                else (binaries / "tokensflow" if tokensflow_enabled else None)
            ),
            tokensflow_user_home=(
                Path(tokensflow_user_home)
                if tokensflow_user_home is not None
                else (root / "tokensflow-home" if tokensflow_enabled else None)
            ),
            tokensflow_egress_network=tokensflow_egress_network,
            uv_binary=Path(uv_bin) if uv_bin is not None else binaries / "uv",
            registry_binary=Path(registry_bin) if registry_bin is not None else binaries / "regctl",
            auth_json=Path(auth_json) if auth_json is not None else root / "codex-home" / "auth.json",
            proxy_url=proxy_url,
            docker_network_pool=docker_network_pool,
            extra_no_proxy_hosts=tuple(host for host in extra_no_proxy_hosts.split(",") if host),
            run_id=run_id or datetime.now(UTC).strftime("run-%Y%m%d-%H%M%S"),
            model=model,
            reasoning_effort=reasoning_effort,
        ),
        instance=catalog.require(instance_id),
    )
    typer.echo(
        json.dumps(
            {
                "run_id": result.run_id,
                "report": str(result.report_path),
                "off_resolved": result.off_resolved,
                "on_resolved": result.on_resolved,
            },
            sort_keys=True,
        )
    )


@swebench_pro_app.command("create-batch")
def swebench_pro_create_batch(
    idempotency_key: str = typer.Option(..., "--idempotency-key"),
    console_url: str = typer.Option("http://127.0.0.1:8787", "--console-url"),
    powercontext_ref: str = typer.Option("latest", "--powercontext-ref"),
    task_set: str = typer.Option(PUBLIC_V2_TASK_SET, "--task-set"),
    model: str = typer.Option(DEFAULT_CODEX_MODEL, "--model"),
    treatment_mode: Annotated[TreatmentMode, typer.Option("--treatment-mode")] = TreatmentMode.OFF_ON,
    usage_pause_percent: int = typer.Option(80, "--usage-pause-percent", min=1, max=100),
    start_paused: bool = typer.Option(False, "--start-paused/--start-running"),
) -> None:
    """Create one full batch through the console API, optionally atomically paused."""

    from powercontext_eval.web.batches import BatchCreate

    endpoint = _batch_api_endpoint(console_url)
    try:
        batch = BatchCreate(
            powercontext_ref=powercontext_ref,
            benchmark="swebench-pro",
            task_set=cast(TaskSet, task_set),
            model=model,
            reasoning_effort=DEFAULT_REASONING_EFFORT,
            treatment_mode=treatment_mode,
            idempotency_key=idempotency_key,
            usage_pause_percent=usage_pause_percent,
            initial_control_intent="pause" if start_paused else "run",
        )
    except ValidationError:
        raise typer.BadParameter("Invalid batch configuration.") from None
    request = Request(
        endpoint,
        data=batch.model_dump_json().encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        if error.code == 422:
            raise typer.BadParameter(
                "Codex model is not enabled for new evaluation work.",
                param_hint="--model",
            ) from None
        raise typer.Exit(code=1) from None
    except (URLError, OSError, ValueError, UnicodeDecodeError):
        raise typer.Exit(code=1) from None
    if not isinstance(payload, dict) or not isinstance(payload.get("batch_id"), str):
        raise typer.Exit(code=1)
    typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _batch_api_endpoint(console_url: str) -> str:
    parsed = urlsplit(console_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise typer.BadParameter("Invalid console URL.", param_hint="--console-url")
    return console_url.rstrip("/") + "/api/batches"


def main() -> None:
    """Run the evaluation command-line application."""

    app()


if __name__ == "__main__":
    main()
