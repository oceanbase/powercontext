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

"""Explicit offline learning; generation and approval are separate operations."""

# ruff: noqa: TRY003 - bounded validation errors are part of this bridge's diagnostics.

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from powercontext.client import PowerContextClient
from powercontext.http import (
    CaptureContentSourceRequest,
    GeneratedCandidateResponse,
    GenerateSkillRequest,
    RecordSkillUsageRequest,
    SkillGenerationOrigin,
)
from powercontext_datus.freeze import digest_json


@dataclass(frozen=True)
class LearningEvidence:
    """Evaluator-reviewed independent example; never constructed from formal questions.

    The caller must check provenance/SQL success against its native run receipts.
    This transport type cannot certify independence or ground truth on its own.
    """

    sample_id: str
    question: str
    sql: str
    result_digest: str
    native_receipt_digest: str
    source_manifest_digest: str
    lesson: str

    def __post_init__(self) -> None:
        if any(not value.strip() for value in asdict(self).values()):
            raise ValueError("incomplete learning evidence")
        for digest in (self.result_digest, self.native_receipt_digest, self.source_manifest_digest):
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("learning evidence requires SHA-256 digests")


async def generate_from_examples(
    client: PowerContextClient, *, scope_id: str, examples: list[LearningEvidence]
) -> GeneratedCandidateResponse:
    """Capture bounded independent evidence and request a pending Skill Candidate."""
    if not 1 <= len(examples) <= 32 or len({e.sample_id for e in examples}) != len(examples):
        raise ValueError("supply 1-32 distinct independent examples")
    refs = []
    for example in examples:
        payload = asdict(example)
        source = await client.capture_content_source(
            CaptureContentSourceRequest(
                scope_id=scope_id,
                source_id="datus-learning-" + digest_json(payload),
                content=json.dumps(payload, ensure_ascii=False, sort_keys=True),
                metadata={"integration": "datus", "phase": "independent_learning"},
            )
        )
        refs.append(source.source)
    return await client.generate_skill(
        GenerateSkillRequest(
            scope_id=scope_id,
            origin=SkillGenerationOrigin.SOURCE,
            source_refs=refs,
            artifact_refs=[],
            reason="Derive reusable read-only SQL guidance from verified independent Datus executions.",
        )
    )


async def publish_completed_usage(
    client: PowerContextClient, records: list[RecordSkillUsageRequest], *, paired_run_complete: bool
) -> None:
    """Flush evaluator-held usage only after both frozen arms have finished.

    The evaluator, not the running Agent, owns the completion decision.
    """
    if not paired_run_complete:
        raise ValueError("usage must remain isolated until both arms complete")
    for record in records:
        await client.record_skill_usage(record)
