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

"""Synthetic, bilingual known-answer cases and explicitly reviewed standard packages."""

from __future__ import annotations

import base64
import io
import json
import uuid
import zipfile
from dataclasses import dataclass
from typing import Literal

from powercontext.client import PowerContextClient
from powercontext.http import (
    ApproveArtifactCandidateRequest,
    ArtifactReference,
    CaptureContentSourceRequest,
    CreateScopeRequest,
    ExperienceProposal,
    ProposeExperienceRequest,
    ProposeSkillPackageRequest,
)

FIXTURE_VERSION = "powercontext.applicability.fixture.v1"


@dataclass(frozen=True)
class EvaluationCase:
    name: str
    task: str
    expected: tuple[str, ...]
    query: str = "HTTP contract"
    applicable: tuple[str, ...] | None = None
    status: Literal["selected", "none", "uncertain"] | None = None


CASES = (
    EvaluationCase(
        "change-en",
        "Add an optional string request_id to the HTTP response contract and regenerate its Python client.",
        ("generation-lesson", "validation-lesson", "http-contract-generation"),
        applicable=("generation-lesson", "validation-lesson", "http-general-maintenance", "http-contract-generation"),
    ),
    EvaluationCase(
        "change-zh",
        "修改 HTTP 响应契约。添加可选的字符串字段 request_id。重新生成 Python 客户端。",
        ("generation-lesson", "validation-lesson", "http-contract-generation"),
        applicable=("generation-lesson", "validation-lesson", "http-general-maintenance", "http-contract-generation"),
    ),
    EvaluationCase(
        "explain-en", "Only explain what the HTTP endpoint does. Do not change files or run generators.", ()
    ),
    EvaluationCase("explain-zh", "只解释 HTTP 接口的用途。无需修改文件或运行生成器。", ()),
    EvaluationCase("no-fit-en", "Translate this poem into French.", ()),
    EvaluationCase("no-fit-zh", "把这首诗翻译成法语。", ()),
    EvaluationCase(
        "missing-condition-en",
        "Deploy the unchanged HTTP contract. The deployment approval is unknown.",
        (),
        status="uncertain",
    ),
    EvaluationCase("missing-condition-zh", "部署未改动的 HTTP 契约。目前不知道部署是否获批。", (), status="uncertain"),
    EvaluationCase("known-mismatch-en", "Deploy the unchanged HTTP contract. Deployment approval was denied.", ()),
    EvaluationCase("known-mismatch-zh", "部署未改动的 HTTP 契约。部署审批已被拒绝。", ()),
)

EXPERIENCES = {
    "generation-lesson": ExperienceProposal(
        situation="When modifying an HTTP contract and regenerating its client code.",
        action="Edit the contract source, then run the client generator; never hand-edit generated files.",
        outcome="The generated Python client matched the HTTP contract.",
        lesson="For HTTP contract changes, generate client code from the authoritative schema.",
    ),
    "validation-lesson": ExperienceProposal(
        situation="When changing an HTTP contract and regenerating client code.",
        action="After generation, run contract tests and verify exact request and response fields.",
        outcome="A mismatched optional response field was detected before review.",
        lesson="HTTP contract generation and contract validation are complementary steps.",
    ),
}
SKILLS = {
    "http-general-maintenance": (
        "General HTTP contract maintenance when files need modification.",
        "Use only for modifying HTTP-related project files. Inspect the task, edit the owning source, "
        "and run its tests. This general procedure is less specific than contract generation. "
        "Do not use for explanation, translation, or deployment of an unchanged contract.",
    ),
    "http-contract-generation": (
        "HTTP contract generation for a changed API contract and its Python client.",
        "Use only when the task requires changing the HTTP contract and regenerating its client. "
        "Edit openapi/powercontext.yaml, run make api-generate, then make contract-test. "
        "Do not use for explanation-only requests or deployment of an unchanged contract.",
    ),
    "http-contract-deployment": (
        "HTTP contract deployment after explicit deployment approval.",
        "Use only to deploy an unchanged HTTP contract when deployment approval is explicitly granted. "
        "If approval is unknown, do not assume it. If approval is denied, this procedure does not apply. "
        "Do not use for contract modification, generation, explanation, or translation.",
    ),
}


def skill_archive(name: str, description: str, instructions: str, *, incompatible: bool = False) -> bytes:
    """Build a standard package without executing any package content."""

    files = {"SKILL.md": f"---\nname: {name}\ndescription: {description}\n---\n\n{instructions}\n".encode()}
    if incompatible:
        files["scripts/run.py"] = b"print('This fixture must never execute')\n"
        files["powercontext.runtime.yaml"] = json.dumps({
            "schema": "powercontext.skill-runtime.v1",
            "variants": [
                {
                    "id": "linux",
                    "entrypoint": "scripts/run.py",
                    "interpreter": "python",
                    "requirements": {"operating_systems": ["linux"], "commands": {"python": ">=3.14"}},
                }
            ],
        }).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


async def seed_fixture(client: PowerContextClient) -> tuple[str, dict[str, ArtifactReference]]:
    """Explicitly create synthetic records in a fresh isolated Scope using public write APIs."""

    scope = await client.create_scope(
        CreateScopeRequest(
            title="Applicability evaluation",
            summary="Synthetic HTTP contract selection cases.",
            idempotency_key="applicability:" + uuid.uuid4().hex,
        )
    )
    scope_id = scope.scope_id
    source = await client.capture_content_source(
        CaptureContentSourceRequest(
            scope_id=scope_id,
            source_id="fixture-evidence",
            content="Synthetic observed task outcome: HTTP contract generation and validation both passed.",
        )
    )
    references: dict[str, ArtifactReference] = {}
    for name, proposal in EXPERIENCES.items():
        pending = await client.propose_experience(
            ProposeExperienceRequest(
                scope_id=scope_id,
                proposal=proposal,
                source_refs=[source.source],
                artifact_refs=[],
            )
        )
        approved = await client.approve_artifact_candidate(
            ApproveArtifactCandidateRequest(
                scope_id=scope_id,
                candidate_id=pending.candidate_id,
                expected_version=pending.version,
            )
        )
        if approved.result_artifact is None:
            raise ValueError("fixture Experience approval produced no exact reference")  # noqa: TRY003
        references[name] = approved.result_artifact
    for name, (description, instructions) in SKILLS.items():
        pending = await client.propose_skill_package(
            ProposeSkillPackageRequest(
                scope_id=scope_id,
                archive_base64=base64.b64encode(skill_archive(name, description, instructions)).decode(),
                reason="Explicit review of a synthetic evaluation package.",
            )
        )
        approved = await client.approve_artifact_candidate(
            ApproveArtifactCandidateRequest(
                scope_id=scope_id,
                candidate_id=pending.candidate_id,
                expected_version=pending.version,
            )
        )
        if approved.result_artifact is None:
            raise ValueError("fixture Skill approval produced no exact reference")  # noqa: TRY003
        references[name] = approved.result_artifact
    return scope_id, references
