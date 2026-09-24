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

"""Observable Catalog Candidate review, content CAS, ETag CAS and persistence."""

import asyncio
import os
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.engine import make_url

from powercontext.artifacts import MemoryCitation
from powercontext.builtin.artifacts.experience import ExperienceContent, ExperienceDraft
from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.catalog_changes.models import CatalogChangeProposal
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import ARTIFACTS_TABLE
from powercontext.builtin.review.errors import (
    ArtifactTargetConflictError,
    CandidateConflictError,
    InvalidCandidateError,
)
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.sources import ContentCapture
from powercontext.builtin.sources.content import ContentSourceAdapter
from powercontext.builtin.tags import ArtifactTagTarget, MemoryEntryTagTarget, TagPreconditionError


@pytest.fixture(params=("sqlite", "oceanbase"))
def database(request, tmp_path):
    if request.param == "sqlite":
        yield SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'catalog.db'}")
        return
    configured_url = os.environ.get("POWERCONTEXT_TEST_OCEANBASE_URL")
    if not configured_url:
        pytest.skip("set POWERCONTEXT_TEST_OCEANBASE_URL for catalog transaction acceptance")
    configured = OceanBaseConfig(url=SecretStr(configured_url))
    name = "pc_catalog_test_" + uuid4().hex[:16]

    async def manage(statement):
        async with (
            OceanBaseProfile.open(configured, tables=()) as admin,
            admin.database.transaction() as connection,
        ):
            await connection.exec_driver_sql(statement)

    asyncio.run(manage(f"CREATE DATABASE `{name}` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"))
    try:
        url = make_url(configured_url).set(database=name)
        yield OceanBaseConfig(url=SecretStr(url.render_as_string(hide_password=False)))
    finally:
        asyncio.run(manage(f"DROP DATABASE `{name}`"))


@asynccontextmanager
async def seed(database):
    async with open_builtin_contexts(BuiltinConfig(database=database)) as contexts:
        scope = await contexts.scopes.create(
            ScopeDraft(title="Catalog review", summary="Double CAS", idempotency_key="tag")
        )
        source = await ContentSourceAdapter().resolve(
            ContentCapture(source_id="evidence", content="Verified delivery error.")
        )
        async with contexts.database.transaction() as connection:
            stored = await contexts.repositories.sources.add(connection, scope.scope_id, source)
            artifact = await contexts.repositories.artifacts.create(
                connection,
                scope.scope_id,
                "delivery",
                ExperienceDraft(
                    content=ExperienceContent(
                        situation="Delivery", action="Check proof", outcome="Wrong address", lesson="Verify address"
                    )
                ),
            )
        yield contexts, scope.scope_id, stored.ref, artifact


async def proposal(contexts, scope, artifact, labels=("confirmed",)):
    target = ArtifactTagTarget(family="experience", artifact_id=artifact.artifact_id)
    tags = await contexts.records.get_tags(scope, target)
    return CatalogChangeProposal(
        target=target, expected_etag=tags.etag, basis_ref=artifact.as_ref(), before_tags=tags.tags, after_tags=labels
    )


def test_catalog_review_is_versioned_and_never_creates_an_artifact(database):

    async def scenario():
        async with seed(database) as (contexts, scope, source, artifact):
            service = contexts.catalog_changes(scope)
            item = await service.propose(
                await proposal(contexts, scope, artifact), sources=(source,), reason="Verified root cause"
            )
            assert (await contexts.records.get_tags(scope, item.proposal.target)).tags == ()
            revised = await service.revise(
                item.candidate_id, 1, after_tags=("delivery", "confirmed"), reason="Keep category"
            )
            with pytest.raises(CandidateConflictError):
                await service.approve(item.candidate_id, 1)
            approved = await service.approve(item.candidate_id, revised.version)
            assert approved.status == "approved" and approved.result.tags == ("confirmed", "delivery")
            assert approved.result.etag == (await contexts.records.get_tags(scope, item.proposal.target)).etag
            assert [c.version for c in await service.history(item.candidate_id)] == [1, 2]
            assert (await service.evidence(item.candidate_id)).resolved is not None
            assert (await service.list(status="approved")).candidates == (approved,)
            async with contexts.database.transaction() as connection:
                head = await contexts.repositories.artifacts.latest(
                    connection, scope, artifact.family, artifact.artifact_id
                )
                revisions = await connection.scalars(
                    select(ARTIFACTS_TABLE.c.revision).where(ARTIFACTS_TABLE.c.scope_id == scope)
                )
                assert head == artifact and tuple(revisions) == (1,)
            identifier = item.candidate_id
        async with open_builtin_contexts(BuiltinConfig(database=database)) as contexts:
            restored = await contexts.catalog_changes(scope).get(identifier)
            assert restored == approved

    asyncio.run(scenario())


