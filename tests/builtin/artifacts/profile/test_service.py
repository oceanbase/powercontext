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


import asyncio

import pytest
from sqlalchemy import func, select

from powercontext.builtin.artifacts.profile.models import ProfileContent, ProfileWriteContent
from powercontext.builtin.artifacts.prompt import PromptRegistry
from powercontext.builtin.artifacts.prompt.builtin import builtin_prompt_definitions
from powercontext.builtin.artifacts.prompt.service import current_prompt
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES, SCOPES_TABLE, SOURCES_TABLE
from powercontext.builtin.records import ArtifactWrite, BaseValueConflictError, InvalidBaseAccessRequestError
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.builtin.scope import ScopeBindingKey, ScopeDraft
from powercontext.builtin.scope.errors import ScopeBindingNotFoundError


class Generator:
    def __init__(self, text="# Profile\n\n- Prefers Chinese."):
        self.text = text
        self.inputs = []

    async def generate(self, value):
        self.inputs.append(value)
        return self.text


async def scope(contexts, name):
    return (await contexts.scopes.create(ScopeDraft(title=name, summary=name, idempotency_key=name))).scope_id


def test_subject_binding_dual_write_and_management():
    async def run():
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as db:
            ctx = RelationalContexts(database=db.database)
            group = await scope(ctx, "Group")
            target, pair = await ctx.subject_sources.create(group, "U1", {"speaker": "U1", "text": "Chinese"})
            assert target != group
            assert pair[0].source_id == pair[1].source_id
            assert pair[0].content == pair[1].content
            assert (await ctx.scopes.get(target)).parent_scope_id is None
            assert (await ctx.profiles.get_policy(target)).generation_enabled
            again, _ = await ctx.subject_sources.create(group, "U1", "Second")
            assert again == target
            other = await scope(ctx, "Other")
            with pytest.raises(BaseValueConflictError):
                await ctx.subject_sources.create(group, "U1", "Conflict", subject_scope_id=other)
            with pytest.raises(InvalidBaseAccessRequestError):
                await ctx.subject_sources.create(target, "U1", "Same")
            key = ScopeBindingKey(integration="subject", kind="user", external_id="U1")
            await ctx.scopes.bind(key, other)
            rebound, _ = await ctx.subject_sources.create(group, "U1", "Third")
            assert rebound == other
            await ctx.scopes.clear_binding(key)
            await ctx.scopes.set_default(group)
            with pytest.raises(ScopeBindingNotFoundError):
                await ctx.scopes.resolve_binding(binding_keys=(key,), allow_default=False)
            assert (await ctx.scopes.resolve_binding(binding_keys=(key,))).scope_id == group
            async with db.database.transaction() as connection:
                assert await connection.scalar(select(func.count()).select_from(SOURCES_TABLE)) == 6

    asyncio.run(run())


def test_subject_failure_rolls_back_scope_binding_and_both_sources():
    async def run():
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as db:
            ctx = RelationalContexts(database=db.database)
            group = await scope(ctx, "Group")

            async def denied(*args):
                raise PermissionError

            with pytest.raises(PermissionError):
                await ctx.subject_sources.create(group, "U1", "Private", authorize=denied)
            async with db.database.transaction() as connection:
                assert await connection.scalar(select(func.count()).select_from(SCOPES_TABLE)) == 1
                assert await connection.scalar(select(func.count()).select_from(SOURCES_TABLE)) == 0

    asyncio.run(run())


def test_profile_generic_crud_generation_and_no_change():
    async def run():
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as db:
            ctx = RelationalContexts(database=db.database)
            sid = await scope(ctx, "User")
            assert (await ctx.profiles.flush(sid)).status == "disabled"
            created = await ctx.records.create_artifact(sid, "profile", ArtifactWrite(content={"content": "# Manual"}))
            assert created.artifact_id == "profile"
            assert not (await ctx.profiles.get_policy(sid)).generation_enabled
            policy = await ctx.profiles.get_policy(sid)
            await ctx.profiles.put_policy(sid, generation_enabled=True, expected_version=policy.version)
            generator = Generator()
            ctx.profiles.generator = generator
            assert (await ctx.profiles.flush(sid)).status == "noop"
            assert not generator.inputs  # lineage-only Source consumed without generation
            await ctx.records.create_source(sid, "content", "Prefers Chinese")
            result = await ctx.profiles.flush(sid)
            assert result.status == "updated"
            assert result.artifact is not None
            assert result.artifact.revision == 2
            saved = await ctx.records.get_artifact(sid, "profile", "profile")
            source_window = ProfileContent.model_validate(saved.content).generation.source_window
            assert source_window is not None and source_window.through == result.current_cursor
            await ctx.records.create_source(sid, "content", "Prefers Chinese")
            assert (await ctx.profiles.flush(sid)).status == "noop"
            assert (await ctx.records.get_artifact(sid, "profile", "profile")).revision == 2
            count = len(generator.inputs)
            assert (await ctx.profiles.flush(sid)).status == "noop"
            assert len(generator.inputs) == count

    asyncio.run(run())


