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

"""Real capture/readback and extraction, with evaluation-local evidence views."""

from __future__ import annotations

import asyncio
import hashlib
import subprocess
import uuid
from contextlib import AsyncExitStack
from pathlib import Path
from time import perf_counter
from typing import Any, cast

from .host import PARSER_VERSION, Turn, current_turn, load_hook
from .models import ARMS, Case, Manifest, RunConfig, fingerprint, write_json


def prompt_definition(config: RunConfig) -> Any:
    from powercontext.builtin.artifacts.memory.prompts import MemoryExtractionProfile
    from powercontext.builtin.artifacts.prompt.builtin import builtin_prompt_definitions

    return next(
        item
        for item in builtin_prompt_definitions(MemoryExtractionProfile(config.profile))
        if item.key == "atomic_memory.extract"
    )


def freeze(root: Path, manifest: Manifest, config: RunConfig) -> dict[str, Any]:
    definition = prompt_definition(config)
    paths = {"pyproject.toml", "evaluation/pyproject.toml", "uv.lock", "evaluation/uv.lock"}
    # Include working-file contents and file inventory, not just the committed HEAD.
    # Runtime resources can change prompts/schema behavior just as Python sources can.
    runtime = root / "src/powercontext"
    paths.update(
        path.relative_to(root).as_posix()
        for path in runtime.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}
    )
    for directory in (
        "integrations/workbuddy/plugins/powercontext",
        "evaluation/memory/capture_ownership/src",
        "evaluation/memory/capture_ownership/scripts",
    ):
        paths.update(
            path.relative_to(root).as_posix()
            for path in (root / directory).rglob("*.py")
            if "__pycache__" not in path.parts
        )
    files = {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in sorted(paths)}
    return {
        "schema_version": "capture-ownership.lock.v1",
        "manifest_sha256": fingerprint(manifest.model_dump(mode="json")),
        "config": config.model_dump(mode="json"),
        "code_sha": subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip(),
        "file_sha256": files,
        "parser_version": PARSER_VERSION,
        "prompt_version": definition.builtin_version,
        "prompt_sha256": hashlib.sha256(definition.default_instructions.encode()).hexdigest(),
        "decision_policy": {
            "critical_repetitions": 2,
            "required_current_retention": 1.0,
            "critical_candidate_arm_contamination": 0,
            "critical_labels": "human_review_against_frozen_claims",
        },
    }


def verify_lock(root: Path, manifest: Manifest, lock: dict[str, Any]) -> RunConfig:
    config = RunConfig.model_validate(lock["config"])
    if lock != freeze(root, manifest, config):
        raise ValueError("manifest, prompt, configuration, or code differs from the frozen experiment")
    return config


def candidate_evidence(evidence: Any, turn: Turn, prompt: str, materialization_hash: str) -> Any:
    """Replace the entire evaluation view, including the duplicate raw metadata carrier.

    No Source object, repository row, registry, or production projection is modified.
    Source identity and evidence ID remain exactly those of the control arm.
    """

    return evidence.model_copy(
        update={
            "content": {
                "source_type": evidence.source_ref.source_type,
                "source_id": evidence.source_ref.source_id,
                "content": turn.text(prompt),
                "metadata": {},
            },
            "source_metadata": {
                "projection_version": "host-turn-candidate-v1",
                "parser_version": PARSER_VERSION,
                "materialization_sha256": materialization_hash,
                "ownership": turn.status,
                "char_start": turn.start,
                "char_end": turn.end,
            },
        }
    )


def occupancy(case: Case, evidence: Any, turn: Turn) -> dict[str, Any]:
    """Count labeled source bytes in every raw-text carrier, not JSON escaping overhead."""

    full = (0, len(case.prompt))
    selected = None if turn.start is None or turn.end is None else (turn.start, turn.end)
    by_owner = {span.owner: 0 for span in case.spans}
    carriers = 0

    def visit(value: Any) -> None:
        nonlocal carriers
        if isinstance(value, dict):
            for child in value.values():
                visit(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)
        elif isinstance(value, str):
            region = (
                full if value == case.prompt else selected if selected and value == turn.text(case.prompt) else None
            )
            if region is None or region[0] == region[1]:
                return
            carriers += 1
            for span in case.spans:
                start, end = max(region[0], span.start), min(region[1], span.end)
                if start < end:
                    by_owner[span.owner] += len(case.prompt[start:end].encode())

    visit(evidence.model_dump(mode="json"))
    return {
        "labeled_utf8_bytes": by_owner,
        "text_carriers": carriers,
        "serialized_utf8_bytes": len(evidence.model_dump_json().encode()),
    }


