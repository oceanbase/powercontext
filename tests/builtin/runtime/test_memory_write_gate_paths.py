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

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import ClassVar

import pytest
from pydantic import BaseModel

from powercontext.artifacts import Artifact
from powercontext.builtin.artifacts.experience import Experience, ExperienceContent, ExperienceDraft
from powercontext.builtin.artifacts.memory import (
    MemoryEntryInput,
    MemoryService,
    MemoryWriteAssessment,
    MemoryWriteGate,
    MemoryWriteGateRequest,
    MemoryWriteRejectionCode,
    MemoryWriteVerdict,
)
from powercontext.builtin.artifacts.memory.errors import MemoryWriteRejectedError
from powercontext.builtin.inference import InferenceUsage
from powercontext.builtin.persistence.generation_sources import GenerationSourceAccess
from powercontext.builtin.persistence.memory import RelationalMemoryBackend
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    MemoryFlushResult,
    RuntimeConfig,
    SubmitSourceObservation,
    open_builtin_contexts,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.composition import BuiltinConfigurationError
from powercontext.builtin.runtime.decision_model import (
    DecisionOutcome,
    DecisionRequest,
    DecisionResult,
    FailOpenDecisionModel,
)
from powercontext.builtin.runtime.memory_write_gate import DecisionMemoryWriteGate
from powercontext.builtin.runtime.relational import (
    RelationalContexts,
    _RelationalArtifactResolver,
    _RelationalMemorySourceResolver,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import ContentCapture
from powercontext.errors import ArtifactNotFoundError
from powercontext.server import mapping
from powercontext.server.app import _map_error
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    AdapterSourceDefinition,
    Source,
    SourceDefinitionRegistry,
    SourceMaterialization,
    TextEvidence,
    manifest_for_definition,
    project_source_for_transport,
)

_SCRIPTED_POLICY_ID = "test.memory.write-gate.v1"


class _RemoteNoteCapture(BaseModel):
    source_id: str
    content: str


class _RemoteNoteSource(Source):
    note: str


class _RemoteNoteAdapter:
    name = "remote-note"
    input_class = _RemoteNoteCapture
    source_class = _RemoteNoteSource

    async def resolve(self, value: _RemoteNoteCapture, /) -> _RemoteNoteSource:
        return _RemoteNoteSource(
            name=value.source_id, materialization=SourceMaterialization.CAPTURED, note=value.content
        )

    async def read(self, source: _RemoteNoteSource, /) -> _RemoteNoteCapture:
        return _RemoteNoteCapture(source_id=source.name, content=source.note)


class _RemoteNoteTextEvidenceProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = _RemoteNoteSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: _RemoteNoteSource, /) -> TextEvidence:
        return TextEvidence(source_type="remote-note", source_id=source.name, content=source.note)


_REMOTE_NOTE_DEFINITION = AdapterSourceDefinition(
    _RemoteNoteAdapter(),
    projections=(_RemoteNoteTextEvidenceProjection(),),
)


class _SkillFamilyExperienceBody(Artifact[ExperienceContent]):
    family: ClassVar[str] = "skill"


class _ScriptedGate:
    """A gate that returns one prepared assessment and records its requests."""

    policy_id = _SCRIPTED_POLICY_ID

    def __init__(self, assessment: MemoryWriteAssessment) -> None:
        self._assessment = assessment
        self.requests: list[MemoryWriteGateRequest] = []

    async def assess(self, request: MemoryWriteGateRequest, /) -> MemoryWriteAssessment:
        self.requests.append(request)
        return self._assessment


class _FailingGate:
    policy_id = "test.memory.write-gate.failing.v1"

    async def assess(self, request: MemoryWriteGateRequest, /) -> MemoryWriteAssessment:
        raise ValueError("gate unavailable")  # noqa: TRY003