def test_profile_generation_binds_custom_prompt_and_records_lineage():
    class PromptAwareGenerator(Generator):
        def __init__(self):
            super().__init__()
            self.selection = None

        async def generate(self, value):
            self.selection = current_prompt("profile.generate")
            return await super().generate(value)

    async def run():
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as db:
            registry = PromptRegistry(
                builtin_prompt_definitions(),
                supported=frozenset({"profile.generate"}),
            )
            ctx = RelationalContexts(database=db.database, prompt_registry=registry)
            sid = await scope(ctx, "Prompt Profile")
            prompt = await ctx.records.create_artifact(
                sid,
                "prompt",
                ArtifactWrite(
                    prompt_key="profile.generate",
                    content={
                        "schema_version": "powercontext.prompt.v1",
                        "mode": "custom",
                        "instructions": "Keep verified long-term communication preferences.",
                        "demonstrations": [],
                    },
                ),
            )
            await ctx.profiles.put_policy(sid, generation_enabled=True, expected_version=0)
            await ctx.records.create_source(sid, "content", "Prefers Chinese")
            generator = PromptAwareGenerator()
            ctx.profiles.generator = generator

            result = await ctx.profiles.flush(sid)

            assert result.status == "updated"
            assert generator.selection is not None
            assert generator.selection.artifact is not None
            assert generator.selection.artifact.family == "prompt"
            assert generator.selection.artifact.artifact_id == prompt.artifact_id
            assert generator.selection.artifact.revision == prompt.revision
            saved = await ctx.records.get_artifact(sid, "profile", "profile")
            assert any(
                ref.family == "prompt" and ref.artifact_id == prompt.artifact_id and ref.revision == prompt.revision
                for ref in saved.artifacts
            )

    asyncio.run(run())


@pytest.mark.parametrize("has_existing_profile", [False, True])
def test_review_generation_reserves_candidate_evidence_capacity_for_custom_prompt(has_existing_profile):
    async def run():
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as db:
            registry = PromptRegistry(
                builtin_prompt_definitions(),
                supported=frozenset({"profile.generate"}),
            )
            ctx = RelationalContexts(database=db.database, prompt_registry=registry)
            sid = await scope(ctx, f"Prompt Capacity {has_existing_profile}")
            if has_existing_profile:
                await ctx.records.create_artifact(
                    sid,
                    "profile",
                    ArtifactWrite(content={"content": "# Existing Profile"}),
                )
                policy = await ctx.profiles.get_policy(sid)
                await ctx.profiles.put_policy(
                    sid,
                    generation_enabled=True,
                    activation_mode="review_required",
                    expected_version=policy.version,
                )
                assert (await ctx.profiles.flush(sid)).status == "noop"
            else:
                await ctx.profiles.put_policy(
                    sid,
                    generation_enabled=True,
                    activation_mode="review_required",
                    expected_version=0,
                )
            expected_sources = 30 if has_existing_profile else 31
            for index in range(expected_sources + 1):
                await ctx.records.create_source(sid, "content", f"Lasting fact {index}")
            prompt = await ctx.records.create_artifact(
                sid,
                "prompt",
                ArtifactWrite(
                    prompt_key="profile.generate",
                    content={
                        "schema_version": "powercontext.prompt.v1",
                        "mode": "custom",
                        "instructions": "Keep verified lasting facts.",
                        "demonstrations": [],
                    },
                ),
            )
            ctx.profiles.generator = Generator("# Generated Profile")

            result = await ctx.profiles.flush(sid)

            assert result.status == "review_pending"
            assert result.candidate_id is not None
            candidate = await ctx.review(sid).get_candidate(result.candidate_id)
            assert len(candidate.sources) == expected_sources
            assert len(candidate.sources) + len(candidate.artifacts) == 32
            assert any(
                ref.family == "prompt" and ref.artifact_id == prompt.artifact_id and ref.revision == prompt.revision
                for ref in candidate.artifacts
            )

    asyncio.run(run())