def test_catalog_revision_requires_support_beyond_content_basis(database):
    async def scenario():
        async with seed(database) as (contexts, scope, source, artifact):
            service = contexts.catalog_changes(scope)
            candidate = await service.propose(
                await proposal(contexts, scope, artifact), sources=(source,), reason="Verified correction"
            )
            with pytest.raises(InvalidCandidateError, match="beyond the content basis"):
                await service.revise(
                    candidate.candidate_id,
                    candidate.version,
                    after_tags=("unsupported",),
                    reason="Remove all supporting evidence",
                    sources=(),
                    artifacts=(artifact.as_ref(),),
                    memory_citations=(),
                )

    asyncio.run(scenario())


@pytest.mark.parametrize("conflict", ["tags", "body", "evidence", "authority"])
def test_catalog_approval_rechecks_both_bases_and_live_evidence(database, conflict):
    async def scenario():
        async with seed(database) as (
            contexts,
            scope,
            source,
            artifact,
        ):
            service = contexts.catalog_changes(scope)
            item = await service.propose(
                await proposal(contexts, scope, artifact), sources=(source,), reason="Verified correction"
            )
            error = TagPreconditionError
            if conflict == "tags":
                await contexts.records.replace_tags(
                    scope, item.proposal.target, ("manual",), expected_etag=item.proposal.expected_etag
                )
            elif conflict == "body":
                async with contexts.database.transaction() as connection:
                    await contexts.repositories.artifacts.revise(
                        connection,
                        scope,
                        artifact,
                        ExperienceDraft(
                            content=artifact.content.model_copy(update={"lesson": "A changed lesson"}),
                            sources=(source,),
                        ),
                    )
                error = ArtifactTargetConflictError
            elif conflict == "authority":

                async def deny(reference):
                    raise PermissionError

                service._evidence.authorize = deny
                error = PermissionError
            else:
                from sqlalchemy import delete

                from powercontext.builtin.persistence.tables import SOURCES_TABLE

                async with contexts.database.transaction() as connection:
                    await connection.execute(delete(SOURCES_TABLE).where(SOURCES_TABLE.c.scope_id == scope))
                from powercontext.builtin.evidence.models import EvidenceResolutionError

                error = EvidenceResolutionError
            with pytest.raises(error):
                await service.approve(item.candidate_id, 1)
            service._evidence.authorize = None
            assert (await service.get(item.candidate_id)).status == "pending"
            assert (await contexts.records.get_tags(scope, item.proposal.target)).tags != ("confirmed",)
            rejected = await service.reject(item.candidate_id, 1, "Discard superseded candidate")
            assert rejected.status == "rejected"

    asyncio.run(scenario())


def test_catalog_memory_entry_basis_ignores_unrelated_head_changes(database):
    async def scenario():
        async with seed(database) as (
            contexts,
            scope,
            source,
            _artifact,
        ):
            context = await contexts.get(scope)
            memory_service = context.artifacts.memory
            memory = await memory_service.remember(
                memory=None, entries=(MemoryEntryInput(kind="fact", text="Delivery was incorrect"),), mode="append"
            )
            entry = memory.content.manifest.entries[0]
            target = MemoryEntryTagTarget(artifact_id=memory.artifact_id, entry_id=entry.entry_id)
            tags = await contexts.records.get_tags(scope, target)
            candidate = await contexts.catalog_changes(scope).propose(
                CatalogChangeProposal(
                    target=target,
                    expected_etag=tags.etag,
                    basis_citation=MemoryCitation(
                        memory_ref=memory.as_ref(), entry_id=entry.entry_id, entry_version_id=entry.entry_version_id
                    ),
                    before_tags=(),
                    after_tags=("verified",),
                ),
                sources=(source,),
                reason="Verified result",
            )
            await memory_service.remember(
                memory=memory, entries=(MemoryEntryInput(kind="fact", text="Unrelated entry"),), mode="append"
            )
            approved = await contexts.catalog_changes(scope).approve(candidate.candidate_id, 1)
            assert approved.result.tags == ("verified",)

    asyncio.run(scenario())


