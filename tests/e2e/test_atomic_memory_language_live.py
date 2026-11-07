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

"""Opt-in live language policy checks; no persistence or host qualification is implied."""

from __future__ import annotations

import asyncio
import logging
import re
from contextlib import AsyncExitStack
from pathlib import Path
from typing import cast

import pytest
from pydantic_ai.settings import ModelSettings

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryEvidence,
    AtomicMemoryExtractionInput,
    AtomicMemoryExtractionOutput,
    AtomicMemoryGenerationPipeline,
    atomic_memory_extraction_instructions,
    validate_extraction_output,
)
from powercontext.builtin.artifacts.atomic_memory.models import (
    AtomicMemoryContent,
    AtomicMemoryRead,
    AtomicMemoryStateValue,
)
from powercontext.builtin.artifacts.atomic_memory.reconciliation import (
    ATOMIC_MEMORY_RECONCILIATION_INSTRUCTIONS,
    AtomicMemoryArtifactEvidence,
    AtomicMemoryComparisonItem,
    AtomicMemoryReconciliationInput,
    AtomicMemoryReconciliationOutput,
    validate_reconciliation_output,
)
from powercontext.builtin.artifacts.memory.prompts import MemoryExtractionProfile
from powercontext.builtin.inference import InvalidInferenceOutputError, character_token_estimator
from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIStructuredGenerator
from powercontext.builtin.runtime.composition import _open_pydantic_ai_model
from powercontext.server.configuration import server_settings_context
from powercontext.sources import SourceRef

pytestmark = pytest.mark.real_e2e

_STATEMENTS = (
    ("cn", "小北项目的长期提交策略 patch_gate_cn_6842 是每完成一个小改动就提交一次。", "patch_gate_cn_6842"),
    (
        "en",
        "Project Elm requires code review before every deployment under policy review_gate_en_7319.",
        "review_gate_en_7319",
    ),
    ("mixed", "松果项目的 worker_pool_limit 固定为 7。", "worker_pool_limit"),
)


def _source(identity: str, text: str, position: int) -> AtomicMemoryEvidence:
    return AtomicMemoryEvidence(
        evidence_id=identity,
        source_ref=SourceRef(source_type="content", source_id=identity),
        journal_position=position,
        content=text,
        source_metadata={},
    )


def _check_language(identity: str, text: str, marker: str) -> None:
    assert marker in text, "exact policy identifier was changed"
    chinese = re.findall(r"[\u4e00-\u9fff]", text)
    if identity == "en":
        assert not chinese, "English fact adopted the language of neighboring Sources"
    else:
        assert len(chinese) >= 6, "Chinese fact was translated instead of retaining its natural-language framing"
    if identity == "mixed":
        assert "7" in text, "literal configuration value was changed"


@pytest.mark.parametrize("profile", tuple(MemoryExtractionProfile))
def test_live_extraction_and_reconciliation_preserve_each_facts_source_language(profile, pytestconfig):
    if not pytestconfig.getoption("run_real_e2e"):
        pytest.skip("requires --run-real-e2e and an explicitly configured provider")
    previous_logging = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    failure = None
    try:
        asyncio.run(_exercise(profile, Path(pytestconfig.getoption("real_e2e_env_file"))))
    except AssertionError as error:
        failure = str(error)
    except InvalidInferenceOutputError as error:
        failure = f"{error.operation}: {error.detail}"
    except Exception as error:
        # Provider errors can include credentials or request bodies.
        failure = type(error).__name__
    finally:
        logging.disable(previous_logging)
    if failure is not None:
        pytest.fail(f"live Atomic Memory language policy failed: {failure}", pytrace=False)


