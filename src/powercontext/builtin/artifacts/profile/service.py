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


"""Bounded Profile generation with policy, cursor and head compare-and-swap."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, nullcontext
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.profile.models import (
    PROFILE_ARTIFACT_ID,
    PROFILE_SOURCE_WINDOW_BINDING,
    Profile,
    ProfileActivationMode,
    ProfileCandidateProposal,
    ProfileContent,
    ProfileDraft,
    ProfileFlushResult,
    ProfileGeneration,
    ProfilePolicy,
    SourceWindow,
    normalize_profile_markdown,
)
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.candidates import CandidateRepository
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.database import AsyncDatabase, is_transaction_contention
from powercontext.builtin.persistence.errors import GenerationConflictError, RepositoryNotFoundError
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.profile import ProfilePolicyRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.tables import PROFILE_POLICIES_TABLE
from powercontext.builtin.records import BaseValueConflictError, BaseValueNotFoundError
from powercontext.builtin.scope.errors import ScopeNotFoundError
from powercontext.builtin.scope.repository import ScopeRepository
from powercontext.builtin.source_eligibility import is_generation_eligible
from powercontext.builtin.sources import SourceCursor
from powercontext.errors import RevisionConflictError

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.prompt.service import PromptService
    from powercontext.builtin.runtime.processing_execution import ScopeInvocation


class ProfileGenerationInput(BaseModel):
    previous_content: str | None
    sources: tuple[str, ...]


class ProfileGenerationOutput(BaseModel):
    content: str | None = None


class ProfileGenerator(Protocol):
    async def generate(self, value: ProfileGenerationInput, /) -> str | None: ...


def _profile_prompt_refs():
    # Import locally because Prompt Definitions import the Profile generation contract.
    from powercontext.builtin.artifacts.prompt.service import current_prompt

    selection = current_prompt("profile.generate")
    return () if selection is None or selection.artifact is None else (selection.artifact,)


class RelationalProfileService:
    def __init__(
        self,
        database: AsyncDatabase,
        sources: SourceRepository,
        artifacts: ArtifactRepository,
        candidates: CandidateRepository,
        *,
        generator: ProfileGenerator | None = None,
        prompt_service: PromptService | None = None,
        id_factory: Callable[[str], str] | None = None,
        max_sources: int = 32,
    ):
        if not 1 <= max_sources <= 32:
            raise ValueError("profile window size must be between 1 and 32")  # noqa: TRY003
        self.database = database
        self.sources = sources
        self.artifacts = artifacts
        self.candidates = candidates
        self.policies = ProfilePolicyRepository()
        self.cursors = SourceCursorRepository()
        self.generator = generator
        self._prompt_service = prompt_service
        self.operation_context: Callable[[], AbstractAsyncContextManager[Any]] = nullcontext
        self.scheduled_runner: Callable[[str, int], Awaitable[ProfileFlushResult]] | None = None
        self.max_sources = max_sources
        self.id_factory = id_factory or (lambda kind: f"{kind}_{uuid4().hex}")

    async def get_policy(self, scope_id: str) -> ProfilePolicy:
        async with self.database.transaction() as connection:
            policy = await self.policies.get(connection, scope_id)
            if policy is None:
                raise BaseValueNotFoundError("profile_policy", (scope_id,))
            return policy

    async def put_policy(
        self,
        scope_id: str,
        *,
        generation_enabled: bool,
        activation_mode: ProfileActivationMode = "automatic",
        expected_version: int,
    ) -> ProfilePolicy:
        try:
            async with self.database.transaction() as connection:
                if await ScopeRepository().get(connection, scope_id) is None:
                    raise ScopeNotFoundError(scope_id)
                current = await self.policies.get(connection, scope_id, for_update=True)
                if current is None:
                    if expected_version != 0:
                        raise BaseValueConflictError("profile_policy", (scope_id,))
                    updated = await self.policies.create(
                        connection,
                        scope_id,
                        enabled=generation_enabled,
                        activation_mode=activation_mode,
                    )
                    await ArtifactProcessingIntentRepository().mark_dirty(
                        connection, scope_id, PROFILE_SOURCE_WINDOW_BINDING
                    )
                    return updated
                elif current.version != expected_version or expected_version == 0:
                    raise BaseValueConflictError("profile_policy", (scope_id,))
                updated = await self.policies.update(
                    connection,
                    current,
                    generation_enabled=generation_enabled,
                    activation_mode=activation_mode,
                )
                await ArtifactProcessingIntentRepository().mark_dirty(
                    connection, scope_id, PROFILE_SOURCE_WINDOW_BINDING
                )
                return updated
        except IntegrityError as error:
            raise BaseValueConflictError("profile_policy", (scope_id,)) from error

    async def latest(self, connection, scope_id):
        try:
            return await self.artifacts.latest(connection, scope_id, "profile", PROFILE_ARTIFACT_ID)
        except RepositoryNotFoundError:
            return None

    async def flush(
        self,
        scope_id: str,
        *,
        high_watermark: int | None = None,
        authorize_snapshot: Callable[[Profile | None], Awaitable[None]] | None = None,
        on_commit=None,
        processing: ScopeInvocation | None = None,
        authorize_commit: Callable[[AsyncConnection, Profile | None], Awaitable[None]] | None = None,
    ) -> ProfileFlushResult:
        async with self.operation_context():
            if self._prompt_service is None:
                return await self._flush(
                    scope_id,
                    high_watermark=high_watermark,
                    authorize_snapshot=authorize_snapshot,
                    on_commit=on_commit,
                    processing=processing,
                    authorize_commit=authorize_commit,
                )
            async with self._prompt_service.bind(scope_id, "profile.generate"):
                return await self._flush(
                    scope_id,
                    high_watermark=high_watermark,
                    authorize_snapshot=authorize_snapshot,
                    on_commit=on_commit,
                    processing=processing,
                    authorize_commit=authorize_commit,
                )

    async def _flush(  # noqa: C901
        self,
        scope_id: str,
        *,
        high_watermark: int | None = None,
        authorize_snapshot: Callable[[Profile | None], Awaitable[None]] | None = None,
        on_commit=None,
        processing: ScopeInvocation | None = None,
        authorize_commit: Callable[[AsyncConnection, Profile | None], Awaitable[None]] | None = None,
    ) -> ProfileFlushResult:
        async with self.database.transaction() as connection:
            if processing is not None:
                await processing.start(connection)
            policy = await self.policies.get(connection, scope_id)
            cursor = await self.cursors.load(connection, scope_id, PROFILE_SOURCE_WINDOW_BINDING)
            after = 0 if cursor is None else cursor.cursor.sequence
            high = await self.sources.journal_position(connection, scope_id)
            high = high if high_watermark is None else min(high, high_watermark)
            base: dict[str, Any] = {"previous_cursor": after, "current_cursor": after, "high_watermark": high}
            if policy is None or not policy.generation_enabled:
                if processing is not None:
                    await processing.complete(connection, remaining_work=after < high)
                return ProfileFlushResult(status="disabled", **base)
            if policy.pending_candidate_id is not None:
                if processing is not None:
                    await processing.complete(connection, remaining_work=True)
                return ProfileFlushResult(status="review_pending", candidate_id=policy.pending_candidate_id, **base)
            current = await self.latest(connection, scope_id)
            prompt_refs = _profile_prompt_refs()
            reserved_artifacts = (0 if current is None else 1) + len(prompt_refs)
            limit = min(self.max_sources, 32 - reserved_artifacts)
            window = tuple(
                item
                for item in await self.sources.list(connection, scope_id, after=after, limit=limit)
                if item.journal_position <= high
            )
            if not window and processing is not None:
                await processing.complete(connection, remaining_work=False)
        if not window:
            return ProfileFlushResult(status="noop", **base)
        # Authorize the exact Head used by generation. The commit CAS below also
        # rejects a Head created or changed after this snapshot was authorized.
        if authorize_snapshot is not None:
            await authorize_snapshot(current)
        through = window[-1].journal_position
        evidence = tuple(item for item in window if is_generation_eligible(item.value))
        markdown = None
        if evidence:
            if self.generator is None:
                # Import locally to avoid a cycle through the shared Review service.
                from powercontext.builtin.review.generation import GenerationCapabilityUnavailableError

                raise GenerationCapabilityUnavailableError("profile")
            markdown = await self.generator.generate(
                ProfileGenerationInput(
                    previous_content=None if current is None else current.content.content,
                    sources=tuple(item.value.model_dump_json() for item in evidence),
                )
            )
            if markdown is not None:
                try:
                    markdown = normalize_profile_markdown(markdown)
                except ValueError as error:
                    from powercontext.builtin.inference import InvalidInferenceOutputError

                    raise InvalidInferenceOutputError("profile-generate", "invalid Markdown") from error
                if current is not None and current.content.content == markdown:
                    markdown = None
        try:
            async with self.database.transaction() as connection:
                if processing is not None:
                    await processing.guard(connection)
                locked = await self.policies.get(connection, scope_id, for_update=True)
                actual_cursor = await self.cursors.load(
                    connection, scope_id, PROFILE_SOURCE_WINDOW_BINDING, for_update=True
                )
                actual = await self.latest(connection, scope_id)
                if (
                    locked != policy
                    or actual_cursor != cursor
                    or (None if actual is None else actual.as_ref()) != (None if current is None else current.as_ref())
                ):
                    raise BaseValueConflictError("profile_processing", (scope_id,))  # noqa: TRY301
                if authorize_commit is not None:
                    await authorize_commit(connection, current)
                # CAS also serializes SQLite, whose SELECT FOR UPDATE is a no-op.
                updated = await self.policies.update(connection, policy)
                refs = tuple(item.ref for item in evidence)
                parents = () if current is None else (current.as_ref(),)
                artifacts = (*parents, *prompt_refs)
                source_window = SourceWindow(after=after, through=through)
                candidate_id = None
                candidate = None
                artifact = None
                if markdown is not None and policy.activation_mode == "review_required":
                    candidate_id = self.id_factory("candidate")
                    candidate = await self.candidates.create(
                        connection,
                        scope_id,
                        candidate_id,
                        "profile",
                        ProfileCandidateProposal(
                            content=markdown,
                            source_window=source_window,
                            generator_id=PROFILE_SOURCE_WINDOW_BINDING,
                            generator_version="1",
                            created_at=datetime.now(UTC),
                        ),
                        sources=refs,
                        artifacts=artifacts,
                        target=None if current is None else current.as_ref(),
                        reason="New source evidence",
                    )
                    await self.policies.update(connection, updated, pending_candidate_id=candidate_id)
                else:
                    if markdown is not None:
                        draft = ProfileDraft(
                            content=ProfileContent(
                                content=markdown,
                                generation=ProfileGeneration(
                                    mode="automatic",
                                    created_at=datetime.now(UTC),
                                    generator_id=PROFILE_SOURCE_WINDOW_BINDING,
                                    generator_version="1",
                                    source_window=source_window,
                                ),
                            ),
                            sources=refs,
                            artifacts=artifacts,
                        )
                        artifact = (
                            await self.artifacts.create(connection, scope_id, PROFILE_ARTIFACT_ID, draft)
                            if current is None
                            else await self.artifacts.revise(connection, scope_id, current, draft)
                        )
                    await self.cursors.save(
                        connection,
                        scope_id,
                        PROFILE_SOURCE_WINDOW_BINDING,
                        SourceCursor(sequence=through),
                        expected_generation=None if cursor is None else cursor.generation,
                    )
                if on_commit is not None:
                    await on_commit(connection, artifact, candidate)
                if processing is not None:
                    await processing.complete(connection, remaining_work=candidate_id is not None or through < high)
                return ProfileFlushResult(
                    status="review_pending" if candidate_id else ("updated" if artifact else "noop"),
                    previous_cursor=after,
                    current_cursor=after if candidate_id else through,
                    high_watermark=high,
                    processed_source_count=len(evidence),
                    candidate_id=candidate_id,
                    artifact=None if artifact is None else artifact.as_ref(),
                )
        except (
            BaseValueConflictError,
            GenerationConflictError,
            RevisionConflictError,
            IntegrityError,
            OperationalError,
        ) as error:
            if isinstance(error, OperationalError) and not is_transaction_contention(error):
                raise
            async with self.database.transaction() as connection:
                refreshed = await self.cursors.load(connection, scope_id, PROFILE_SOURCE_WINDOW_BINDING)
            return ProfileFlushResult(
                status="conflict",
                previous_cursor=after,
                current_cursor=0 if refreshed is None else refreshed.cursor.sequence,
                high_watermark=high,
                processed_source_count=len(evidence),
            )

    async def scan(self, *, max_concurrency: int = 4) -> None:
        # Page policies; each scope gets a fixed high watermark and bounded work.
        last = ""
        semaphore = asyncio.Semaphore(max_concurrency)

        async def process(scope_id):
            async with semaphore:
                async with self.database.transaction() as connection:
                    high = await self.sources.journal_position(connection, scope_id)
                for _ in range(100):
                    result = (
                        await self.flush(scope_id, high_watermark=high)
                        if self.scheduled_runner is None
                        else await self.scheduled_runner(scope_id, high)
                    )
                    if result.status in {"disabled", "review_pending", "conflict"} or result.current_cursor >= high:
                        break

        while True:
            async with self.database.transaction() as connection:
                scope_ids = tuple(
                    (
                        await connection.execute(
                            select(PROFILE_POLICIES_TABLE.c.scope_id)
                            .where(
                                PROFILE_POLICIES_TABLE.c.generation_enabled.is_(True),
                                PROFILE_POLICIES_TABLE.c.scope_id > last,
                            )
                            .order_by(PROFILE_POLICIES_TABLE.c.scope_id)
                            .limit(100)
                        )
                    ).scalars()
                )
            if not scope_ids:
                break
            results = await asyncio.gather(*(process(scope_id) for scope_id in scope_ids), return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    if isinstance(result, asyncio.CancelledError):
                        raise result
                    logging.getLogger(__name__).warning("Profile scan failed: %s", type(result).__name__)
            last = scope_ids[-1]