def test_catalog_decision_failure_rolls_back_tags_and_can_be_retried(tmp_path):
    async def scenario():
        async with seed(SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'atomic.db'}")) as (
            contexts,
            scope,
            source,
            artifact,
        ):
            service = contexts.catalog_changes(scope)
            item = await service.propose(
                await proposal(contexts, scope, artifact), sources=(source,), reason="Verified correction"
            )
            async with contexts.database.transaction() as connection:
                await connection.exec_driver_sql("""CREATE TRIGGER reject_catalog_decision
                    BEFORE UPDATE ON pc_candidate_heads
                    WHEN NEW.status = 'approved'
                    BEGIN SELECT RAISE(ABORT, 'injected storage failure'); END""")
            from sqlalchemy.exc import IntegrityError

            with pytest.raises(IntegrityError):
                await service.approve(item.candidate_id, 1)
            assert (await contexts.records.get_tags(scope, item.proposal.target)).tags == ()
            assert (await service.get(item.candidate_id)).status == "pending"
            async with contexts.database.transaction() as connection:
                await connection.exec_driver_sql("DROP TRIGGER reject_catalog_decision")
            assert (await service.approve(item.candidate_id, 1)).status == "approved"

    asyncio.run(scenario())


def test_catalog_concurrent_decisions_publish_only_once(database):
    async def scenario():
        async with seed(database) as (
            contexts,
            scope,
            source,
            artifact,
        ):
            service = contexts.catalog_changes(scope)
            item = await service.propose(
                await proposal(contexts, scope, artifact), sources=(source,), reason="Verified correction"
            )
            results = await asyncio.gather(
                service.approve(item.candidate_id, 1), service.approve(item.candidate_id, 1), return_exceptions=True
            )
            from powercontext.builtin.catalog_changes.models import CatalogChangeCandidate

            assert all(isinstance(result, CatalogChangeCandidate) for result in results)
            assert results[0] == results[1]
            assert (await contexts.records.get_tags(scope, item.proposal.target)).tags == ("confirmed",)

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["revise", "retire"])
def test_catalog_memory_entry_changes_invalidate_pending_candidate(database, change):
    async def scenario():
        async with seed(database) as (
            contexts,
            scope,
            source,
            _artifact,
        ):
            memory_service = (await contexts.get(scope)).artifacts.memory
            memory = await memory_service.remember(
                memory=None, entries=(MemoryEntryInput(kind="fact", text="Delivery was incorrect"),), mode="append"
            )
            entry = memory.content.manifest.entries[0]
            citation = MemoryCitation(
                memory_ref=memory.as_ref(), entry_id=entry.entry_id, entry_version_id=entry.entry_version_id
            )
            target = MemoryEntryTagTarget(artifact_id=memory.artifact_id, entry_id=entry.entry_id)
            tags = await contexts.records.get_tags(scope, target)
            candidate = await contexts.catalog_changes(scope).propose(
                CatalogChangeProposal(
                    target=target,
                    expected_etag=tags.etag,
                    basis_citation=citation,
                    before_tags=(),
                    after_tags=("verified",),
                ),
                sources=(source,),
                reason="Verified result",
            )
            version = await memory_service.validate_citation(citation)
            if change == "revise":
                await memory_service.remember(
                    memory=memory,
                    entries=(MemoryEntryInput(entry=version, kind="fact", text="Delivery was correct"),),
                    mode="append",
                )
            else:
                await memory_service.forget(memory, entries=(version,))
            from powercontext.builtin.evidence.models import EvidenceResolutionError

            with pytest.raises((ArtifactTargetConflictError, EvidenceResolutionError)):
                await contexts.catalog_changes(scope).approve(candidate.candidate_id, 1)
            assert (await contexts.catalog_changes(scope).get(candidate.candidate_id)).status == "pending"
            assert (await contexts.records.get_tags(scope, target)).tags == ()

    asyncio.run(scenario())