def test_review_revise_approve_reject_and_resume():
    async def run():
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as db:
            ctx = RelationalContexts(database=db.database)
            sid = await scope(ctx, "User")
            await ctx.profiles.put_policy(
                sid, generation_enabled=True, activation_mode="review_required", expected_version=0
            )
            ctx.profiles.generator = Generator()
            await ctx.records.create_source(sid, "content", "Prefers Chinese")
            first = await ctx.profiles.flush(sid)
            assert first.status == "review_pending" and first.current_cursor == 0
            review = ctx.review(sid)
            assert first.candidate_id is not None
            pending = await review.get_candidate(first.candidate_id)
            assert (await ctx.profiles.flush(sid)).candidate_id == pending.candidate_id
            revised = await review.revise(
                pending.candidate_id,
                pending.version,
                ProfileWriteContent(content="# Reviewed"),
                sources=pending.sources,
                artifacts=pending.artifacts,
                target=pending.target,
                reason=pending.reason,
            )
            approved = await review.approve(revised.candidate_id, revised.version)
            assert approved.result_artifact is not None
            assert approved.result_artifact.artifact_id == "profile"
            assert (await ctx.profiles.get_policy(sid)).pending_candidate_id is None
            assert (await ctx.profiles.flush(sid)).status == "noop"
            await ctx.records.create_source(sid, "content", "New evidence")
            second = await ctx.profiles.flush(sid)
            assert second.candidate_id is not None
            rejected = await review.reject(second.candidate_id, 1, "Temporary request")
            assert rejected.status.value == "rejected"
            calls = len(ctx.profiles.generator.inputs)
            assert (await ctx.profiles.flush(sid)).status == "noop"
            assert len(ctx.profiles.generator.inputs) == calls
            # Restarting the service reconstructs all state from shared tables.
            reopened = RelationalContexts(database=db.database)
            assert (await reopened.profiles.flush(sid)).status == "noop"

    asyncio.run(run())


def test_pending_review_survives_manual_replace_and_rejects_stale_approval():
    async def run():
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as db:
            ctx = RelationalContexts(database=db.database)
            sid = await scope(ctx, "Review")
            await ctx.profiles.put_policy(sid, generation_enabled=True, expected_version=0)
            generator = Generator()
            ctx.profiles.generator = generator
            await ctx.records.create_source(sid, "content", "First evidence")
            await ctx.profiles.flush(sid)
            policy = await ctx.profiles.get_policy(sid)
            await ctx.profiles.put_policy(
                sid, generation_enabled=True, activation_mode="review_required", expected_version=policy.version
            )
            generator.text = "# New"
            await ctx.records.create_source(sid, "content", "Second evidence")
            pending = await ctx.profiles.flush(sid)
            assert pending.candidate_id is not None
            record = await ctx.records.get_artifact(sid, "profile", "profile")
            await ctx.records.replace_artifact(
                sid,
                "profile",
                "profile",
                f'"revision:{record.revision}"',
                ArtifactWrite(content={"content": "# Human"}),
            )
            with pytest.raises(BaseValueConflictError):
                await ctx.review(sid).approve(pending.candidate_id, 1)
            assert (await ctx.profiles.get_policy(sid)).pending_candidate_id == pending.candidate_id
            await ctx.review(sid).reject(pending.candidate_id, 1, "Superseded by manual edit")
            assert (await ctx.profiles.flush(sid)).status == "noop"

    asyncio.run(run())


def test_generation_cas_discards_stale_result_after_policy_change(tmp_path):
    async def run():
        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'cas.db'}"), tables=BUILTIN_TABLES
        ) as db:
            ctx = RelationalContexts(database=db.database)
            sid = await scope(ctx, "User")
            await ctx.profiles.put_policy(sid, generation_enabled=True, expected_version=0)
            await ctx.records.create_source(sid, "content", "Chinese")
            entered, release = asyncio.Event(), asyncio.Event()

            class PausedGenerator:
                async def generate(self, value):
                    entered.set()
                    await release.wait()
                    return "# Stale"

            ctx.profiles.generator = PausedGenerator()
            task = asyncio.create_task(ctx.profiles.flush(sid))
            await entered.wait()
            policy = await ctx.profiles.get_policy(sid)
            await ctx.profiles.put_policy(sid, generation_enabled=False, expected_version=policy.version)
            release.set()
            result = await task
            assert result.status == "conflict"
            assert result.current_cursor == 0
            ctx.profiles.generator = Generator()
            policy = await ctx.profiles.get_policy(sid)
            await ctx.profiles.put_policy(sid, generation_enabled=True, expected_version=policy.version)
            assert (await ctx.profiles.flush(sid)).status == "updated"

    asyncio.run(run())


