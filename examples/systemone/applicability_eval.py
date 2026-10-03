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

"""Paired applicability evaluation using synthetic records and a live Jev/Laya provider.

Run ``python -m examples.systemone.applicability_eval --env-file .env_jev`` from the checkout.
This explicitly seeds a fresh local SQLite Scope; it never reads the developer's project Memory.
The Server/Client SDK run through ASGITransport. Add --codex-host for paired real host execution
of the contract-change case. Other task-success fields remain unknown.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import platform
import time
import uuid
from pathlib import Path
from typing import Literal

import httpx
from pydantic import Field, SecretStr, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from powercontext.builtin.artifacts.skill.external import AgentEnvironmentProfile, AgentSkillTarget
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import DecisionModel, DecisionOutcome, RuntimeConfig
from powercontext.builtin.runtime.config import ExternalSkillsConfig
from powercontext.client import PowerContextClient
from powercontext.http import ArtifactReference
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, MetricsConfig, ServerSettings

from .adapter import SystemOneConfig, SystemOneDecisionModel
from .applicability import DecisionApplicabilitySelector, SelectionRequest, SelectionResult
from .applicability_catalog import ServerCandidateCatalog
from .applicability_fixture import CASES, FIXTURE_VERSION, EvaluationCase, seed_fixture
from .applicability_host import run_codex_host
from .decision import load_laya_budget


class ProviderSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SYSTEMONE_",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    provider: Literal["jev", "laya"]
    endpoint: str = Field(repr=False)
    model: str
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""), repr=False)


def selection_metrics(
    case: EvaluationCase,
    references: dict[str, ArtifactReference],
    request: SelectionRequest,
    result: SelectionResult,
) -> dict[str, object]:
    """Separate absolute applicability recall, recommendation errors and ranking success."""

    identities = {name: (item.family, item.artifact_id, item.revision) for name, item in references.items()}
    expected = {identities[name] for name in case.expected}
    applicable = {identities[name] for name in (case.expected if case.applicable is None else case.applicable)}
    pool = {
        (item.address.artifact.family, item.address.artifact.artifact_id, item.address.artifact.revision)
        for item in request.candidates
    }
    selected = {
        (item.artifact.family, item.artifact.artifact_id, item.artifact.revision) for item in result.recommendations
    }
    admitted = (
        pool
        if result.mode == "retrieval"
        else {
            (item.address.artifact.family, item.address.artifact.artifact_id, item.address.artifact.revision)
            for item in result.assessments
            if item.outcome is DecisionOutcome.YES
        }
    )
    retrieved_applicable = applicable & pool
    status = case.status or ("selected" if expected else "none")
    return {
        "applicable_candidate_recall": (
            len(admitted & retrieved_applicable) / len(retrieved_applicable) if retrieved_applicable else None
        ),
        "unretrieved_applicable_candidates": len(applicable - pool),
        "wrong_recommendations": len(selected - applicable),
        "unnecessary_recommendations": len(selected) if status == "none" else 0,
        "wrong_loads": None,
        "unnecessary_loads": None,
        "selection_exact": selected == expected and result.status == status and not result.used_fallback,
        "task_success": None,
    }


async def evaluate_cases(
    client: PowerContextClient,
    catalog: ServerCandidateCatalog,
    model: DecisionModel,
    scope_id: str,
    references: dict[str, ArtifactReference],
    environment: tuple[str, ...],
    *,
    input_price_per_million: float | None = None,
    output_price_per_million: float | None = None,
    output_directory: Path,
    codex_host: bool = False,
    codex_timeout: int = 180,
) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    baseline = DecisionApplicabilitySelector()
    assisted = DecisionApplicabilitySelector(model, enabled=True)
    for case in CASES:
        pool = await catalog.retrieve(scope_id, case.query)
        request = SelectionRequest(task=case.task, candidates=pool.candidates, environment=environment)
        started = time.monotonic()
        before = await baseline.select(request)
        baseline_ms = (time.monotonic() - started) * 1000
        started = time.monotonic()
        after = await assisted.select(request)
        decision_ms = (time.monotonic() - started) * 1000
        selected = tuple(item for item in pool.candidates if item.address in after.recommendations)
        await catalog.revalidate(scope_id, case.query, selected)
        decisions = (*after.assessments, *(item.decision for item in after.preferences))
        inputs = [item.input_tokens for item in decisions]
        outputs = [item.output_tokens for item in decisions]
        # Interrupted requests can be billed without returning usage. Never estimate them as zero.
        input_tokens = sum(inputs) if not after.used_fallback and all(item is not None for item in inputs) else None
        output_tokens = sum(outputs) if not after.used_fallback and all(item is not None for item in outputs) else None
        cost = None
        if (
            input_tokens is not None
            and output_tokens is not None
            and input_price_per_million is not None
            and output_price_per_million is not None
        ):
            cost = (input_tokens * input_price_per_million + output_tokens * output_price_per_million) / 1_000_000
        baseline_metrics = selection_metrics(case, references, request, before)
        assisted_metrics = selection_metrics(case, references, request, after)
        hosts: dict[str, object] = {}
        record = {
            "case": case.name,
            "task": case.task,
            "query": case.query,
            "request": request.model_dump(mode="json"),
            "omissions": [item.model_dump(mode="json") for item in pool.omissions],
            "baseline": {
                "result": before.model_dump(mode="json"),
                "metrics": baseline_metrics,
            },
            "assisted": {
                "result": after.model_dump(mode="json"),
                "metrics": assisted_metrics,
            },
            "added_latency_ms": decision_ms - baseline_ms,
            "added_input_tokens": input_tokens,
            "added_output_tokens": output_tokens,
            "estimated_added_cost_usd": cost,
            "hosts": hosts,
        }
        case_path = output_directory / f"{case.name}.json"
        case_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if codex_host and case.name == "change-en":
            for name, result, metrics in (
                ("baseline", before, baseline_metrics),
                ("assisted", after, assisted_metrics),
            ):
                host = await run_codex_host(
                    client,
                    catalog,
                    scope_id,
                    case.query,
                    request,
                    result,
                    output_directory / "host" / name,
                    timeout=codex_timeout,
                )
                metrics["task_success"] = host["task_success"]
                if host["context_read_observed"]:
                    metrics["wrong_loads"] = metrics["wrong_recommendations"]
                    metrics["unnecessary_loads"] = metrics["unnecessary_recommendations"]
                hosts[name] = host
                case_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # Preserve completed paid cases even if a later model or host request is interrupted.
        with (output_directory / "cases.jsonl").open("a", encoding="utf-8") as checkpoint:
            checkpoint.write(json.dumps(record, ensure_ascii=False) + "\n")
        records.append(record)
    return records


async def run(args: argparse.Namespace, config: SystemOneConfig) -> Path:
    directory = args.output / uuid.uuid4().hex
    directory.mkdir(parents=True)
    systems: dict[str, Literal["windows", "macos", "linux", "other"]] = {
        "Windows": "windows",
        "Darwin": "macos",
        "Linux": "linux",
    }
    operating_system = systems.get(platform.system(), "other")
    profile = AgentEnvironmentProfile(
        operating_system=operating_system,
        architecture=platform.machine(),
        commands={"python": platform.python_version()},
    )
    target = AgentSkillTarget(
        target_id="codex-project",
        agent_kind="codex",
        installation_scope="project",
        path=directory / "skills",
        environment=profile,
    )
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{directory / 'runtime.db'}"),
            runtime=RuntimeConfig(artifact_processing_families=()),
            external_skills=ExternalSkillsConfig(),
            mcp=McpConfig(enabled=False),
            metrics=MetricsConfig(enabled=False),
        ),
        scheduler_path=directory / "scheduler.db",
    )
    budget = load_laya_budget(args.checkpoint) if config.provider == "laya" else None
    source_digests = {
        name: "sha256:" + hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
        for name in (
            "adapter.py",
            "applicability.py",
            "applicability_catalog.py",
            "applicability_fixture.py",
            "applicability_eval.py",
            "applicability_host.py",
        )
    }
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as transport,
        PowerContextClient("http://localhost", http_client=transport) as client,
        httpx.AsyncClient() as provider_client,
    ):
        scope_id, references = await seed_fixture(client)
        backend = SystemOneDecisionModel(config, provider_client, laya_budget=budget)
        records = await evaluate_cases(
            client,
            ServerCandidateCatalog(client, target),
            backend,
            scope_id,
            references,
            (profile.model_dump_json(),),
            input_price_per_million=args.input_price_per_million,
            output_price_per_million=args.output_price_per_million,
            output_directory=directory,
            codex_host=args.codex_host,
            codex_timeout=args.codex_timeout,
        )
    report = {
        "fixture_version": FIXTURE_VERSION,
        "status": "complete",
        "provider_execution": "live",
        "provider": config.provider,
        "model": config.model,
        "model_version_verified": False,
        "powercontext_transport": "ASGITransport with actual Server and SQLite",
        "host_execution": "paired real Codex CLI on change-en" if args.codex_host else "not_run",
        "scope_id": scope_id,
        "references": {name: ref.model_dump(mode="json") for name, ref in references.items()},
        "source_digests": source_digests,
        "cases": records,
    }
    path = directory / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, help="Checkpoint matching the served Laya model")
    parser.add_argument("--output", type=Path, default=Path(".powercontext/applicability"))
    parser.add_argument("--input-price-per-million", type=float)
    parser.add_argument("--output-price-per-million", type=float)
    parser.add_argument("--codex-host", action="store_true", help="Run paired real Codex host arms on change-en")
    parser.add_argument("--codex-timeout", type=int, default=180)
    args = parser.parse_args()
    if args.codex_timeout < 1:
        parser.error("--codex-timeout must be positive")
    for value in (args.input_price_per_million, args.output_price_per_million):
        if value is not None and (not math.isfinite(value) or value < 0):
            parser.error("prices must be finite and nonnegative")
    try:
        # BaseSettings obtains required fields from the dedicated environment file.
        settings = ProviderSettings(_env_file=args.env_file)  # ty: ignore[missing-argument, unknown-argument]
        config = SystemOneConfig.model_validate(settings.model_dump())
    except ValidationError:
        parser.error("Invalid SYSTEMONE_* settings; configure the dedicated provider file")
    if config.provider == "laya" and args.checkpoint is None:
        parser.error("Laya requires --checkpoint matching the served model")
    if config.provider != "laya" and args.checkpoint is not None:
        parser.error("--checkpoint only applies to Laya")
    path = asyncio.run(run(args, config))
    print(path)
    report = json.loads(path.read_text(encoding="utf-8"))
    selections_ok = all(case["assisted"]["metrics"]["selection_exact"] for case in report["cases"])
    hosts_ok = all(host["task_success"] for case in report["cases"] for host in case["hosts"].values())
    return 0 if selections_ok and hosts_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
