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

"""Command-line interface for the LongMemEval-V2 memory evaluation suite."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from powercontext_eval_longmemeval_v2.adapter import PowerContextMemoryAdapterError
from powercontext_eval_longmemeval_v2.arms import (
    DEFAULT_EXPERIMENT_ARM_ID,
    ExperimentArmError,
)
from powercontext_eval_longmemeval_v2.catalog import (
    LongMemEvalV2CatalogError,
    LongMemEvalV2EnvironmentError,
    LongMemEvalV2InputError,
)
from powercontext_eval_longmemeval_v2.costs import (
    CostPolicyError,
    ModelPricePolicy,
    parse_cost_policy,
)
from powercontext_eval_longmemeval_v2.prepare_smoke import (
    DEFAULT_PROCESSOR_MODEL,
    PrepareSmokeError,
    prepare_reader_inputs_smoke,
)
from powercontext_eval_longmemeval_v2.reader_smoke import (
    DEFAULT_ANTHROPIC_BASE_URL_ENV,
    ReaderSmokeError,
    run_reader_smoke,
)
from powercontext_eval_longmemeval_v2.replay_score import (
    ReplayScoreError,
    replay_score_smoke,
)
from powercontext_eval_longmemeval_v2.report import ReportError, build_report
from powercontext_eval_longmemeval_v2.retrieval_smoke import (
    RetrievalSmokeError,
    run_retrieval_smoke,
)
from powercontext_eval_longmemeval_v2.run_smoke import RunSmokeError, run_smoke
from powercontext_eval_longmemeval_v2.score_smoke import (
    DEFAULT_DEEPSEEK_BASE_URL as SCORE_DEFAULT_DEEPSEEK_BASE_URL,
)
from powercontext_eval_longmemeval_v2.score_smoke import (
    DEFAULT_DEEPSEEK_MODEL as SCORE_DEFAULT_DEEPSEEK_MODEL,
)
from powercontext_eval_longmemeval_v2.score_smoke import (
    DEFAULT_DEEPSEEK_TOKEN_ENV as SCORE_DEFAULT_DEEPSEEK_TOKEN_ENV,
)
from powercontext_eval_longmemeval_v2.score_smoke import (
    ScoreSmokeError,
    run_score_smoke,
)
from powercontext_eval_longmemeval_v2.smoke import prepare_smoke_run

app = typer.Typer(no_args_is_help=True, help="Pinned LongMemEval-V2 evaluation.")


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


@app.command("smoke")
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


@app.command("retrieval-smoke")
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
    except (
        LongMemEvalV2CatalogError,
        RetrievalSmokeError,
        PowerContextMemoryAdapterError,
    ) as error:
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


@app.command("prepare-smoke")
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


@app.command("reader-smoke")
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
    allow_insecure_http: Annotated[
        bool, typer.Option("--allow-insecure-http", help="Allow plaintext HTTP for a trusted Reader endpoint.")
    ] = False,
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
            allow_insecure_http=allow_insecure_http,
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


@app.command("score-smoke")
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
    judge_allow_insecure_http: Annotated[
        bool, typer.Option("--judge-allow-insecure-http", help="Allow plaintext HTTP for a trusted Judge endpoint.")
    ] = False,
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
            judge_allow_insecure_http=judge_allow_insecure_http,
            judge_price_policy=resolved_price_policy,
        )
    except (ScoreSmokeError, ReaderSmokeError) as error:
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


@app.command("replay-score")
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


@app.command("run-smoke")
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
    reader_allow_insecure_http: Annotated[
        bool, typer.Option("--reader-allow-insecure-http", help="Allow plaintext HTTP for a trusted Reader endpoint.")
    ] = False,
    judge_provider: Annotated[str, typer.Option("--judge-provider")] = "deepseek-openai",
    judge_model: Annotated[str, typer.Option("--judge-model")] = SCORE_DEFAULT_DEEPSEEK_MODEL,
    judge_token_env: Annotated[str, typer.Option("--judge-token-env")] = SCORE_DEFAULT_DEEPSEEK_TOKEN_ENV,
    judge_base_url: Annotated[str, typer.Option("--judge-base-url")] = SCORE_DEFAULT_DEEPSEEK_BASE_URL,
    judge_max_tokens: Annotated[int, typer.Option("--judge-max-tokens", min=1)] = 256,
    judge_temperature: Annotated[float, typer.Option("--judge-temperature", min=0.0, max=2.0)] = 0.0,
    judge_timeout_seconds: Annotated[float, typer.Option("--judge-timeout-seconds", min=1.0)] = 120.0,
    judge_allow_insecure_http: Annotated[
        bool, typer.Option("--judge-allow-insecure-http", help="Allow plaintext HTTP for a trusted Judge endpoint.")
    ] = False,
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
            reader_allow_insecure_http=reader_allow_insecure_http,
            judge_provider=judge_provider,
            judge_model=judge_model,
            judge_token_env=judge_token_env,
            judge_base_url=judge_base_url,
            judge_max_tokens=judge_max_tokens,
            judge_temperature=judge_temperature,
            judge_timeout_seconds=judge_timeout_seconds,
            judge_allow_insecure_http=judge_allow_insecure_http,
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


@app.command("report")
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


def main() -> None:
    """Run the LongMemEval-V2 command-line application."""

    app()


if __name__ == "__main__":
    main()