def test_legacy_database_adds_catalog_review_without_losing_existing_tags(tmp_path):
    database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'legacy.db'}")

    async def scenario():
        async with seed(database) as (contexts, scope, source, artifact):
            initial = await proposal(contexts, scope, artifact)
            await contexts.records.replace_tags(
                scope, initial.target, ("existing",), expected_etag=initial.expected_etag
            )
            async with contexts.database.transaction() as connection:
                await connection.exec_driver_sql("ALTER TABLE pc_candidate_heads RENAME TO pc_artifact_candidate_heads")
                await connection.exec_driver_sql(
                    "ALTER TABLE pc_candidate_versions RENAME TO pc_artifact_candidate_versions"
                )
                await connection.exec_driver_sql("DROP INDEX ix_pc_dream_runs_proposal_fingerprint")
                await connection.exec_driver_sql("ALTER TABLE pc_dream_runs DROP COLUMN proposal_fingerprint")
        for _ in range(2):
            async with open_builtin_contexts(BuiltinConfig(database=database)) as contexts:
                tags = await contexts.records.get_tags(scope, initial.target)
                assert tags.tags == ("existing",)
                candidate = await contexts.catalog_changes(scope).propose(
                    await proposal(contexts, scope, artifact), sources=(source,), reason="Migration preserves targets"
                )
                assert (await contexts.catalog_changes(scope).get(candidate.candidate_id)).status == "pending"

    asyncio.run(scenario())


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_tag_dream_uses_exact_body_and_reuses_pending_candidates(database, decision):
    from powercontext.artifacts import ArtifactRef
    from powercontext.builtin.catalog_changes.models import TagChangeProposal, TagDreamTarget
    from powercontext.builtin.dream.generation import DreamGenerationInput
    from powercontext.builtin.dream.models import CreateDreamRunRequest, DreamPlan, GetDreamRunRequest
    from powercontext.builtin.inference.models import GenerationResult, InferenceUsage
    from powercontext.builtin.records import ArtifactWrite
    from powercontext.builtin.runtime import CaptureSource
    from tests.e2e.dream_support import open_dream_runtime, process_pending

    class Generator:
        config_id = "catalog-dream-test"

        async def generate(self, value: DreamGenerationInput):
            assert value.operation == "revise_tags"
            assert value.before_tags == ("old",)
            assert value.tag_target is not None and value.tag_target.basis_ref is not None
            assert value.tag_target.basis_ref.revision == 1
            assert any("EXACT_BODY_FOR_TAGGING" in item.text for item in value.evidence.evidence)
            return GenerationResult(
                output=DreamPlan(
                    outcome="proposed",
                    reason="Verified delivery error replaces old classification",
                    intent="correct",
                    proposal=TagChangeProposal(after_tags=("delivery-error",)),
                    evidence_ids=tuple(item.evidence_id for item in value.evidence.evidence if item.kind == "source"),
                ),
                usage=InferenceUsage(requests=1),
            )

    async def scenario():
        async with open_dream_runtime(BuiltinConfig(database=database), dream_generator=Generator()) as runtime:
            scope = (
                await runtime.scopes.create(
                    ScopeDraft(title="Tag Dream", summary="Review first", idempotency_key="dream")
                )
            ).scope_id
            created = await runtime.records.for_scope(scope).create_artifact(
                "experience",
                ArtifactWrite(
                    content={
                        "situation": "EXACT_BODY_FOR_TAGGING",
                        "action": "Verify",
                        "outcome": "Wrong delivery",
                        "lesson": "Classify verified cause",
                    }
                ),
            )
            ref = ArtifactRef(family="experience", artifact_id=created.artifact_id, revision=created.revision)
            target = ArtifactTagTarget(family="experience", artifact_id=ref.artifact_id)
            original = await runtime.records.for_scope(scope).get_tags(target)
            tags = await runtime.records.for_scope(scope).replace_tags(target, ("old",), expected_etag=original.etag)
            source = await runtime.sources.for_scope(scope).capture(
                CaptureSource(source_id="result", content="Verified delivery error", metadata={})
            )
            request = CreateDreamRunRequest(
                operation="revise_tags",
                tag_target=TagDreamTarget(target=target, expected_etag=tags.etag, basis_ref=ref),
                sources=(source.source_ref,),
                idempotency_key="first",
            )
            first = await runtime.dream.for_scope(scope).create(request)
            await process_pending(runtime)
            first = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=first.run_id))
            assert first.outcome == "proposed", first
            assert first.candidate.kind == "tag"
            assert (await runtime.records.for_scope(scope).get_tags(target)).tags == ("old",)
            second = await runtime.dream.for_scope(scope).create(
                request.model_copy(update={"idempotency_key": "second"})
            )
            await process_pending(runtime)
            second = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=second.run_id))
            assert second.candidate == first.candidate and second.reused
            candidate = await runtime.catalog_changes.for_scope(scope).get(first.candidate.candidate_id)
            assert candidate.audit.operation == "revise_tags"
            if decision == "approve":
                candidate = await runtime.catalog_changes.for_scope(scope).revise(
                    candidate.candidate_id, 1, after_tags=("reviewed",), reason="Reviewer confirms narrower labels"
                )
                approved = await runtime.catalog_changes.for_scope(scope).approve(
                    candidate.candidate_id, candidate.version
                )
                assert approved.result.tags == ("reviewed",)
                assert (
                    await runtime.records.for_scope(scope).get_artifact("experience", ref.artifact_id)
                ).revision == 1
            else:
                await runtime.catalog_changes.for_scope(scope).reject(
                    candidate.candidate_id, 1, "Keep the original classification"
                )
                third = await runtime.dream.for_scope(scope).create(
                    request.model_copy(update={"idempotency_key": "third"})
                )
                await process_pending(runtime)
                third = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=third.run_id))
                assert third.outcome == "no_change" and third.candidate == first.candidate
                assert (await runtime.records.for_scope(scope).get_tags(target)).tags == ("old",)

    asyncio.run(scenario())