def test_concurrent_subject_initialization_has_no_orphan_scope(tmp_path):
    async def run():
        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'bindings.db'}"), tables=BUILTIN_TABLES
        ) as db:
            first, second = RelationalContexts(database=db.database), RelationalContexts(database=db.database)
            group = await scope(first, "Group")
            a, b = await asyncio.gather(
                first.subject_sources.create(group, "U1", "One"),
                second.subject_sources.create(group, "U1", "Two"),
            )
            assert a[0] == b[0]
            async with db.database.transaction() as connection:
                assert await connection.scalar(select(func.count()).select_from(SCOPES_TABLE)) == 2
                assert await connection.scalar(select(func.count()).select_from(SOURCES_TABLE)) == 4

    asyncio.run(run())


def test_concurrent_generators_commit_only_one_profile(tmp_path):
    async def run():
        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'generation.db'}"), tables=BUILTIN_TABLES
        ) as db:
            a, b = RelationalContexts(database=db.database), RelationalContexts(database=db.database)
            sid = await scope(a, "User")
            await a.profiles.put_policy(sid, generation_enabled=True, expected_version=0)
            await a.records.create_source(sid, "content", "Chinese")
            both = asyncio.Event()

            class BarrierGenerator:
                arrived = 0

                async def generate(self, value):
                    self.arrived += 1
                    if self.arrived == 2:
                        both.set()
                    await both.wait()
                    return "# Profile"

            a.profiles.generator = b.profiles.generator = BarrierGenerator()
            results = await asyncio.gather(a.profiles.flush(sid), b.profiles.flush(sid))
            assert sorted(r.status for r in results) == ["conflict", "updated"]
            assert (await a.records.get_artifact(sid, "profile", "profile")).revision == 1
            assert (await a.profiles.flush(sid)).status == "noop"

    asyncio.run(run())


def test_subject_second_source_failure_rolls_back_new_scope_and_binding(monkeypatch):
    async def run():
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as db:
            ctx = RelationalContexts(database=db.database)
            group = await scope(ctx, "Group")
            original = ctx.repositories.sources.add
            writes = 0

            async def fail_second(connection, scope_id, source):
                nonlocal writes
                writes += 1
                if writes == 2:
                    raise OSError("simulated storage failure")  # noqa: TRY003
                return await original(connection, scope_id, source)

            monkeypatch.setattr(ctx.repositories.sources, "add", fail_second)
            with pytest.raises(OSError):
                await ctx.subject_sources.create(group, "U1", "Chinese")
            key = ScopeBindingKey(integration="subject", kind="user", external_id="U1")
            assert await ctx.scopes.binding(key) is None
            async with db.database.transaction() as connection:
                assert await connection.scalar(select(func.count()).select_from(SCOPES_TABLE)) == 1
                assert await connection.scalar(select(func.count()).select_from(SOURCES_TABLE)) == 0

    asyncio.run(run())


def test_profile_created_after_authorization_cannot_be_overwritten():
    async def run():
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as db:
            ctx = RelationalContexts(database=db.database)
            sid = await scope(ctx, "User")
            await ctx.profiles.put_policy(sid, generation_enabled=True, expected_version=0)
            await ctx.records.create_source(sid, "content", "Chinese")
            ctx.profiles.generator = Generator()

            async def authorize_snapshot(current):
                assert current is None
                await ctx.records.create_artifact(
                    sid, "profile", ArtifactWrite(content={"content": "# Concurrent owner"})
                )

            result = await ctx.profiles.flush(sid, authorize_snapshot=authorize_snapshot)
            assert result.status == "conflict"
            assert result.current_cursor == 0
            saved = await ctx.records.get_artifact(sid, "profile", "profile")
            assert saved.revision == 1
            assert saved.content["content"] == "# Concurrent owner\n"

    asyncio.run(run())


def test_failed_generation_owner_write_preserves_window():
    async def run():
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as db:
            ctx = RelationalContexts(database=db.database)
            sid = await scope(ctx, "User")
            await ctx.profiles.put_policy(sid, generation_enabled=True, expected_version=0)
            await ctx.records.create_source(sid, "content", "Chinese")
            ctx.profiles.generator = Generator()

            async def failed_owner(*args):
                raise OSError("simulated owner persistence failure")  # noqa: TRY003

            with pytest.raises(OSError):
                await ctx.profiles.flush(sid, on_commit=failed_owner)
            result = await ctx.profiles.flush(sid)
            assert result.status == "updated" and result.previous_cursor == 0
            assert result.artifact is not None and result.artifact.revision == 1

    asyncio.run(run())
