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

"""The only writer of the recurrence ledger.

Every other path in PowerContext stays read-only with respect to this ledger:
``prepare_context`` and retrieval never touch it, and the pure matching rules in
``builtin.artifacts.experience.recurrence`` perform no IO at all. This module is
invoked from the Task Outcome incubation window, inside the window's own
transaction, and it is the only place that derives ledger events from evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.experience import Experience
from powercontext.builtin.artifacts.experience.incubation import TASK_OUTCOME_SOURCE_KIND
from powercontext.builtin.artifacts.experience.models import ExperienceContent
from powercontext.builtin.artifacts.experience.recurrence import (
    MAX_RECURRENCE_CANDIDATES,
    RECURRENCE_REVIEW_STREAK_THRESHOLD,
    CandidateSetMode,
    RecurrenceMatch,
    RecurrenceRevisionProposal,
    TaskOutcomeItemRef,
    avoided_refs,
    candidate_set_digest,
    eligible_candidates,
    failure_item_text,
    failure_refs,
    freeze_candidate_set,
    item_digest,
    match_key,
    match_result,
    new_observation,
    signature_key,
    terminal_streak,
)
from powercontext.builtin.artifacts.handoff import Handoff
from powercontext.builtin.artifacts.handoff.models import HandoffArtifactCitation, HandoffContent
from powercontext.builtin.persistence import RecurrenceRepository
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.sources import SourceRepository, StoredSource
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE
from powercontext.builtin.sources.content import ContentSource
from powercontext.builtin.work.models import HandoffReceipt, TaskOutcome
from powercontext.sources import SourceRef

_EXPERIENCE_CONTENT_SURFACE = "experience_content"
_Contents = tuple[tuple[ArtifactRef, ExperienceContent], ...]


@dataclass(frozen=True, slots=True)
class _LinkedHandoff:
    """One resolved Handoff chain: the receipt, the Handoff, and its Experiences."""

    receipt_ref: SourceRef
    handoff_ref: ArtifactRef
    contents: _Contents


class _UnresolvedHandoff:
    """Marker for a receipt that exists but cannot reconstruct its exact chain."""


@dataclass(frozen=True, slots=True)
class RelationalRecurrenceLedger:
    """Derive recurrence events from one Task Outcome window and append them.

    The ledger is append-only and idempotent: replaying one Source window
    reproduces the same keys, so the uniqueness constraints answer "already
    recorded" instead of duplicating history or re-invoking a generator.
    """

    database: AsyncDatabase
    scope_id: str
    sources: SourceRepository
    artifacts: ArtifactRepository
    recurrence: RecurrenceRepository

    async def record_window(
        self,
        connection: AsyncConnection,
        rows: tuple[StoredSource, ...],
        /,
    ) -> tuple[RecurrenceRevisionProposal, ...]:
        """Record every event one incubation window proves, in evidence order."""

        proposals: list[RecurrenceRevisionProposal] = []
        for row in rows:
            outcome = _task_outcome(row)
            if outcome is None:
                continue
            proposals.extend(await self._record_outcome(connection, row, outcome))
        return tuple(proposals)

    async def _record_outcome(
        self,
        connection: AsyncConnection,
        row: StoredSource,
        outcome: TaskOutcome,
        /,
    ) -> tuple[RecurrenceRevisionProposal, ...]:
        link = await self._resolve_link(connection, outcome)
        if not isinstance(link, (_LinkedHandoff, type(None))):
            return ()
        contents: _Contents = () if link is None else link.contents
        if link is not None:
            await self._record_selected(connection, row=row, link=link, contents=contents)
        candidates: _Contents = contents if link is not None else await self._experience_heads(connection)
        matches = await self._freeze_matches(
            connection,
            row=row,
            outcome=outcome,
            contents=candidates,
            mode="handoff_citations" if link is not None else "scope_heads",
        )
        if link is not None:
            await self._record_avoided(
                connection,
                row=row,
                outcome=outcome,
                link=link,
                contents=contents,
                matches=matches,
            )
        return await self._record_recurrences(connection, row=row, matches=matches)

    async def _record_selected(
        self,
        connection: AsyncConnection,
        *,
        row: StoredSource,
        link: _LinkedHandoff,
        contents: _Contents,
    ) -> None:
        """Rebuild ``selected`` from the Receipt and the Handoff that cited a revision."""

        for ref, content in sorted(contents, key=_ordered):
            if content.failure is None:
                continue
            await self._append(
                connection,
                event="selected",
                artifact_ref=ref,
                cue_key=signature_key(content.failure.signature.recall_cue),
                outcome_ref=row.ref,
                position=row.journal_position,
                handoff_receipt_ref=link.receipt_ref,
                handoff_ref=link.handoff_ref,
            )

    async def _record_avoided(
        self,
        connection: AsyncConnection,
        *,
        row: StoredSource,
        outcome: TaskOutcome,
        link: _LinkedHandoff,
        contents: _Contents,
        matches: tuple[RecurrenceMatch, ...],
    ) -> None:
        """Write ``avoided`` only where every evidence gate is actually proven."""

        recurring_keys = {
            match.signature_key for match in matches if match.result == "matched" and match.signature_key is not None
        }
        for ref, content in sorted(contents, key=_ordered):
            if content.failure is None:
                continue
            key = signature_key(content.failure.signature.recall_cue)
            if key in recurring_keys:
                continue
            pair = avoided_refs(outcome, row.ref, content)
            if pair is None:
                continue
            condition_ref, check_ref = pair
            await self._append(
                connection,
                event="avoided",
                artifact_ref=ref,
                cue_key=key,
                outcome_ref=row.ref,
                position=row.journal_position,
                handoff_receipt_ref=link.receipt_ref,
                handoff_ref=link.handoff_ref,
                condition_ref=condition_ref,
                check_ref=check_ref,
            )

    async def _record_recurrences(
        self,
        connection: AsyncConnection,
        *,
        row: StoredSource,
        matches: tuple[RecurrenceMatch, ...],
    ) -> tuple[RecurrenceRevisionProposal, ...]:
        proposals: list[RecurrenceRevisionProposal] = []
        for match in matches:
            if match.result != "matched" or match.artifact_ref is None or match.signature_key is None:
                continue
            recorded = await self._append(
                connection,
                event="recurred",
                artifact_ref=match.artifact_ref,
                cue_key=match.signature_key,
                outcome_ref=row.ref,
                position=row.journal_position,
                failure_ref=match.failure_ref,
                recurrence_match_digest=match_key(match),
            )
            if not recorded:
                continue
            proposal = await self._revision_proposal(
                connection,
                artifact_ref=match.artifact_ref,
                cue_key=match.signature_key,
                source=row.ref,
            )
            if proposal is not None:
                proposals.append(proposal)
        return tuple(proposals)

    async def _revision_proposal(
        self,
        connection: AsyncConnection,
        *,
        artifact_ref: ArtifactRef,
        cue_key: str,
        source: SourceRef,
    ) -> RecurrenceRevisionProposal | None:
        """Return a Review proposal when one revision's streak reaches the threshold.

        Only ``experience_content`` surfaces produce an artifact proposal. A
        ``recall_policy`` repair is a retrieval problem, so the ledger exposes it
        in statistics and proposes nothing.
        """

        content = await self._experience_content(connection, artifact_ref)
        if content is None or content.failure is None:
            return None
        try:
            current = await self.artifacts.latest(
                connection,
                self.scope_id,
                artifact_ref.family,
                artifact_ref.artifact_id,
            )
        except RepositoryNotFoundError:
            return None
        if current.as_ref() != artifact_ref:
            return None
        if content.failure.repair_surface != _EXPERIENCE_CONTENT_SURFACE:
            return None
        observations = await self.recurrence.observations(connection, self.scope_id)
        verdicts = tuple(
            observation
            for observation in observations
            if observation.artifact_ref == artifact_ref
            and observation.signature_key == cue_key
            and observation.event != "selected"
        )
        streak = terminal_streak(verdicts)
        if streak < RECURRENCE_REVIEW_STREAK_THRESHOLD:
            return None
        return RecurrenceRevisionProposal(
            target=artifact_ref,
            proposal=content,
            sources=(source,),
            reason=_revision_reason(streak, artifact_ref, cue_key),
        )

    async def _freeze_matches(
        self,
        connection: AsyncConnection,
        *,
        row: StoredSource,
        outcome: TaskOutcome,
        contents: _Contents,
        mode: CandidateSetMode,
    ) -> tuple[RecurrenceMatch, ...]:
        """Load or derive one matching decision per failure evidence item.

        An already recorded decision is returned untouched: replay must never ask
        a generator to choose again.
        """

        matches: list[RecurrenceMatch] = []
        for failure_ref in failure_refs(outcome, row.ref):
            existing = await self.recurrence.find_match(connection, self.scope_id, row.ref, failure_ref)
            if existing is not None:
                matches.append(existing)
                continue
            item = _item(outcome, failure_ref)
            if item is None:
                continue
            candidates = freeze_candidate_set(
                mode=mode,
                refs=tuple(ref for ref, _ in contents),
            )
            eligible = eligible_candidates(failure_item_text(item), contents)
            result = match_result(len(eligible))
            target = eligible[0] if result == "matched" else None
            target_content = None if target is None else _content_of(contents, target)
            match = RecurrenceMatch(
                scope_id=self.scope_id,
                task_outcome_ref=row.ref,
                task_outcome_position=row.journal_position,
                failure_ref=failure_ref,
                candidate_set_mode=mode,
                candidate_refs=candidates,
                candidate_set_digest=candidate_set_digest(candidates),
                result=result,
                artifact_ref=target,
                signature_key=(
                    None
                    if target_content is None or target_content.failure is None
                    else signature_key(target_content.failure.signature.recall_cue)
                ),
            )
            await self.recurrence.append_match(connection, match)
            matches.append(await self.recurrence.find_match(connection, self.scope_id, row.ref, failure_ref) or match)
        return tuple(matches)

    async def _append(
        self,
        connection: AsyncConnection,
        *,
        event: str,
        artifact_ref: ArtifactRef,
        cue_key: str,
        outcome_ref: SourceRef,
        position: int,
        handoff_receipt_ref: SourceRef | None = None,
        handoff_ref: ArtifactRef | None = None,
        condition_ref: TaskOutcomeItemRef | None = None,
        check_ref: TaskOutcomeItemRef | None = None,
        failure_ref: TaskOutcomeItemRef | None = None,
        recurrence_match_digest: str | None = None,
    ) -> bool:
        """Append one derived event, reporting whether this call recorded it."""

        return await self.recurrence.append_observation(
            connection,
            new_observation(
                event=event,
                scope_id=self.scope_id,
                artifact_ref=artifact_ref,
                signature_key=cue_key,
                task_outcome_ref=outcome_ref,
                task_outcome_position=position,
                handoff_receipt_ref=handoff_receipt_ref,
                handoff_ref=handoff_ref,
                condition_ref=condition_ref,
                check_ref=check_ref,
                failure_ref=failure_ref,
                recurrence_match_digest=recurrence_match_digest,
            ),
        )

    async def _resolve_link(
        self, connection: AsyncConnection, outcome: TaskOutcome, /
    ) -> _LinkedHandoff | _UnresolvedHandoff | None:
        """Rebuild the ``selected`` chain from a Receipt and its Handoff citations.

        Nothing here instruments the read path: the chain comes from the Receipt
        Source and the exact Handoff revision, both immutable.
        """

        receipt_ref = outcome.handoff_receipt_ref
        if receipt_ref is None:
            return None
        receipt = await self._receipt(connection, receipt_ref)
        if receipt is None:
            return _UNRESOLVED_HANDOFF
        handoff_ref = receipt.selected_revision
        if handoff_ref is None or handoff_ref.family != Handoff.family:
            return _UNRESOLVED_HANDOFF
        contents = await self._handoff_experiences(connection, handoff_ref)
        if contents is None:
            return _UNRESOLVED_HANDOFF
        return _LinkedHandoff(
            receipt_ref=receipt_ref,
            handoff_ref=handoff_ref,
            contents=contents,
        )

    async def _receipt(self, connection: AsyncConnection, receipt_ref: SourceRef, /) -> HandoffReceipt | None:
        try:
            stored = await self.sources.get(connection, self.scope_id, receipt_ref)
        except RepositoryNotFoundError:
            return None
        if not isinstance(stored.value, ContentSource):
            return None
        try:
            receipt = HandoffReceipt.model_validate_json(stored.value.content)
        except ValidationError:
            return None
        if receipt.status != "accepted" or receipt.selection != "exact":
            return None
        return receipt

    async def _handoff_experiences(
        self,
        connection: AsyncConnection,
        handoff_ref: ArtifactRef,
        /,
    ) -> _Contents | None:
        try:
            handoff = await self.artifacts.get(connection, self.scope_id, handoff_ref)
        except RepositoryNotFoundError:
            return None
        if not isinstance(handoff.content, HandoffContent):
            return None
        citations = _unique_refs(handoff_experience_citations(handoff.content))[:MAX_RECURRENCE_CANDIDATES]
        return await self._contents(connection, citations)

    async def _experience_heads(self, connection: AsyncConnection, /) -> _Contents:
        """Snapshot the active Experience heads of this scope, bounded and sorted."""

        rows = (
            await connection.execute(
                select(
                    ARTIFACT_HEADS_TABLE.c.artifact_id,
                    ARTIFACT_HEADS_TABLE.c.revision,
                )
                .where(
                    ARTIFACT_HEADS_TABLE.c.scope_id == self.scope_id,
                    ARTIFACT_HEADS_TABLE.c.family == Experience.family,
                    ARTIFACT_HEADS_TABLE.c.lifecycle_state == "active",
                )
                .order_by(ARTIFACT_HEADS_TABLE.c.artifact_id, ARTIFACT_HEADS_TABLE.c.revision)
                .limit(MAX_RECURRENCE_CANDIDATES)
            )
        ).all()
        refs = tuple(
            ArtifactRef(family=Experience.family, artifact_id=str(row[0]), revision=int(row[1])) for row in rows
        )
        return await self._contents(connection, refs)

    async def _contents(self, connection: AsyncConnection, refs: tuple[ArtifactRef, ...], /) -> _Contents:
        if not refs:
            return ()
        contents: list[tuple[ArtifactRef, ExperienceContent]] = []
        for ref in refs:
            try:
                artifact = await self.artifacts.get(connection, self.scope_id, ref)
            except RepositoryNotFoundError:
                continue
            if isinstance(artifact.content, ExperienceContent):
                contents.append((artifact.as_ref(), artifact.content))
        return tuple(contents)

    async def _experience_content(self, connection: AsyncConnection, ref: ArtifactRef, /) -> ExperienceContent | None:
        try:
            artifact = await self.artifacts.get(connection, self.scope_id, ref)
        except RepositoryNotFoundError:
            return None
        return artifact.content if isinstance(artifact.content, ExperienceContent) else None


def _task_outcome(row: StoredSource, /) -> TaskOutcome | None:
    value = row.value
    if not isinstance(value, ContentSource) or value.metadata.get("kind") != TASK_OUTCOME_SOURCE_KIND:
        return None
    try:
        return TaskOutcome.model_validate_json(value.content)
    except ValidationError:
        return None


def _item(outcome: TaskOutcome, ref: TaskOutcomeItemRef, /) -> Any | None:
    items: tuple[Any, ...] = outcome.checks if ref.item_kind == "check" else outcome.observations
    if ref.item_index >= len(items):
        return None
    item = items[ref.item_index]
    return item if item_digest(item) == ref.item_digest else None


def handoff_experience_citations(content: HandoffContent, /) -> tuple[ArtifactRef, ...]:
    """Return every cited Experience revision inside one Handoff, in citation order."""

    citations: list[Any] = [citation for statement in content.state for citation in statement.citations]
    if content.next_action is not None:
        citations.extend(content.next_action.citations)
    return tuple(
        citation.artifact_ref
        for citation in citations
        if isinstance(citation, HandoffArtifactCitation) and citation.artifact_ref.family == Experience.family
    )


def _unique_refs(refs: tuple[ArtifactRef, ...], /) -> tuple[ArtifactRef, ...]:
    unique: dict[tuple[str, str, int], ArtifactRef] = {}
    for ref in refs:
        unique.setdefault((ref.family, ref.artifact_id, ref.revision), ref)
    return tuple(unique.values())


_UNRESOLVED_HANDOFF = _UnresolvedHandoff()


def _ordered(pair: tuple[ArtifactRef, ExperienceContent], /) -> tuple[str, str, int]:
    ref = pair[0]
    return ref.family, ref.artifact_id, ref.revision


def _content_of(contents: _Contents, ref: ArtifactRef, /) -> ExperienceContent | None:
    identity = (ref.family, ref.artifact_id, ref.revision)
    for candidate, content in contents:
        if (candidate.family, candidate.artifact_id, candidate.revision) == identity:
            return content
    return None


def _revision_reason(streak: int, ref: ArtifactRef, cue_key: str, /) -> str:
    return (
        f"recurred {streak} times without an avoided verdict"
        f" ({ref.family}/{ref.artifact_id} revision {ref.revision}, cue: {cue_key})"
    )


__all__ = ["RelationalRecurrenceLedger", "handoff_experience_citations"]