async def _exercise(profile: MemoryExtractionProfile, env_file: Path) -> None:
    with server_settings_context(env_file=env_file) as settings:
        assert settings.inference.generation_model is not None, "generation model is required"
        async with AsyncExitStack() as resources:
            _, model = await _open_pydantic_ai_model(
                settings.inference.generation_model,
                base_url=settings.inference.generation_base_url,
                headers=settings.inference.generation_headers,
                resources=resources,
                instrumentation=None,
            )
            limits = InferenceLimits(timeout_seconds=settings.inference.generation_timeout_seconds, max_requests=2)
            model_settings = cast(ModelSettings, dict(settings.inference.generation_model_settings)) or None
            instructions = atomic_memory_extraction_instructions(profile)
            extractor = PydanticAIStructuredGenerator(
                model=model,
                instructions=instructions,
                input_type=AtomicMemoryExtractionInput,
                output_type=AtomicMemoryExtractionOutput,
                limits=limits,
                model_settings=model_settings,
                output_check=validate_extraction_output,
            )
            reconciler = PydanticAIStructuredGenerator(
                model=model,
                instructions=ATOMIC_MEMORY_RECONCILIATION_INSTRUCTIONS,
                input_type=AtomicMemoryReconciliationInput,
                output_type=AtomicMemoryReconciliationOutput,
                limits=limits,
                model_settings=model_settings,
            )
            pipeline = AtomicMemoryGenerationPipeline(
                extractor=extractor,
                reconciler=reconciler,
                estimator=character_token_estimator(),
                extraction_instructions=instructions,
            )
            evidence = tuple(_source(identity, text, index) for index, (identity, text, _) in enumerate(_STATEMENTS, 1))
            candidates = await pipeline.extract(AtomicMemoryExtractionInput(evidence=evidence))
            for identity, _, marker in _STATEMENTS:
                matching = [item for item in candidates if marker in item.text and identity in item.evidence_ids]
                assert len(matching) == 1, "Source fact missing, duplicated or unsupported"
                candidate = matching[0]
                _check_language(identity, candidate.text, marker)
                proposal = AtomicMemoryComparisonItem(
                    item_id=f"candidate:{identity}",
                    kind=candidate.kind,
                    text=candidate.text,
                    original_refs=(),
                    evidence_ids=candidate.evidence_ids,
                )
                request = AtomicMemoryReconciliationInput(proposal=proposal, related=(), evidence=evidence)
                output = (await reconciler.generate(request)).output
                validate_reconciliation_output(request, output)
                assert output.action == "create" and output.content is not None, "independent Source fact was discarded"
                assert identity in output.evidence_ids, "reconciliation lost its actual Source evidence"
                _check_language(identity, output.content.text, marker)

            # A new translation of an already supported fact is not permission to rewrite a published identity.
            reference = ArtifactRef(family="atomic-memory", artifact_id="existing-approach", revision=1)
            existing = AtomicMemoryContent(
                kind="preference",
                text="Project Larch uses planning, multiple workers for implementation and multiple reviews.",
            )
            new_source = _source("new-cn", "Project Larch 采用规划、多个 worker 实施、多个 review 的工作方式。", 4)
            related = AtomicMemoryComparisonItem(
                item_id="published",
                kind=existing.kind,
                text=existing.text,
                original_refs=(AtomicMemoryRead(ref=reference, state=AtomicMemoryStateValue.ACTIVE, state_version=0),),
                evidence_ids=("existing-evidence",),
            )
            request = AtomicMemoryReconciliationInput(
                proposal=AtomicMemoryComparisonItem(
                    item_id="translated-proposal",
                    kind="preference",
                    text=str(new_source.content),
                    original_refs=(),
                    evidence_ids=(new_source.evidence_id,),
                ),
                related=(related,),
                evidence=(
                    new_source,
                    AtomicMemoryArtifactEvidence(
                        evidence_id="existing-evidence", artifact_ref=reference, content=existing
                    ),
                ),
            )
            output = (await reconciler.generate(request)).output
            validate_reconciliation_output(request, output)
            assert output.action == "noop" and output.target_ids == (related.item_id,), (
                "translation rewrote an unchanged fact"
            )