@pytest.mark.parametrize("decision", ["pending", "rejected"])
def test_tag_dream_new_independent_evidence_does_not_reuse_old_candidate(database, decision):
    """A rootless support Artifact changes Dream identity alongside a rooted Source."""

    from powercontext.artifacts import ArtifactRef
    from powercontext.builtin.artifacts.skill import SkillContent, SkillDraft
    from powercontext.builtin.catalog_changes.models import TagChangeProposal, TagDreamTarget
    from powercontext.builtin.dream.generation import DreamGenerationInput
    from powercontext.builtin.dream.models import CreateDreamRunRequest, DreamPlan, GetDreamRunRequest
    from powercontext.builtin.inference.models import GenerationResult, InferenceUsage
    from powercontext.builtin.records import ArtifactWrite
    from powercontext.builtin.runtime import CaptureSource
    from tests.e2e.dream_support import open_dream_runtime, process_pending

    class Generator:
        config_id = "catalog-dream-independent-evidence-test"

        def __init__(self) -> None:
            self.inputs: list[DreamGenerationInput] = []

        async def generate(self, value: DreamGenerationInput):
            self.inputs.append(value)
            assert value.operation == "revise_tags"
            return GenerationResult(
                output=DreamPlan(
                    outcome="proposed",
                    reason="The supplied evidence supports the classification.",
                    intent="correct",
                    proposal=TagChangeProposal(after_tags=("reviewed",)),
                    evidence_ids=tuple(
                        item.evidence_id for item in value.evidence.evidence if item.kind in {"source", "skill"}
                    ),
                ),
                usage=InferenceUsage(requests=1),
            )

    async def scenario():
        generator = Generator()
        async with open_dream_runtime(BuiltinConfig(database=database), dream_generator=generator) as runtime:
            scope = (
                await runtime.scopes.create(
                    ScopeDraft(title="Tag Dream evidence", summary="Independent support", idempotency_key="dream")
                )
            ).scope_id
            created = await runtime.records.for_scope(scope).create_artifact(
                "experience",
                ArtifactWrite(
                    content={
                        "situation": "Tagging target",
                        "action": "Verify",
                        "outcome": "Evidence supports the label",
                        "lesson": "Use all independent evidence",
                    }
                ),
            )
            target = ArtifactTagTarget(family="experience", artifact_id=created.artifact_id)
            original = await runtime.records.for_scope(scope).get_tags(target)
            tagged = await runtime.records.for_scope(scope).replace_tags(target, ("old",), expected_etag=original.etag)
            source = await runtime.sources.for_scope(scope).capture(
                CaptureSource(source_id="root", content="Verified tagging evidence", metadata={})
            )
            async with runtime._provider.database.transaction() as connection:
                independent = await runtime._provider.repositories.artifacts.create(
                    connection,
                    scope,
                    "independent-skill",
                    SkillDraft(
                        content=SkillContent(
                            name="independent-support",
                            description="Independent support evidence",
                            instructions="Use the verified support.",
                            validation=("Check the supporting evidence.",),
                        )
                    ),
                )
            basis = ArtifactRef(family="experience", artifact_id=created.artifact_id, revision=created.revision)
            request = CreateDreamRunRequest(
                operation="revise_tags",
                tag_target=TagDreamTarget(
                    target=target,
                    expected_etag=tagged.etag,
                    basis_ref=basis,
                ),
                sources=(source.source_ref,),
                idempotency_key="root-only",
            )
            first = await runtime.dream.for_scope(scope).create(request)
            await process_pending(runtime)
            first = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=first.run_id))
            assert first.outcome == "proposed" and first.candidate is not None
            if decision == "rejected":
                await runtime.catalog_changes.for_scope(scope).reject(
                    first.candidate.candidate_id, first.candidate.version, "Keep the original labels"
                )
            second = await runtime.dream.for_scope(scope).create(
                request.model_copy(update={"artifacts": (independent.as_ref(),), "idempotency_key": "with-skill"})
            )
            await process_pending(runtime)
            second = await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=second.run_id))
            assert second.outcome == "proposed" and second.candidate is not None
            assert not second.reused and second.candidate != first.candidate
            assert len(generator.inputs) == 2
            assert any(item.kind == "skill" for item in generator.inputs[1].evidence.evidence)

    asyncio.run(scenario())