class _FailingDecisionModel:
    policy_id = "test.decision.failing.v1"

    async def evaluate(self, request: DecisionRequest, /) -> DecisionResult:
        raise ValueError("backend unavailable")  # noqa: TRY003


class _InsufficientDecisionModel:
    """A backend that always answers "evidence is insufficient" (the hold direction)."""

    policy_id = "test.decision.insufficient.v1"

    async def evaluate(self, request: DecisionRequest, /) -> DecisionResult:
        return DecisionResult(DecisionOutcome.YES, self.policy_id, InferenceUsage(requests=1))


def _assessment(
    verdict: MemoryWriteVerdict,
    *,
    code: MemoryWriteRejectionCode | None = None,
    reason: str | None = None,
) -> MemoryWriteAssessment:
    return MemoryWriteAssessment(verdict=verdict, policy_id=_SCRIPTED_POLICY_ID, code=code, reason=reason)


def _config(tmp_path: Path, runtime: RuntimeConfig | None = None, database: str = "gate.db") -> BuiltinConfig:
    return BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / database}"),
        runtime=RuntimeConfig() if runtime is None else runtime,
    )


async def _memory_service(
    contexts: RelationalContexts,
    *,
    gate: MemoryWriteGate | None = None,
    scope_id: str = "project",
) -> MemoryService:
    """Exercise the independently supported legacy service below frozen Runtime operations."""

    context = await contexts.get(scope_id)
    return MemoryService(
        backend=RelationalMemoryBackend(
            database=contexts.database,
            scope_id=scope_id,
            artifacts=contexts.repositories.artifacts,
            index=contexts.index,
        ),
        source_resolver=_RelationalMemorySourceResolver(
            database=contexts.database,
            scope_id=scope_id,
            catalog=context.sources.catalog,
            access=GenerationSourceAccess(contexts.repositories.sources),
        ),
        artifact_resolver=_RelationalArtifactResolver(
            database=contexts.database,
            scope_id=scope_id,
            repository=contexts.repositories.artifacts,
        ),
        write_gate=gate,
    )


def test_an_accepted_write_behaves_like_the_baseline(tmp_path: Path) -> None:
    async def scenario() -> None:
        gate = _ScriptedGate(_assessment(MemoryWriteVerdict.ACCEPT))
        async with open_builtin_contexts(_config(tmp_path)) as contexts:
            service = await _memory_service(contexts, gate=gate)

            plan = await service.plan_remember(
                memory=None,
                entries=(MemoryEntryInput(kind="note", text="Accepted."),),
                mode="append",
            )

            assert plan.commit is not None
            assert plan.decision is not None
            assert plan.decision.verdict is MemoryWriteVerdict.ACCEPT
            assert [change.reason for change in plan.commit.memory.content.changes] == [None]
            assert gate.requests

    asyncio.run(scenario())


def test_a_flagged_write_is_annotated_and_still_committed(tmp_path: Path) -> None:
    async def scenario() -> None:
        gate = _ScriptedGate(_assessment(MemoryWriteVerdict.FLAG, reason="evidence is thin"))
        async with open_builtin_contexts(_config(tmp_path)) as contexts:
            service = await _memory_service(contexts, gate=gate)

            stored = await service.remember(
                memory=None,
                entries=(MemoryEntryInput(kind="note", text="Flagged."),),
                mode="append",
            )

            assert stored is not None
            assert [change.reason for change in stored.content.changes] == ["evidence is thin"]

    asyncio.run(scenario())


def test_a_flagged_write_preserves_an_existing_candidate_reason(tmp_path: Path) -> None:
    async def scenario() -> None:
        gate = _ScriptedGate(_assessment(MemoryWriteVerdict.FLAG, reason="evidence is thin"))
        async with open_builtin_contexts(_config(tmp_path)) as contexts:
            service = await _memory_service(contexts, gate=gate)

            stored = await service.remember(
                memory=None,
                entries=(MemoryEntryInput(kind="note", text="Annotated.", reason="an explicit reason"),),
                mode="append",
            )

            assert stored is not None
            assert [change.reason for change in stored.content.changes] == ["an explicit reason"]

    asyncio.run(scenario())