async def _model(config: RunConfig, mode: str, env_file: Path | None, resources: AsyncExitStack) -> Any:
    if mode == "offline":
        from pydantic_ai.messages import ModelResponse, TextPart
        from pydantic_ai.models.function import FunctionModel

        async def empty(messages: Any, info: Any) -> ModelResponse:
            return ModelResponse(parts=[TextPart('{"candidates":[]}')])

        return FunctionModel(empty)
    if config.model is None or env_file is None:
        raise ValueError("live generation requires a frozen model and an explicit generation environment file")
    from powercontext.builtin.runtime.composition import _open_pydantic_ai_model
    from powercontext.server.configuration import server_settings_context

    settings = resources.enter_context(server_settings_context(env_file=env_file))
    inference = settings.inference
    base_url = None if inference.generation_base_url is None else str(inference.generation_base_url)
    if (inference.generation_model, base_url) != (config.model, config.base_url):
        raise ValueError("generation provider differs from the frozen model configuration")
    if dict(inference.generation_model_settings) != config.model_settings:
        raise ValueError("generation settings differ from the frozen model configuration")
    # Header secrets may be used by the adapter, but are never serialized into run artifacts.
    _, model = await _open_pydantic_ai_model(
        config.model,
        base_url=inference.generation_base_url,
        headers=inference.generation_headers,
        resources=resources,
        instrumentation=None,
        disable_provider_retries=True,
    )
    return model


class MeasuredGenerator:
    """Observe the real structured adapter's portable usage without changing its input."""

    def __init__(self, generator: Any) -> None:
        self.generator = generator
        self.usage: dict[str, Any] | None = None

    async def generate(self, value: Any, /) -> Any:
        self.usage = None
        result = await self.generator.generate(value)
        self.usage = result.usage.model_dump(mode="json")
        return result


def capture(client: Any, hook: Any, case: Case, scope_id: str) -> dict[str, Any]:
    """Invoke the actual hook; substitute only its network transport with real ASGI HTTP."""

    def post(path: str, body: Any, **kwargs: Any) -> Any:
        response = client.post(path, json=body)
        response.raise_for_status()
        return response.json()

    previous = hook._post_json
    hook._post_json = post
    try:
        return hook._capture_prompt(
            {"session_id": "capture-ownership", "prompt_id": case.case_id},
            prompt=case.prompt,
            cwd="/synthetic/workspace",
            scope_id=scope_id,
            settings=None,
            deadline=0,
        )
    finally:
        hook._post_json = previous