def test_tag_dreams_for_different_families_share_their_existing_workers(database):
    from powercontext.artifacts import ArtifactRef
    from powercontext.builtin.catalog_changes.models import TagChangeProposal, TagDreamTarget
    from powercontext.builtin.dream.models import CreateDreamRunRequest, DreamPlan, GetDreamRunRequest
    from powercontext.builtin.inference.models import GenerationResult, InferenceUsage
    from powercontext.builtin.records import ArtifactWrite
    from powercontext.builtin.runtime import CaptureSource
    from tests.e2e.dream_support import open_dream_runtime, process_pending

    class Generator:
        config_id = "catalog-routing-test"

        async def generate(self, value):
            family = value.tag_target.target.family
            assert value.before_tags == ()
            marker = "powercontext.prompt.v1" if family == "prompt" else f"TAG_BODY_{family}"
            assert any(marker in item.text for item in value.evidence.evidence)
            return GenerationResult(
                output=DreamPlan(
                    outcome="proposed",
                    reason="Classify the verified target",
                    intent="correct",
                    proposal=TagChangeProposal(after_tags=(family,)),
                    evidence_ids=tuple(item.evidence_id for item in value.evidence.evidence if item.kind == "source"),
                ),
                usage=InferenceUsage(requests=1),
            )

    async def scenario():
        async with open_dream_runtime(BuiltinConfig(database=database), dream_generator=Generator()) as runtime:
            scope = (
                await runtime.scopes.create(
                    ScopeDraft(title="Catalog routing", summary="One worker per family", idempotency_key="routing")
                )
            ).scope_id
            source = await runtime.sources.for_scope(scope).capture(
                CaptureSource(source_id="result", content="Verified routing feedback", metadata={})
            )
            contents = {
                "experience": ArtifactWrite(
                    content={
                        "situation": "TAG_BODY_experience",
                        "action": "Verify",
                        "outcome": "Confirmed",
                        "lesson": "Use verified classification",
                    }
                ),
                "profile": ArtifactWrite(content={"content": "# TAG_BODY_profile"}),
                "prompt": ArtifactWrite(
                    prompt_key="memory.extract",
                    content={
                        "schema_version": "powercontext.prompt.v1",
                        "mode": "auto",
                        "instructions": "",
                        "demonstrations": [],
                    },
                ),
                "topic-memory": ArtifactWrite(
                    content={
                        "title": "TAG_BODY_topic-memory",
                        "summary": "Verified",
                        "detail": "Classification evidence",
                    }
                ),
                "skill": ArtifactWrite(
                    content={
                        "name": "catalog-skill",
                        "description": "TAG_BODY_skill",
                        "instructions": "Verify categorization",
                        "validation": ["Review evidence"],
                    }
                ),
            }
            runs = []
            for family, content in contents.items():
                created = await runtime.records.for_scope(scope).create_artifact(family, content)
                ref = ArtifactRef(family=family, artifact_id=created.artifact_id, revision=created.revision)
                target = ArtifactTagTarget.model_validate({"family": family, "artifact_id": ref.artifact_id})
                tags = await runtime.records.for_scope(scope).get_tags(target)
                runs.append(
                    await runtime.dream.for_scope(scope).create(
                        CreateDreamRunRequest(
                            operation="revise_tags",
                            tag_target=TagDreamTarget(target=target, expected_etag=tags.etag, basis_ref=ref),
                            sources=(source.source_ref,),
                            idempotency_key=family,
                        )
                    )
                )
            for _ in range(10):
                await process_pending(runtime)
                completed = [
                    await runtime.dream.for_scope(scope).get(GetDreamRunRequest(run_id=run.run_id)) for run in runs
                ]
                if all(run.terminal for run in completed):
                    break
            assert all(run.outcome == "proposed" for run in completed), [
                (run.tag_target.target.family, run.status, run.error, run.outcome) for run in completed
            ]
            for run in completed:
                candidate = await runtime.catalog_changes.for_scope(scope).get(run.candidate.candidate_id)
                approved = await runtime.catalog_changes.for_scope(scope).approve(candidate.candidate_id, 1)
                assert approved.result.tags == (run.tag_target.target.family,)
                assert (
                    await runtime.records.for_scope(scope).get_artifact(
                        run.tag_target.target.family, run.tag_target.target.artifact_id
                    )
                ).revision == run.tag_target.basis_ref.revision

    asyncio.run(scenario())


