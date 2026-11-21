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

"""Opt-in live generation and default model-free Source replay commands."""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import AsyncExitStack, asynccontextmanager, nullcontext
from dataclasses import replace
from enum import Enum
from pathlib import Path
from typing import Annotated, Any, cast
from urllib.parse import urlsplit

import typer
from pydantic import ValidationError

from .live_evaluation import combined_live, live_plan, verify_smoke
from .manifests import PACKAGE_ROOT, EvaluationError, Suite, live_policy, load_private_suite, load_suite, read_json
from .models import digest
from .private_audit import PrivateAuditJournal
from .report import aggregate, code_identity, load_run, publish
from .runner import instructions_for, run_sample
from .verification import combined_preparation

app = typer.Typer(no_args_is_help=True, help="Versioned, isolated Source evidence admission evaluation.")


class Provider(str, Enum):
    OPENAI = "openai-compatible"
    ANTHROPIC = "anthropic"


def selected_suite(
    root: Path, private_manifest: Path | None, experiment_version: str = "v1", dataset: str = "synthetic-v1"
) -> Suite:
    suite = load_suite(root, experiment_version=experiment_version, dataset=dataset)
    return load_private_suite(private_manifest, suite) if private_manifest else suite


def manifest_for(suite: Suite, mode: str, *, provider: dict | None = None) -> dict:
    return {
        "schema": "evidence-admission.run.v2" if suite.experiment.citation_protocol else "evidence-admission.run.v1",
        "mode": mode,
        "code": code_identity(suite),
        "repetitions": suite.experiment.repetitions if mode == "prepare" else suite.experiment.live_repetitions,
        "experiment": suite.experiment.model_dump(mode="json", exclude_none=True),
        "dataset": suite.dataset,
        "private": suite.private,
        "provider": provider,
        "cases": [
            {
                "case_hash": digest(case.model_dump(mode="json")),
                "label": case.case_id,
                "categories": list(case.categories),
                "completeness_ceiling": case.completeness_ceiling,
                "reducibility": case.reducibility,
                "required_claims": len(case.required),
                "cancel_phase": case.cancel_phase,
            }
            for case in suite.cases
        ],
    }


async def execute(
    suite: Suite, manifest: dict, output: Path, *, generator: Any = None, raw_dir: Path | None = None
) -> dict:
    if output.exists():
        raise EvaluationError("refusing_existing_output")
    records = []
    budgets = suite.experiment.budgets
    if manifest["mode"] == "smoke":
        smoke = live_policy(suite.root, version=suite.live_policy_version)["smoke"]
        if (
            manifest.get("smoke") != smoke
            or suite.dataset != smoke["dataset"]
            or [c.case_id for c in suite.cases] != smoke["cases"]
            or manifest["repetitions"] != smoke["repetitions"]
        ):
            raise EvaluationError("invalid_smoke_scope")
        budgets = tuple(smoke["budgets"])
    with PrivateAuditJournal(raw_dir, manifest) if raw_dir is not None else nullcontext() as journal:
        for case in suite.cases:
            for arm in suite.experiment.arms:
                for budget in budgets:
                    for repetition in range(manifest["repetitions"]):
                        sample = await run_sample(
                            suite,
                            case,
                            arm,
                            budget,
                            repetition,
                            generator=generator,
                            output_sink=journal.record_output if journal else None,
                        )
                        records.append(sample)
                        if journal:
                            journal.record_sample(sample)
        if code_identity(suite) != manifest["code"]:
            raise EvaluationError("code_or_freeze_changed_during_run")
        samples = tuple(records)
        report = aggregate(suite, samples, manifest)
        if journal:
            journal.finalize_outputs()
        publish(output, manifest, samples, report)
    return report


@app.command("validate")
def validate(
    suite_root: Annotated[Path, typer.Option()] = PACKAGE_ROOT,
    experiment_version: Annotated[str, typer.Option()] = "v1",
    dataset: Annotated[str, typer.Option()] = "synthetic-v1",
) -> None:
    suite = load_suite(suite_root, experiment_version=experiment_version, dataset=dataset)
    typer.echo(
        json.dumps(
            {
                "schema": "evidence-admission.validation.v1",
                "fixtures": len(suite.cases),
                "frozen_hashes": suite.frozen_hashes,
            },
            ensure_ascii=False,
        )
    )