async def run_experiment(
    root: Path,
    manifest: Manifest,
    lock: dict[str, Any],
    private_dir: Path,
    *,
    mode: str,
    env_file: Path | None = None,
) -> dict[str, Any]:
    """Run on an isolated real Server application and a fresh file-backed SQLite database."""

    from fastapi.testclient import TestClient
    from powercontext.builtin.artifacts.atomic_memory.extraction import (
        AtomicMemoryExtractionInput,
        AtomicMemoryExtractionOutput,
        AtomicMemoryGenerationPipeline,
        project_atomic_memory_evidence,
    )
    from powercontext.builtin.artifacts.atomic_memory.reconciliation import (
        AtomicMemoryReconciliationInput,
        AtomicMemoryReconciliationOutput,
    )
    from powercontext.builtin.inference import character_token_estimator
    from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIStructuredGenerator
    from powercontext.builtin.persistence.sources import SourceRepository
    from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
    from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY
    from powercontext.builtin.sources.content import ContentSource
    from powercontext.server.factory import create_server_app
    from powercontext.server.settings import ServerSettings
    from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, SourceRef, TextEvidence
    from pydantic_ai.settings import ModelSettings

    config = verify_lock(root, manifest, lock)
    if mode == "live" and not (config.input_cny_per_million > 0 and config.output_cny_per_million > 0):
        raise ValueError("live runs require frozen input/output prices for the cost guard")
    private_dir = private_dir.resolve()
    if private_dir == root.resolve() or root.resolve() in private_dir.parents:
        raise ValueError("raw run artifacts must be outside the repository")
    private_dir.mkdir(parents=True, exist_ok=False)
    private_dir.chmod(0o700)
    write_json(private_dir / "manifest.json", manifest.model_dump(mode="json"))
    write_json(private_dir / "lock.json", lock)
    hook = load_hook(root)
    database = SQLiteConfig(url=f"sqlite+aiosqlite:///{private_dir / 'sources.db'}")
    settings = ServerSettings(database=database)
    definition = prompt_definition(config)
    estimator = character_token_estimator()
    rows: list[dict[str, Any]] = []
    reserved_cost = 0.0
    last_request = 0.0
    output_cap = config.model_settings["max_tokens"]
    if not isinstance(output_cap, int):
        raise TypeError("output cap must be an integer")
    source_audit = []
    async with AsyncExitStack() as resources:
        model = await _model(config, mode, env_file, resources)
        generator = PydanticAIStructuredGenerator(
            model=model,
            instructions=definition.default_instructions,
            input_type=AtomicMemoryExtractionInput,
            output_type=AtomicMemoryExtractionOutput,
            model_settings=cast(ModelSettings, config.model_settings),
            limits=InferenceLimits(
                timeout_seconds=config.timeout_seconds, max_requests=config.max_requests, allow_continuations=False
            ),
        )
        measured = MeasuredGenerator(generator)
        reconciler = PydanticAIStructuredGenerator(
            model=model,
            instructions="Not invoked by this extraction-only evaluation.",
            input_type=AtomicMemoryReconciliationInput,
            output_type=AtomicMemoryReconciliationOutput,
        )
        pipeline = AtomicMemoryGenerationPipeline(
            extractor=measured,
            reconciler=reconciler,
            estimator=estimator,
            extraction_instructions=definition.default_instructions,
        )
        # Real ASGI HTTP routes and application lifespan, not a mocked capture handler.
        with TestClient(create_server_app(settings=settings)) as client:
            response = client.post(
                "/v1/scopes",
                json={
                    "title": "Capture ownership replay",
                    "summary": "Isolated synthetic/private evaluation",
                    "idempotency_key": f"capture-ownership-{uuid.uuid4().hex}",
                },
            )
            response.raise_for_status()
            scope_id = response.json()["scope_id"]
            repository = SourceRepository(BUILTIN_SOURCE_REGISTRY)
            async with SQLiteProfile.open_readonly(database) as profile:
                for case in manifest.cases:
                    receipt_json = capture(client, hook, case, scope_id)
                    repeat = capture(client, hook, case, scope_id)
                    if (receipt_json["source"], receipt_json["position"]) != (repeat["source"], repeat["position"]):
                        raise ValueError("capture replay changed Source identity or journal position")
                    ref = SourceRef(
                        source_type=receipt_json["source"]["name"], source_id=receipt_json["source"]["source_id"]
                    )
                    async with profile.database.transaction() as connection:
                        stored = await repository.get(connection, scope_id, ref)
                    if not isinstance(stored.value, ContentSource) or stored.value.content != case.prompt:
                        raise ValueError("Source readback does not preserve raw host submission")
                    text_projection = BUILTIN_SOURCE_REGISTRY.project(stored.value, TEXT_EVIDENCE_PROJECTION_KEY)
                    if TextEvidence.model_validate(text_projection).content != case.prompt:
                        raise ValueError("registered text projection differs from the captured Source")
                    digest = fingerprint(stored.value.model_dump(mode="json"))
                    original = await project_atomic_memory_evidence(stored, repository, evidence_id="source-1")
                    turn = current_turn(case.prompt, hook)
                    source_audit.append(
                        {
                            "case_id": case.case_id,
                            "scope_id": scope_id,
                            "source": stored.model_dump(mode="json"),
                            "text_projection": text_projection,
                        }
                    )
                    for repetition in range(config.repetitions):
                        for arm in ARMS:
                            evidence = (
                                original if arm == ARMS[0] else candidate_evidence(original, turn, case.prompt, digest)
                            )
                            request = AtomicMemoryExtractionInput(evidence=(evidence,))
                            tokens = estimator.estimate(
                                definition.default_instructions + "\n" + request.model_dump_json()
                            )
                            disposition = "skipped" if arm == ARMS[1] and turn.status != "eligible" else "completed"
                            row: dict[str, Any] = {
                                "case_id": case.case_id,
                                "arm": arm,
                                "repetition": repetition,
                                "mode": mode,
                                "status": disposition,
                                "turn_status": turn.status,
                                "turn_reason": turn.reason,
                                "parser_matches_ground_truth": turn.status == case.expected_turn,
                                "source_sha256": fingerprint(ref.model_dump(mode="json")),
                                "materialization_sha256": digest,
                                "source_position": stored.journal_position,
                                "evidence_id": original.evidence_id,
                                "projection_version": "powercontext.text-evidence.v1",
                                "materialization_path": type(stored.value).__name__,
                                "input_sha256": fingerprint(request.model_dump(mode="json")),
                                "input": request.model_dump(mode="json"),
                                "occupancy": occupancy(case, evidence, turn),
                                "estimated_input_tokens": tokens,
                                "candidates": [],
                                "provider_usage": None,
                            }
                            if mode == "live" and disposition == "completed":
                                await asyncio.sleep(
                                    max(0, config.request_interval_seconds - (perf_counter() - last_request))
                                )
                            started = perf_counter()
                            # UTF-8 bytes plus schema/framing allowance is deliberately more conservative
                            # than the runtime's character estimate. This is an estimate, not provider billing.
                            budget_input = (
                                len((definition.default_instructions + request.model_dump_json()).encode()) + 8192
                            )
                            reservation = (
                                config.max_requests
                                * (
                                    budget_input * config.input_cny_per_million
                                    + output_cap * config.output_cny_per_million
                                )
                                / 1_000_000
                            )
                            row["cost_reservation_cny"] = 0.0
                            if (
                                disposition == "completed"
                                and tokens > config.max_input_tokens
                                or disposition == "completed"
                                and mode == "live"
                                and reserved_cost + reservation > config.max_cost_cny
                            ):
                                row["status"] = "budget_rejected"
                            elif disposition == "completed":
                                if mode == "live":
                                    reserved_cost += reservation
                                    row["cost_reservation_cny"] = reservation
                                    last_request = perf_counter()
                                try:
                                    # Keep the runtime evidence-ID validation in the actual pipeline.
                                    candidates = await pipeline.extract(request)
                                    row["candidates"] = [item.model_dump(mode="json") for item in candidates]
                                except Exception as error:  # noqa: BLE001 -- private failure class, never raw provider text.
                                    row["status"] = "generation_failed"
                                    row["failure_class"] = type(error).__name__
                                    cause: BaseException | None = error
                                    while cause is not None:
                                        status = getattr(cause, "status_code", None)
                                        if isinstance(status, int):
                                            row["provider_http_status"] = status
                                        cause = cause.__cause__
                                finally:
                                    # Evidence-ID validation can fail after generation returned known usage.
                                    row["provider_usage"] = measured.usage if mode == "live" else None
                            row["generation_seconds"] = perf_counter() - started
                            row["output_sha256"] = fingerprint(row["candidates"])
                            rows.append(row)
                            # A partial run remains inspectable, but never receives a completed run manifest.
                            write_json(private_dir / "partial-results.json", rows)
    result = {
        "schema_version": "capture-ownership.run.v1",
        "lock_sha256": fingerprint(lock),
        "manifest_sha256": lock["manifest_sha256"],
        "mode": mode,
        "rows": rows,
        "raw_sources_sha256": fingerprint(source_audit),
        "complete": True,
    }
    write_json(private_dir / "sources.json", source_audit)
    write_json(private_dir / "run.json", result)
    return result