def test_catalog_correction_can_replace_labels_based_on_retired_target_history(database):
    async def scenario():
        async with seed(database) as (contexts, scope, source, _artifact):
            memory_service = (await contexts.get(scope)).artifacts.memory
            memory = await memory_service.remember(
                memory=None, entries=(MemoryEntryInput(kind="fact", text="Old guess"),), mode="append"
            )
            entry = memory.content.manifest.entries[0]
            citation = MemoryCitation(
                memory_ref=memory.as_ref(), entry_id=entry.entry_id, entry_version_id=entry.entry_version_id
            )
            version = await memory_service.validate_citation(citation)
            async with contexts.database.transaction() as connection:
                artifact = await contexts.repositories.artifacts.create(
                    connection,
                    scope,
                    "old-label-target",
                    ExperienceDraft(
                        content=ExperienceContent(
                            situation="Old guess", action="Verify", outcome="Unclear", lesson="Needs correction"
                        ),
                        memory_citations=(citation,),
                    ),
                )
            await memory_service.forget(memory, entries=(version,))
            candidate = await contexts.catalog_changes(scope).propose(
                await proposal(contexts, scope, artifact),
                sources=(source,),
                artifacts=(artifact.as_ref(),),
                reason="New verified source corrects old classification",
            )
            approved = await contexts.catalog_changes(scope).approve(candidate.candidate_id, 1)
            assert approved.result.tags == ("confirmed",)

    asyncio.run(scenario())


def test_catalog_rows_do_not_block_database_scope_cleanup(tmp_path):
    from sqlalchemy import delete

    from powercontext.builtin.persistence.tables import SCOPE_CREATION_REQUESTS_TABLE, SCOPES_TABLE
    from powercontext.builtin.review.errors import CandidateNotFoundError

    async def scenario():
        async with seed(SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'scope.db'}")) as (
            contexts,
            scope,
            source,
            artifact,
        ):
            candidate = await contexts.catalog_changes(scope).propose(
                await proposal(contexts, scope, artifact), sources=(source,), reason="Classify"
            )
            # There is no public Scope deletion endpoint. Exercise the database
            # cleanup boundary after removing the existing idempotency reference.
            async with contexts.database.transaction() as connection:
                await connection.execute(
                    delete(SCOPE_CREATION_REQUESTS_TABLE).where(SCOPE_CREATION_REQUESTS_TABLE.c.scope_id == scope)
                )
                await connection.execute(delete(SCOPES_TABLE).where(SCOPES_TABLE.c.scope_id == scope))
            with pytest.raises(CandidateNotFoundError):
                await contexts.catalog_changes(scope).get(candidate.candidate_id)

    asyncio.run(scenario())