@app.command("prepare")
def prepare(
    output_dir: Annotated[Path, typer.Option()],
    suite_root: Annotated[Path, typer.Option()] = PACKAGE_ROOT,
    private_manifest: Annotated[Path | None, typer.Option()] = None,
    experiment_version: Annotated[str, typer.Option()] = "v1",
    dataset: Annotated[str, typer.Option()] = "synthetic-v1",
) -> None:
    suite = selected_suite(suite_root, private_manifest, experiment_version, dataset)
    report = asyncio.run(execute(suite, manifest_for(suite, "prepare"), output_dir))
    typer.echo(json.dumps({"decision": report["decision"], "live_generation": "not_run"}))


@app.command("compare-preparation")
def compare_preparation_command(
    run_dir: Annotated[list[Path], typer.Option()],
    output_file: Annotated[Path, typer.Option()],
    suite_root: Annotated[Path, typer.Option()] = PACKAGE_ROOT,
    experiment_version: Annotated[str, typer.Option()] = "v3",
) -> None:
    if output_file.exists():
        raise EvaluationError("refusing_existing_output")
    suite = load_suite(suite_root, experiment_version=experiment_version)
    comparison = combined_preparation(suite, tuple(run_dir))
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(comparison, ensure_ascii=False, indent=2) + "\n")
    typer.echo(
        json.dumps(
            {
                "offline_preparation_passed": comparison["offline_preparation_passed"],
                "decision": comparison["decision"],
            }
        )
    )


@app.command("plan-live")
def plan_live_command(
    preparation_run_dir: Annotated[list[Path], typer.Option()],
    output_file: Annotated[Path, typer.Option()],
    suite_root: Annotated[Path, typer.Option()] = PACKAGE_ROOT,
    experiment_version: Annotated[str, typer.Option()] = "v3",
) -> None:
    if output_file.exists():
        raise EvaluationError("refusing_existing_output")
    suite = load_suite(suite_root, experiment_version=experiment_version)
    plan = live_plan(suite, tuple(preparation_run_dir))
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    typer.echo(
        json.dumps(
            {"formal_provider_request_cap": plan["formal_provider_request_cap"], "actual_provider_usage": "not_run"}
        )
    )


@app.command("compare-live")
def compare_live_command(
    preparation_run_dir: Annotated[list[Path], typer.Option()],
    original_run_dir: Annotated[list[Path], typer.Option()],
    audited_run_dir: Annotated[list[Path], typer.Option()],
    output_file: Annotated[Path, typer.Option()],
    suite_root: Annotated[Path, typer.Option()] = PACKAGE_ROOT,
    experiment_version: Annotated[str, typer.Option()] = "v3",
) -> None:
    if output_file.exists():
        raise EvaluationError("refusing_existing_output")
    suite = load_suite(suite_root, experiment_version=experiment_version)
    report = combined_live(suite, tuple(preparation_run_dir), tuple(original_run_dir), tuple(audited_run_dir))
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    typer.echo(json.dumps({"decision": report["decision"]}))