def test_config_rejects_the_legacy_gate_before_the_decision_backend(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = _config(tmp_path, RuntimeConfig(memory_write_gate_enabled=True), database="enabled.db")
        with pytest.raises(BuiltinConfigurationError, match=r"Atomic Memory.*legacy Memory write gate"):
            async with open_builtin_runtime(config, decision_model=_InsufficientDecisionModel()):
                pytest.fail("Legacy gate configuration must fail before Runtime startup")

    asyncio.run(scenario())


def test_config_rejects_the_legacy_gate_without_a_backend(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = _config(tmp_path, RuntimeConfig(memory_write_gate_enabled=True), database="unavailable.db")
        with pytest.raises(BuiltinConfigurationError, match=r"Atomic Memory.*legacy Memory write gate"):
            async with open_builtin_contexts(config):
                pytest.fail("Legacy gate configuration must fail before Contexts startup")

    asyncio.run(scenario())


def test_a_held_write_is_not_committed_and_stays_visible(tmp_path: Path) -> None:
    async def scenario() -> None:
        gate = _ScriptedGate(
            _assessment(
                MemoryWriteVerdict.HOLD,
                code=MemoryWriteRejectionCode.NEEDS_EVIDENCE,
                reason="the candidate cites no evidence",
            )
        )
        async with open_builtin_contexts(_config(tmp_path)) as contexts:
            service = await _memory_service(contexts, gate=gate)

            plan = await service.plan_remember(
                memory=None,
                entries=(MemoryEntryInput(kind="note", text="Held."),),
                mode="append",
            )

            assert plan.commit is None
            assert plan.decision is not None
            assert plan.decision.verdict is MemoryWriteVerdict.HOLD
            assert plan.decision.code is MemoryWriteRejectionCode.NEEDS_EVIDENCE
            assert plan.decision.reason == "the candidate cites no evidence"
            # No head is written, and the refusal is not silently dropped.
            with pytest.raises(MemoryWriteRejectedError) as error:
                await service.remember(memory=None, entries=(MemoryEntryInput(kind="note", text="Held."),))
            assert error.value.code == "needs_evidence"

    asyncio.run(scenario())


def test_a_failing_backend_leaves_the_write_unchanged(tmp_path: Path) -> None:
    async def scenario() -> None:
        gate = DecisionMemoryWriteGate(FailOpenDecisionModel(_FailingDecisionModel()), hold_on=DecisionOutcome.YES)
        async with open_builtin_contexts(_config(tmp_path)) as contexts:
            service = await _memory_service(contexts, gate=gate)

            stored = await service.remember(
                memory=None,
                entries=(MemoryEntryInput(kind="note", text="Passed through."),),
                mode="append",
            )

            assert stored is not None

    asyncio.run(scenario())


def test_a_failing_injected_gate_leaves_the_write_unchanged(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(_config(tmp_path)) as contexts:
            service = await _memory_service(contexts, gate=_FailingGate())

            stored = await service.remember(
                memory=None,
                entries=(MemoryEntryInput(kind="note", text="Passed through."),),
                mode="append",
            )

            assert stored is not None

    asyncio.run(scenario())


def test_revisions_pass_inherited_source_content_to_the_gate(tmp_path: Path) -> None:
    async def scenario() -> None:
        gate = _ScriptedGate(_assessment(MemoryWriteVerdict.ACCEPT))
        async with open_builtin_contexts(_config(tmp_path)) as contexts:
            context = await contexts.get("project")
            service = await _memory_service(contexts, gate=gate)
            source, _ = await context.sources.capture(
                ContentCapture(source_id="db-requirements", content="The database requirement is MySQL 8.")
            )
            initial = await service.remember(
                memory=None,
                sources=(source,),
                entries=(MemoryEntryInput(kind="fact", text="Use MySQL 8.", sources=(source,)),),
                mode="append",
            )
            assert initial is not None
            entry = (await service.entries(initial))[0]

            revised = await service.remember(
                memory=initial,
                entries=(MemoryEntryInput(kind="fact", text="Use PostgreSQL.", entry=entry),),
                mode="append",
            )

            assert revised is not None
            assert "The database requirement is MySQL 8." in "\n".join(gate.requests[-1].evidence)

    asyncio.run(scenario())


def test_artifact_evidence_passes_content_to_the_gate(tmp_path: Path) -> None:
    async def scenario() -> None:
        gate = _ScriptedGate(
            _assessment(
                MemoryWriteVerdict.HOLD,
                code=MemoryWriteRejectionCode.INSUFFICIENT_COVERAGE,
                reason="artifact content was inspected",
            )
        )
        async with open_builtin_contexts(_config(tmp_path)) as contexts:
            service = await _memory_service(contexts, gate=gate)
            draft = ExperienceDraft(
                content=ExperienceContent(
                    situation="The write path used SQLite.",
                    action="Checked the gate request.",
                    outcome="The controlled outcome was HOLD.",
                    lesson="Opposite outcome text must be visible to the judge.",
                ),
            )
            async with contexts.database.transaction() as connection:
                stored = await contexts.repositories.artifacts.create(
                    connection, "project", "experience-db-outcome", draft
                )
            artifact = Experience.model_validate(stored.model_dump(mode="json"))

            plan = await service.plan_remember(
                memory=None,
                artifacts=(artifact,),
                entries=(MemoryEntryInput(kind="fact", text="Gate outcome was ACCEPT.", artifacts=(artifact,)),),
                mode="append",
            )

            assert plan.commit is None
            evidence = "\n".join(gate.requests[-1].evidence)
            assert "Opposite outcome text must be visible to the judge." in evidence

    asyncio.run(scenario())


def test_gate_reads_remote_source_text_evidence_projection(tmp_path: Path) -> None:
    async def scenario() -> None:
        gate = _ScriptedGate(_assessment(MemoryWriteVerdict.ACCEPT))
        registry = SourceDefinitionRegistry((_REMOTE_NOTE_DEFINITION,))
        source = await registry.resolve(
            _RemoteNoteCapture(source_id="remote-1", content="Remote projection says use MySQL.")
        )
        observed = project_source_for_transport(registry, source)
        async with open_builtin_contexts(
            _config(tmp_path),
        ) as contexts:
            scope = await contexts.scopes.create(
                ScopeDraft(title="Remote", summary="Remote source test", idempotency_key="remote-source-gate")
            )
            await contexts.register_source_definition(manifest_for_definition(_REMOTE_NOTE_DEFINITION))
            await contexts.submit_source_observation(
                SubmitSourceObservation(scope_id=scope.scope_id, observation=observed)
            )
            service = await _memory_service(contexts, scope_id=scope.scope_id, gate=gate)

            plan = await service.plan_remember(
                memory=None,
                sources=(observed,),
                entries=(MemoryEntryInput(kind="fact", text="Use MySQL.", sources=(observed,)),),
                mode="append",
            )

            assert plan.commit is not None
            assert any("Remote projection says use MySQL." in item for item in gate.requests[-1].evidence)

    asyncio.run(scenario())


def test_gate_reads_registered_local_source_text_evidence_projection(tmp_path: Path) -> None:
    async def scenario() -> None:
        gate = _ScriptedGate(_assessment(MemoryWriteVerdict.ACCEPT))
        registry = SourceDefinitionRegistry((_REMOTE_NOTE_DEFINITION,))
        async with open_builtin_contexts(
            _config(tmp_path),
            source_registry=registry,
        ) as contexts:
            context = await contexts.get("project")
            service = await _memory_service(contexts, gate=gate)
            source = await context.sources.add(
                await context.sources.resolve(
                    _RemoteNoteCapture(source_id="local-1", content="Local projection says use MySQL.")
                )
            )

            plan = await service.plan_remember(
                memory=None,
                sources=(source,),
                entries=(MemoryEntryInput(kind="fact", text="Use MySQL.", sources=(source,)),),
                mode="append",
            )

            assert plan.commit is not None
            assert any("Local projection says use MySQL." in item for item in gate.requests[-1].evidence)

    asyncio.run(scenario())


def test_gate_budgets_only_effective_candidate_citations(tmp_path: Path) -> None:
    async def scenario() -> None:
        gate = _ScriptedGate(_assessment(MemoryWriteVerdict.ACCEPT))
        async with open_builtin_contexts(_config(tmp_path)) as contexts:
            context = await contexts.get("project")
            service = await _memory_service(contexts, gate=gate)
            short_source, _ = await context.sources.capture(ContentCapture(source_id="short", content="Use MySQL."))
            unrelated_long_source, _ = await context.sources.capture(
                ContentCapture(source_id="build-log", content="unrelated " * 300)
            )

            plan = await service.plan_remember(
                memory=None,
                sources=(short_source, unrelated_long_source),
                entries=(MemoryEntryInput(kind="fact", text="Use MySQL.", sources=(short_source,)),),
                mode="append",
            )

            assert plan.commit is not None
            evidence = "\n".join(gate.requests[-1].evidence)
            assert "Use MySQL." in evidence
            assert "build-log" not in evidence

    asyncio.run(scenario())


def test_gate_preserves_each_candidate_citation_mapping(tmp_path: Path) -> None:
    async def scenario() -> None:
        gate = _ScriptedGate(_assessment(MemoryWriteVerdict.ACCEPT))
        async with open_builtin_contexts(_config(tmp_path)) as contexts:
            context = await contexts.get("project")
            service = await _memory_service(contexts, gate=gate)
            alpha, _ = await context.sources.capture(ContentCapture(source_id="alpha", content="Alpha uses MySQL."))
            beta, _ = await context.sources.capture(ContentCapture(source_id="beta", content="Beta uses PostgreSQL."))

            await service.plan_remember(
                memory=None,
                sources=(alpha, beta),
                entries=(
                    MemoryEntryInput(kind="fact", text="Alpha uses MySQL.", sources=(alpha,)),
                    MemoryEntryInput(kind="fact", text="Beta uses PostgreSQL.", sources=(beta,)),
                ),
                mode="append",
            )

            evidence = "\n".join(gate.requests[-1].evidence)
            assert "candidate:1 source:content:alpha" in evidence
            assert "Alpha uses MySQL." in evidence
            assert "candidate:2 source:content:beta" in evidence
            assert "Beta uses PostgreSQL." in evidence

    asyncio.run(scenario())


def test_explicit_artifact_family_is_not_recovered_as_a_different_allowed_family(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(_config(tmp_path)) as contexts:
            service = await _memory_service(contexts)
            draft = ExperienceDraft(
                content=ExperienceContent(
                    situation="The artifact body is shared.",
                    action="Create an Experience revision.",
                    outcome="A matching Skill revision does not exist.",
                    lesson="Explicit families must stay authoritative.",
                ),
            )
            async with contexts.database.transaction() as connection:
                stored = await contexts.repositories.artifacts.create(connection, "project", "shared-body", draft)
            experience = Experience.model_validate(stored.model_dump(mode="json"))
            explicit_skill = _SkillFamilyExperienceBody(
                artifact_id=experience.artifact_id,
                revision=experience.revision,
                content=experience.content,
                lineage=experience.lineage,
            )
            entry = MemoryEntryInput.model_construct(
                kind="fact",
                text="The shared body is cited.",
                entry=None,
                sources=(),
                artifacts=(explicit_skill,),
                reason=None,
            )

            with pytest.raises(ArtifactNotFoundError):
                await service.plan_remember(
                    memory=None,
                    artifacts=(experience,),
                    entries=(entry,),
                    mode="append",
                )

    asyncio.run(scenario())


def test_incomplete_gate_evidence_is_held_before_backend_assessment(tmp_path: Path) -> None:
    async def scenario() -> None:
        gate = _ScriptedGate(_assessment(MemoryWriteVerdict.ACCEPT))
        async with open_builtin_contexts(_config(tmp_path)) as contexts:
            context = await contexts.get("project")
            service = await _memory_service(contexts, gate=gate)
            long_source, _ = await context.sources.capture(
                ContentCapture(source_id="corrected-requirements", content=f"{'PostgreSQL first. ' * 150}Use MySQL.")
            )

            plan = await service.plan_remember(
                memory=None,
                sources=(long_source,),
                entries=(MemoryEntryInput(kind="fact", text="Use PostgreSQL.", sources=(long_source,)),),
                mode="append",
            )

            assert plan.commit is None
            assert plan.decision is not None
            assert plan.decision.verdict is MemoryWriteVerdict.HOLD
            assert plan.decision.code is MemoryWriteRejectionCode.EVIDENCE_LIMIT_EXCEEDED
            assert gate.requests == []

    asyncio.run(scenario())


def test_without_a_gate_the_plan_carries_no_decision(tmp_path: Path) -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(_config(tmp_path)) as contexts:
            service = await _memory_service(contexts)

            plan = await service.plan_remember(
                memory=None,
                entries=(MemoryEntryInput(kind="note", text="Plain."),),
                mode="append",
            )

            assert plan.decision is None
            assert plan.commit is not None

    asyncio.run(scenario())


def test_runtime_rejects_an_injected_legacy_gate_before_an_explicit_write(tmp_path: Path) -> None:
    async def scenario() -> None:
        gate = _ScriptedGate(
            _assessment(
                MemoryWriteVerdict.HOLD,
                code=MemoryWriteRejectionCode.INSUFFICIENT_COVERAGE,
                reason="the citation is thin",
            )
        )
        with pytest.raises(BuiltinConfigurationError, match=r"Atomic Memory.*legacy Memory write gate"):
            async with open_builtin_runtime(_config(tmp_path), memory_write_gate=gate):
                pytest.fail("Injected legacy gates must fail before Runtime startup")
        assert gate.requests == []

    asyncio.run(scenario())


def test_contexts_reject_an_injected_legacy_gate_before_ingestion(tmp_path: Path) -> None:
    async def scenario() -> None:
        gate = _ScriptedGate(
            _assessment(
                MemoryWriteVerdict.HOLD,
                code=MemoryWriteRejectionCode.INSUFFICIENT_COVERAGE,
                reason="the window evidence is thin",
            )
        )
        with pytest.raises(BuiltinConfigurationError, match=r"Atomic Memory.*legacy Memory write gate"):
            async with open_builtin_contexts(_config(tmp_path), memory_write_gate=gate):
                pytest.fail("Injected legacy gates must fail before Contexts startup")
        assert gate.requests == []

    asyncio.run(scenario())


def test_flush_response_preserves_gate_hold_details() -> None:
    response = mapping.flush_response(
        MemoryFlushResult(
            previous_cursor=0,
            high_watermark=2,
            current_cursor=2,
            source_count=1,
            memory_ref=None,
            held_count=1,
            hold_codes=("insufficient_coverage",),
        )
    )

    assert response.held_count == 1
    assert response.hold_codes == ["insufficient_coverage"]


def test_memory_write_rejection_maps_to_a_structured_transport_error() -> None:
    status_code, code, message, details = _map_error(
        MemoryWriteRejectedError("insufficient_coverage", "the citation is thin")
    )

    assert status_code == 422
    assert code == "memory_write_rejected"
    assert "rejected" in message
    assert details == {"code": "insufficient_coverage", "reason": "the citation is thin"}
