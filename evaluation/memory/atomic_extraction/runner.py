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

"""Opt-in real-provider runner with private generated output."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from pydantic import JsonValue
from pydantic_ai.settings import ModelSettings

from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryEvidence,
    AtomicMemoryExtractionInput,
    AtomicMemoryExtractionOutput,
)
from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemoryContent
from powercontext.builtin.artifacts.memory import MemoryExtractionProfile
from powercontext.builtin.artifacts.prompt.builtin import builtin_prompt_definitions
from powercontext.builtin.inference import InferenceUsage
from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIStructuredGenerator
from powercontext.builtin.runtime.composition import _open_pydantic_ai_model
from powercontext.server.configuration import server_settings_context
from powercontext.sources import SourceRef

from .manifest import LoadedManifest
from .models import (
    RUN_MANIFEST_SCHEMA,
    CandidateObservation,
    CaseObservation,
    EvaluationCase,
    LiveRunManifest,
    PromptRunIdentity,
    safe_model_settings,
)
from .report import build_report, load_observations, write_report


async def run_live_evaluation(
    *,
    loaded: LoadedManifest,
    env_file: Path,
    output_directory: Path,
    run_id: str,
) -> dict[str, Any]:
    """Run every pinned case once and write private observations plus safe summaries."""

    with server_settings_context(env_file=_resolve(env_file)) as settings:
        inference = settings.inference
        if inference.generation_model is None:
            raise ValueError("live evaluation requires a configured generation model")  # noqa: TRY003
        run_manifest = _run_manifest(loaded, run_id=run_id, inference=inference)
        output = _prepare_output_directory(output_directory)
        _write_json(output / "run-manifest.json", run_manifest.model_dump(mode="json"))
        observations_path = output / "observations.private.jsonl"
        observed: set[str] = set()
        with observations_path.open("x", encoding="utf-8", newline="") as stream:
            try:
                async with AsyncExitStack() as resources:
                    _, model = await _open_pydantic_ai_model(
                        inference.generation_model,
                        base_url=inference.generation_base_url,
                        headers=inference.generation_headers,
                        resources=resources,
                        instrumentation=None,
                    )
                    generators = {
                        profile: PydanticAIStructuredGenerator(
                            model=model,
                            instructions=_prompt_definition(profile).default_instructions,
                            input_type=AtomicMemoryExtractionInput,
                            output_type=AtomicMemoryExtractionOutput,
                            limits=InferenceLimits(
                                timeout_seconds=inference.generation_timeout_seconds,
                                max_requests=inference.generation_max_requests,
                            ),
                            model_settings=cast(ModelSettings, dict(inference.generation_model_settings)) or None,
                            name=f"atomic_memory_extraction_eval_{profile.value}",
                        )
                        for profile in MemoryExtractionProfile
                    }
                    for case in loaded.manifest.cases:
                        observation = await _observe_case(case, generators[case.profile])
                        _append_observation(stream, observation)
                        observed.add(case.case_id)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                for case in loaded.manifest.cases:
                    if case.case_id not in observed:
                        _append_observation(
                            stream,
                            CaseObservation(
                                case_id=case.case_id,
                                status="failed",
                                error_class=type(error).__name__,
                            ),
                        )

    public_manifest = run_manifest.model_dump(mode="json")
    observations = load_observations(observations_path)
    report = build_report(loaded, observations, public_manifest)
    write_report(output, report)
    return report


async def _observe_case(
    case: EvaluationCase,
    generator: PydanticAIStructuredGenerator[AtomicMemoryExtractionInput, AtomicMemoryExtractionOutput],
) -> CaseObservation:
    try:
        result = await generator.generate(_extraction_input(case))
        for candidate in result.output.candidates:
            AtomicMemoryContent(kind=candidate.kind, text=candidate.text, description=candidate.description)
        return CaseObservation(
            case_id=case.case_id,
            status="succeeded",
            candidates=tuple(CandidateObservation.from_candidate(item) for item in result.output.candidates),
            usage=result.usage,
        )
    except asyncio.CancelledError:
        raise
    except Exception as error:  # Provider details can contain credentials; retain only the exception type.
        return CaseObservation(
            case_id=case.case_id,
            status="failed",
            usage=InferenceUsage(requests=0),
            error_class=type(error).__name__,
        )


def _extraction_input(case: EvaluationCase) -> AtomicMemoryExtractionInput:
    return AtomicMemoryExtractionInput(
        evidence=tuple(
            AtomicMemoryEvidence(
                evidence_id=item.evidence_id,
                source_ref=SourceRef(source_type="content", source_id=f"eval-{case.case_id}-{index}"),
                journal_position=index,
                content=item.content,
                source_metadata=item.source_metadata,
            )
            for index, item in enumerate(case.evidence, 1)
        )
    )


def _run_manifest(loaded: LoadedManifest, *, run_id: str, inference: Any) -> LiveRunManifest:
    model = inference.generation_model
    provider = model.split(":", 1)[0] if ":" in model else "inferred"
    base_url = None if inference.generation_base_url is None else str(inference.generation_base_url)
    return LiveRunManifest.model_validate({
        "schema_version": RUN_MANIFEST_SCHEMA,
        "run_id": run_id,
        "started_at": datetime.now(UTC).isoformat(),
        "dataset": {
            "schema_version": loaded.manifest.schema_version,
            "dataset_version": loaded.manifest.dataset_version,
            "manifest_sha256": loaded.sha256,
            "case_count": len(loaded.manifest.cases),
        },
        "prompts": {
            profile.value: identity.model_dump(mode="json") for profile, identity in prompt_run_identities().items()
        },
        "provider": {
            "provider": provider,
            "model": model,
            "base_url_configured": base_url is not None,
            "base_url_sha256": None if base_url is None else sha256(base_url.encode()).hexdigest(),
            "header_names": sorted(inference.generation_headers),
            "model_settings": _safe_settings(inference.generation_model_settings),
            "model_settings_sha256": _value_sha256(inference.generation_model_settings),
            "timeout_seconds": inference.generation_timeout_seconds,
            "max_requests": inference.generation_max_requests,
        },
    })


def prompt_run_identities() -> dict[MemoryExtractionProfile, PromptRunIdentity]:
    """Return the exact current Prompt identities accepted for pinning."""

    prompts: dict[MemoryExtractionProfile, PromptRunIdentity] = {}
    for profile in MemoryExtractionProfile:
        definition = _prompt_definition(profile)
        instructions = definition.default_instructions.encode("utf-8")
        prompts[profile] = PromptRunIdentity.model_validate({
            "key": definition.key,
            "definition_version": definition.definition_version,
            "builtin_version": definition.builtin_version,
            "instructions_sha256": sha256(instructions).hexdigest(),
            "compiled_digest": definition.resolve("evaluation", None).compiled_digest,
        })
    return prompts


def _prompt_definition(profile: MemoryExtractionProfile):
    return next(item for item in builtin_prompt_definitions(profile) if item.key == "atomic_memory.extract")


def _safe_settings(value: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    return safe_model_settings(value)


def _value_sha256(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _append_observation(stream: Any, observation: CaseObservation) -> None:
    stream.write(observation.model_dump_json() + "\n")
    stream.flush()


def _prepare_output_directory(path: Path) -> Path:
    output = path.resolve()
    try:
        output.mkdir(parents=True, exist_ok=False)
    except FileExistsError as error:
        raise ValueError(f"refusing to overwrite an existing run directory: {output}") from error  # noqa: TRY003
    return output


def _resolve(path: Path) -> Path:
    return path.resolve()


__all__ = ["prompt_run_identities", "run_live_evaluation"]