@asynccontextmanager
async def live_generator(
    suite: Suite, provider: Provider, model: str, token_env: str, base_url: str, max_requests: int
):
    from powercontext.builtin.artifacts.atomic_memory.extraction import (
        AtomicMemoryExtractionInput,
        AtomicMemoryExtractionOutput,
    )
    from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIStructuredGenerator
    from pydantic_ai.settings import ModelSettings

    parsed = urlsplit(base_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise EvaluationError("live_endpoint_requires_credential_free_https")
    key = os.environ.get(token_env)
    if not key or not model.strip() or max_requests < 1:
        raise EvaluationError("missing_live_configuration")
    async with AsyncExitStack() as resources:
        if provider is Provider.OPENAI:
            from openai import AsyncOpenAI, DefaultAsyncHttpxClient
            from pydantic_ai.models.openai import OpenAIChatModel
            from pydantic_ai.providers.openai import OpenAIProvider

            client = await resources.enter_async_context(
                DefaultAsyncHttpxClient(follow_redirects=False, timeout=suite.experiment.timeout_seconds)
            )
            sdk = await resources.enter_async_context(
                AsyncOpenAI(api_key=key, base_url=base_url, http_client=client, max_retries=0)
            )
            selected = OpenAIChatModel(model, provider=OpenAIProvider(openai_client=sdk))
        else:
            from anthropic import AsyncAnthropic
            from anthropic import DefaultAsyncHttpxClient as AnthropicHttpClient
            from pydantic_ai.models.anthropic import AnthropicModel
            from pydantic_ai.providers.anthropic import AnthropicProvider

            anthropic_client = await resources.enter_async_context(
                AnthropicHttpClient(follow_redirects=False, timeout=suite.experiment.timeout_seconds)
            )
            anthropic_sdk = await resources.enter_async_context(
                AsyncAnthropic(api_key=key, base_url=base_url, http_client=anthropic_client, max_retries=0)
            )
            selected = AnthropicModel(model, provider=AnthropicProvider(anthropic_client=anthropic_sdk))
        generator = PydanticAIStructuredGenerator(
            model=selected,
            instructions=instructions_for(suite),
            input_type=AtomicMemoryExtractionInput,
            output_type=AtomicMemoryExtractionOutput,
            limits=InferenceLimits(
                timeout_seconds=suite.experiment.timeout_seconds,
                max_requests=suite.experiment.max_requests_per_call,
                max_output_tokens_per_request=suite.experiment.max_output_tokens,
                output_tokens_limit=suite.experiment.max_output_tokens,
                allow_continuations=False,
            ),
            model_settings=cast(ModelSettings, dict(suite.experiment.model_settings)),
            name="evidence_admission_evaluation",
        )

        class BudgetedGenerator:
            calls = 0

            async def generate(self, value, /):
                if self.calls + suite.experiment.max_requests_per_call > max_requests:
                    raise EvaluationError("live_request_budget_exhausted")
                self.calls += suite.experiment.max_requests_per_call
                return await generator.generate(value)

        yield BudgetedGenerator()


@app.command("run-live")
def run_live(
    output_dir: Annotated[Path, typer.Option()],
    audit_dir: Annotated[Path, typer.Option()],
    provider: Annotated[Provider, typer.Option()],
    model: Annotated[str, typer.Option()],
    token_env: Annotated[str, typer.Option()],
    base_url: Annotated[str, typer.Option()],
    max_requests: Annotated[int, typer.Option(min=1)],
    suite_root: Annotated[Path, typer.Option()] = PACKAGE_ROOT,
    private_manifest: Annotated[Path | None, typer.Option()] = None,
    experiment_version: Annotated[str, typer.Option()] = "v1",
    dataset: Annotated[str, typer.Option()] = "synthetic-v1",
    smoke_run_dir: Annotated[Path | None, typer.Option()] = None,
    smoke_audited_run_dir: Annotated[Path | None, typer.Option()] = None,
) -> None:
    suite = selected_suite(suite_root, private_manifest, experiment_version, dataset)
    manifest = manifest_for(
        suite,
        "live",
        provider={
            "provider": provider.value,
            "model": model,
            "endpoint_hash": digest(base_url),
            "max_requests": max_requests,
        },
    )
    if not manifest["code"]["frozen_committed"]:
        raise EvaluationError("live_requires_committed_fixture_policy_and_code_freeze")

    if suite.experiment.citation_protocol:
        if smoke_run_dir is None or smoke_audited_run_dir is None:
            raise EvaluationError("successful_audited_smoke_required")
        manifest.update(verify_smoke(suite, smoke_run_dir, smoke_audited_run_dir, manifest["provider"]))

    async def run():
        async with live_generator(suite, provider, model, token_env, base_url, max_requests) as generator:
            return await execute(suite, manifest, output_dir, generator=generator, raw_dir=audit_dir)

    report = asyncio.run(run())
    typer.echo(json.dumps({"decision": report["decision"]}))


@app.command("run-smoke")
def run_smoke(
    output_dir: Annotated[Path, typer.Option()],
    audit_dir: Annotated[Path, typer.Option()],
    provider: Annotated[Provider, typer.Option()],
    model: Annotated[str, typer.Option()],
    token_env: Annotated[str, typer.Option()],
    base_url: Annotated[str, typer.Option()],
    max_requests: Annotated[int, typer.Option(min=1)],
    suite_root: Annotated[Path, typer.Option()] = PACKAGE_ROOT,
    experiment_version: Annotated[str, typer.Option()] = "v3",
) -> None:
    if experiment_version not in {"v3", "v4", "v5"}:
        raise EvaluationError("smoke_requires_versioned_live_protocol")
    suite = load_suite(suite_root, experiment_version=experiment_version)
    scope = live_policy(suite.root, version=suite.live_policy_version)["smoke"]
    cases = {c.case_id: c for c in suite.cases}
    suite = replace(suite, cases=tuple(cases[label] for label in scope["cases"]))
    manifest = manifest_for(
        suite,
        "smoke",
        provider={
            "provider": provider.value,
            "model": model,
            "endpoint_hash": digest(base_url),
            "max_requests": max_requests,
        },
    )
    manifest.update(repetitions=scope["repetitions"], smoke=scope)
    if not manifest["code"]["frozen_committed"]:
        raise EvaluationError("live_requires_committed_fixture_policy_and_code_freeze")

    async def run():
        async with live_generator(suite, provider, model, token_env, base_url, max_requests) as generator:
            return await execute(suite, manifest, output_dir, generator=generator, raw_dir=audit_dir)

    report = asyncio.run(run())
    typer.echo(json.dumps({"decision": report["decision"], "smoke": report["smoke"]}))


def _matching_freeze(suite: Suite, manifest: dict) -> None:
    current = code_identity(suite)
    keys = (
        "suite_code_hash",
        "runtime_code_hash",
        "frozen_hashes",
        "instruction_hash",
        "estimator",
        "dependency_hashes",
        "versions",
    )
    if any(current[key] != manifest["code"][key] for key in keys):
        raise EvaluationError("run_code_or_configuration_mismatch")


@app.command("report")
def report_command(
    run_dir: Annotated[Path, typer.Option()],
    suite_root: Annotated[Path, typer.Option()] = PACKAGE_ROOT,
    private_manifest: Annotated[Path | None, typer.Option()] = None,
    experiment_version: Annotated[str, typer.Option()] = "v1",
    dataset: Annotated[str, typer.Option()] = "synthetic-v1",
) -> None:
    suite = selected_suite(suite_root, private_manifest, experiment_version, dataset)
    manifest, samples, stored = load_run(run_dir)
    _matching_freeze(suite, manifest)
    if manifest["mode"] in {"smoke", "audited_smoke"}:
        scope = live_policy(suite.root, version=suite.live_policy_version)["smoke"]
        if suite.dataset != scope["dataset"] or manifest.get("smoke") != scope:
            raise EvaluationError("invalid_smoke_scope")
        cases = {c.case_id: c for c in suite.cases}
        suite = replace(suite, cases=tuple(cases[label] for label in scope["cases"]))
    recomputed = aggregate(suite, samples, manifest)
    if recomputed != stored:
        raise EvaluationError("report_does_not_match_samples")
    typer.echo(json.dumps(recomputed, ensure_ascii=False, indent=2))


@app.command("audit")
def audit_command(
    run_dir: Annotated[Path, typer.Option()],
    output_dir: Annotated[Path, typer.Option()],
    private_outputs: Annotated[Path, typer.Option()],
    reviews: Annotated[Path, typer.Option()],
    suite_root: Annotated[Path, typer.Option()] = PACKAGE_ROOT,
    private_manifest: Annotated[Path | None, typer.Option()] = None,
    experiment_version: Annotated[str, typer.Option()] = "v1",
    dataset: Annotated[str, typer.Option()] = "synthetic-v1",
) -> None:
    from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryExtractionOutput
    from powercontext.builtin.inference import GenerationResult

    suite = selected_suite(suite_root, private_manifest, experiment_version, dataset)
    manifest, samples, _ = load_run(run_dir)
    _matching_freeze(suite, manifest)
    smoke = manifest["mode"] == "smoke" and suite.experiment.citation_protocol is not None
    if manifest["mode"] != "live" and not smoke:
        raise EvaluationError("audit_requires_original_live_run")
    if smoke:
        scope = live_policy(suite.root, version=suite.live_policy_version)["smoke"]
        if (
            suite.dataset != scope["dataset"]
            or manifest.get("smoke") != scope
            or manifest["repetitions"] != scope["repetitions"]
        ):
            raise EvaluationError("invalid_smoke_scope")
        cases = {case.case_id: case for case in suite.cases}
        suite = replace(suite, cases=tuple(cases[label] for label in scope["cases"]))
    raw = read_json(private_outputs)
    labels = read_json(reviews)
    if raw.get("manifest_hash") != digest(manifest) or labels.get("manifest_hash") != digest(manifest):
        raise EvaluationError("audit_run_identity_mismatch")
    by_sample = {}
    for output in raw["outputs"]:
        by_sample.setdefault(output["sample_id"], []).append(output)
    if set(by_sample) - {sample.sample_id for sample in samples}:
        raise EvaluationError("audit_unknown_sample")
    for sample in samples:
        calls = sorted(by_sample.get(sample.sample_id, []), key=lambda row: row["call_index"])
        if (
            [row["call_index"] for row in calls] != list(range(len(sample.output_hashes)))
            or tuple(row["output_hash"] for row in calls) != sample.output_hashes
            or any(digest(row["output"]) != row["output_hash"] for row in calls)
            or tuple(row["input_hash"] for row in calls) != sample.input_hashes[: len(calls)]
        ):
            raise EvaluationError("audit_output_hash_mismatch")
    reviewable = {s.sample_id: set(s.generation.candidate_hashes) for s in samples if s.generation}
    if set(labels.get("reviews", {})) - set(reviewable) or any(
        set(reviews) - reviewable[sample_id] for sample_id, reviews in labels.get("reviews", {}).items()
    ):
        raise EvaluationError("audit_unknown_review")

    async def replay_audit():
        audited = []
        cases = {digest(c.model_dump(mode="json")): c for c in suite.cases}
        for sample in samples:
            if not sample.generation:
                audited.append(sample)
                continue
            calls = sorted(by_sample.get(sample.sample_id, []), key=lambda row: row["call_index"])

            class RecordedGenerator:
                def __init__(self, recorded):
                    self.recorded = recorded
                    self.index = 0

                async def generate(self, value, /):
                    row = self.recorded[self.index]
                    self.index += 1
                    if (
                        digest(value.model_dump(mode="json")) != row["input_hash"]
                        or digest(row["output"]) != row["output_hash"]
                    ):
                        raise EvaluationError("audit_replay_identity_mismatch")
                    return GenerationResult(output=AtomicMemoryExtractionOutput.model_validate(row["output"]))

            regenerated = await run_sample(
                suite,
                cases[sample.case_hash],
                sample.arm,
                sample.budget,
                sample.repetition,
                generator=RecordedGenerator(calls),
                audits=labels.get("reviews", {}).get(sample.sample_id, {}),
            )
            if regenerated.input_hashes != sample.input_hashes:
                raise EvaluationError("audit_preparation_changed")
            audited.append(sample.model_copy(update={"generation": regenerated.generation}))
        return tuple(audited)

    audited = asyncio.run(replay_audit())
    updated = {
        **manifest,
        "mode": "audited_smoke" if smoke else "audited_live",
        "parent_manifest_hash": digest(manifest),
        "review_hash": digest(labels),
    }
    publish(output_dir, updated, audited, aggregate(suite, audited, updated))
    typer.echo("audit_complete")


def main() -> None:
    try:
        app()
    except EvaluationError as error:
        typer.echo(f"Evidence admission evaluation failed: {error}.", err=True)
        raise SystemExit(1) from None
    except (OSError, ValidationError, KeyError, TypeError, IndexError):
        # Provider, corpus and filesystem values are not public diagnostics.
        typer.echo("Evidence admission evaluation failed; check frozen inputs and local configuration.", err=True)
        raise SystemExit(1) from None
    except Exception:  # noqa: BLE001 - Public CLI diagnostics must exclude private corpus/provider values.
        typer.echo("Evidence admission evaluation failed: unexpected_evaluation_failure.", err=True)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
