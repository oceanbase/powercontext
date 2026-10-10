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

"""Runtime acceptance for the recall-sufficiency gate and its bounded expansion.

These tests exercise the real SQLite FTS recall path through the in-process runtime seam
(``ScopedContextApplication._prepare_build``) rather than the pure gate module: a three-term
query lets the round-zero admission floor reject a candidate that the lower round-one floor
re-admits, which is exactly the behaviour the gate exists to drive.
"""

from __future__ import annotations

import asyncio
import math
import sqlite3
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import select

from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
from powercontext.builtin.artifacts.atomic_memory.errors import AtomicMemoryConflictError
from powercontext.builtin.artifacts.experience import ExperienceSearchOutcome
from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.artifacts.search import AdmissionCounts
from powercontext.builtin.artifacts.topic_memory import (
    TopicMemoryContent,
    TopicMemoryDraft,
    prepare_topic_memory_projection,
)
from powercontext.builtin.inference import EmbeddingResult, InferenceUnavailableError
from powercontext.builtin.persistence.records import RelationalRecordService
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.statistics import StatisticsRepository
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE, ARTIFACTS_TABLE, RECALL_EFFORT_DAILY_TABLE
from powercontext.builtin.runtime import (
    BuiltinConfig,
    PrepareContextRequest,
    RuntimeConfig,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.application import (
    BuiltinRuntime,
    ScopedContextApplication,
    _RecallRoundOutcome,
)
from powercontext.builtin.runtime.prepared_context import (
    PreparedContextBuild,
    PreparedContextBuilder,
    PreparedMemoryCandidates,
)
from powercontext.builtin.runtime.recall_sufficiency import (
    MEMORY_FAMILY,
    REASON_AT_MAX_ROUNDS,
    REASON_BUDGET_FLOOR,
    REASON_EXPANSION_FAILED,
    REASON_NO_CONTENT,
    REASON_SUFFICIENT,
    RecallSufficiencyGate,
    recall_effort_measurement,
)
from powercontext.builtin.runtime.topic_memory_search import TopicMemorySearcher
from powercontext.builtin.scope import ScopeDraft

# A three-term query is required: the round-zero floor only differs from the round-one floor
# once the query has more than two Analyzer terms (``fts_query_requirements`` clamps a short
# query to a single required match).
_QUERY = "alpha beta gamma"
_MEMORY_ONLY = {"sections": [{"family": "memory", "limit": 8}]}
_TOPIC_MEMORY_ONLY = {"sections": [{"family": "topic-memory", "limit": 8}]}


class _RecallRoundLog:
    """Record every ``_recall_round`` invocation and optionally starve a later round."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.empty_from: int | None = None
        self.force_recoverable_family: str | None = None

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        original = ScopedContextApplication._recall_round
        log = self

        async def counted(
            application: ScopedContextApplication,
            request: PrepareContextRequest,
            scope_ids: Any,
            families: set[str],
            builder: Any,
            *,
            admission: Any,
            reuse: Any,
            topic_reuse: Any,
        ) -> Any:
            log.calls.append({"families": set(families), "admission": admission})
            if log.empty_from is not None and len(log.calls) >= log.empty_from:
                return _RecallRoundOutcome()
            result = await original(
                application,
                request,
                scope_ids,
                families,
                builder,
                admission=admission,
                reuse=reuse,
                topic_reuse=topic_reuse,
            )
            log.calls[-1]["memory_recoverable"] = result.memory_recoverable
            log.calls[-1]["memory_texts"] = tuple(hit.text for group in result.memory for hit in group.hits)
            if log.force_recoverable_family is None:
                return result
            return _RecallRoundOutcome(
                memory=result.memory,
                experience=result.experience,
                topic_memory=result.topic_memory,
                admissions=(
                    AdmissionCounts(
                        family=log.force_recoverable_family,
                        scope_id=scope_ids[0],
                        retrieved=2,
                        admitted=1,
                    ),
                ),
                embedding_calls=result.embedding_calls,
                generation_calls=result.generation_calls,
            )

        monkeypatch.setattr(ScopedContextApplication, "_recall_round", counted)


@asynccontextmanager
async def _runtime(
    database: Path,
    runtime: RuntimeConfig | None = None,
    *,
    in_memory: bool = False,
    embedding_model: Any = None,
) -> AsyncIterator[BuiltinRuntime]:
    config = BuiltinConfig(
        database=SQLiteConfig() if in_memory else SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"),
        runtime=runtime if runtime is not None else RuntimeConfig(),
    )
    async with open_builtin_runtime(
        config, scheduler_path=database.with_suffix(".scheduler.db"), embedding_model=embedding_model
    ) as opened:
        yield opened


def _entry(text: str) -> AtomicMemoryContent:
    return AtomicMemoryContent(kind="fact", text=text)


async def _seed(runtime: BuiltinRuntime, scope_id: str, texts: list[str]) -> None:
    assert runtime.atomic_memory is not None
    await runtime.atomic_memory.for_scope(scope_id).create(tuple(_entry(text) for text in texts))


async def _seed_topic_memories(runtime: BuiltinRuntime, scope_id: str, count: int) -> None:
    provider = cast(Any, runtime._provider)
    async with provider.database.transaction() as connection:
        for position in range(count):
            content = TopicMemoryContent(
                title=f"alpha beta topic {position:02d}",
                summary=f"Useful evidence {position}",
                detail="unrelated detail",
            )
            await provider.repositories.topic_memories.publish_create(
                connection,
                scope_id,
                f"topic-over-cap-{position:02d}",
                TopicMemoryDraft(content=content),
                prepare_topic_memory_projection(content),
            )


async def _create_scope(runtime: BuiltinRuntime, key: str, *, references: tuple[str, ...] = ()) -> str:
    assert runtime.scopes is not None
    created = await runtime.scopes.create(
        ScopeDraft(
            title="Gate",
            summary="Recall gate acceptance",
            idempotency_key=key,
            context_references=references,
        )
    )
    return created.scope_id


def _memory_request(*, max_bytes: int = 8000, assembly: dict[str, Any] | None = None) -> PrepareContextRequest:
    payload: dict[str, Any] = {"query": _QUERY, "max_bytes": max_bytes}
    payload["assembly"] = _MEMORY_ONLY if assembly is None else assembly
    return PrepareContextRequest.model_validate(payload)


async def _prepare_build(
    runtime: BuiltinRuntime,
    scope_id: str,
    request: PrepareContextRequest,
) -> tuple[PreparedContextBuild, Any]:
    application = runtime.context.for_scope(scope_id)
    async with runtime._scope_operation(scope_id) as scope:
        return await application._prepare_build(request, scope)


async def _effort_rows(runtime: BuiltinRuntime) -> list[dict[str, Any]]:
    provider = cast(Any, runtime._provider)
    async with provider.database.transaction() as connection:
        return [dict(row) for row in (await connection.execute(select(RECALL_EFFORT_DAILY_TABLE))).mappings()]


async def _revision_state(runtime: BuiltinRuntime, scope_id: str) -> tuple[list[Any], ...]:
    provider = cast(Any, runtime._provider)
    async with provider.database.transaction() as connection:
        snapshots = []
        for table in (ARTIFACTS_TABLE, ARTIFACT_HEADS_TABLE):
            snapshots.append((await connection.execute(select(table).where(table.c.scope_id == scope_id))).all())
        return tuple(snapshots)


def test_default_off_matches_a_sufficient_round_zero_byte_for_byte(tmp_path, monkeypatch) -> None:
    log = _RecallRoundLog()
    log.install(monkeypatch)

    async def scenario() -> None:
        database = tmp_path / "e1.db"
        request = _memory_request()

        async with _runtime(database) as disabled:
            scope_id = await _create_scope(disabled, "e1")
            await _seed(disabled, scope_id, [f"alpha beta gamma memory {index}" for index in range(8)])
            disabled_build = await _prepare_build(disabled, scope_id, request)
        disabled_build, disabled_effort = disabled_build
        assert disabled_effort is None
        assert len(log.calls) == 1

        log.calls.clear()
        async with _runtime(
            database,
            RuntimeConfig(recall_gate_enabled=True, recall_gate_min_top_gap=0.0),
        ) as enabled:
            enabled_build = await _prepare_build(enabled, scope_id, request)

        enabled_build, effort = enabled_build
        assert effort is not None
        assert len(log.calls) == 1
        assert effort.rounds == 1
        assert effort.assessment == REASON_SUFFICIENT
        assert len(effort.candidates_by_round) == 1
        assert enabled_build.context.status == "ready"
        assert enabled_build.context.content == disabled_build.context.content
        assert enabled_build.context.content_bytes == disabled_build.context.content_bytes
        assert enabled_build.origins == disabled_build.origins

    asyncio.run(scenario())


def test_sqlite_vector_prepare_reports_cosine_and_expands_past_a_weak_top(tmp_path, monkeypatch) -> None:
    embedding_profile = EmbeddingProfile(
        profile_id="recall-gate-vector", model="deterministic", dimension=2, distance="l2", normalization="unit"
    )

    class DeterministicEmbedding:
        profile = embedding_profile

        async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
            similarities = tuple(
                1.0 if text == _QUERY else 0.2 if text.removeprefix("fact\n") == "unrelated vector evidence" else 0.31
                for text in texts
            )
            return EmbeddingResult(
                vectors=tuple((similarity, math.sqrt(1.0 - similarity**2)) for similarity in similarities)
            )

    log = _RecallRoundLog()
    log.install(monkeypatch)

    async def scenario() -> None:
        database = tmp_path / "vector-gate.db"
        async with _runtime(
            database,
            RuntimeConfig(recall_gate_enabled=True, recall_gate_min_candidates=1),
            embedding_model=DeterministicEmbedding(),
        ) as runtime:
            scope_id = await _create_scope(runtime, "vector-gate")
            await _seed(runtime, scope_id, ["alpha beta gamma vector evidence", "unrelated vector evidence"])
            build, effort = await _prepare_build(runtime, scope_id, _memory_request())

        # weak-top-1 is not terminal: the recoverable below-floor hit is re-admitted by one
        # expansion round, and the reason stays weak-top-1 once nothing remains recoverable.
        assert effort is not None
        assert effort.assessment == "weak-top-1"
        assert effort.rounds == 2
        assert effort.candidates_by_round == (1, 2)
        assert effort.signals is not None
        assert effort.signals.top_score == pytest.approx(0.31, abs=0.005)
        assert len(log.calls) == 2
        assert "unrelated vector evidence" in (build.context.content or "")
        # Atomic filters before LIMIT and reports an existence probe rather than
        # fabricating legacy pre-admission counts.
        assert effort.admission_by_family == ()
        assert [call["memory_recoverable"] for call in log.calls] == [True, False]

    asyncio.run(scenario())


def test_bounded_expansion_surfaces_a_strong_hit_hidden_by_the_fusion_limit(tmp_path, monkeypatch) -> None:
    """The fused window is capped before the gate sees hits, so a weak top cannot be
    declared unimprovable: a strong vector-only hit outside the window gains an FTS
    channel under the lowered floor and RRF moves it into view."""
    embedding_profile = EmbeddingProfile(
        profile_id="recall-gate-hidden", model="deterministic", dimension=2, distance="l2", normalization="unit"
    )
    weak_texts = tuple(f"beta gamma weak {index:02d}" for index in range(16))
    strong_text = "alpha window hidden"
    cosines = {_QUERY: 1.0, strong_text: 0.90}
    cosines.update({text: 0.31 + index * 0.001 for index, text in enumerate(weak_texts)})

    class DeterministicEmbedding:
        profile = embedding_profile

        async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
            return EmbeddingResult(
                vectors=tuple(
                    (cosine := cosines.get(text.removeprefix("fact\n"), 0.0), math.sqrt(1.0 - cosine * cosine))
                    for text in texts
                )
            )

    log = _RecallRoundLog()
    log.install(monkeypatch)

    # Capture the committed candidates handed to the Builder: the verdict's evidence must
    # be inside the delivery cap, not trailing the round-zero prefix. The section render
    # limit (<= 8 items) is a separate request-level display cap and may still clip it.
    delivered: list[PreparedMemoryCandidates] = []
    original_build = PreparedContextBuilder.build_scopes_result

    def spying_build(self: PreparedContextBuilder, **kwargs: Any) -> PreparedContextBuild:
        delivered.extend(kwargs["memory_candidates"])
        return original_build(self, **kwargs)

    monkeypatch.setattr(PreparedContextBuilder, "build_scopes_result", spying_build)

    async def scenario() -> None:
        database = tmp_path / "hidden-hit.db"
        async with _runtime(
            database,
            RuntimeConfig(recall_gate_enabled=True),
            embedding_model=DeterministicEmbedding(),
        ) as runtime:
            scope_id = await _create_scope(runtime, "hidden-hit")
            # Each channel is capped at sixteen. The weakest vector row and the
            # strong vector-only row tie for the last fused slot; stable IDs put
            # the FTS-only weak row first. Under the lower lexical floor, the
            # strong row's rare alpha term ranks first and gives it both channels.
            records = cast(RelationalRecordService, runtime._records())
            original_id_factory = records._id_factory
            ids = iter(f"hidden-memory-{index:02d}" for index in range(17))
            monkeypatch.setattr(
                records,
                "_id_factory",
                lambda kind: next(ids) if kind == "atomic-memory" else original_id_factory(kind),
            )
            await _seed(runtime, scope_id, [*weak_texts, strong_text])
            _, effort = await _prepare_build(runtime, scope_id, _memory_request())

        # Round 0: fifteen dual-channel and one FTS-only weak hit fill the window and truncate
        # the vector-only 0.90 hit, so the gate sees a weak top. Round 1's lower lexical
        # floor admits the strong hit's FTS row; the dual-channel hit then enters the
        # window, the top score reflects it, and the committed candidates keep the latest
        # round's fused order so the surfaced hit is actually delivered to the Builder.
        assert effort is not None
        assert effort.assessment == REASON_SUFFICIENT
        assert effort.rounds == 2
        assert effort.candidates_by_round == (16, 17)
        assert effort.signals is not None
        assert effort.signals.top_score == pytest.approx(0.90, abs=0.005)
        assert len(log.calls) == 2
        assert strong_text not in log.calls[0]["memory_texts"]
        assert strong_text in log.calls[1]["memory_texts"]
        assert any(strong_text in hit.text for group in delivered for hit in group.hits)
        assert [call["memory_recoverable"] for call in log.calls] == [True, False]

    asyncio.run(scenario())


def test_expansion_fts_only_hit_preserves_round_zero_scored_family(tmp_path, monkeypatch) -> None:
    embedding_profile = EmbeddingProfile(
        profile_id="recall-gate-first-hit", model="deterministic", dimension=2, distance="l2", normalization="unit"
    )

    class DeterministicEmbedding:
        profile = embedding_profile

        async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
            vectors = {_QUERY: (1.0, 0.0), "alpha beta gamma evidence": (0.5, math.sqrt(0.75))}
            return EmbeddingResult(
                vectors=tuple(vectors.get(text.removeprefix("fact\n"), (0.0, 1.0)) for text in texts)
            )

    original = ScopedContextApplication._recall_round
    expansion_first_distance = []

    async def fts_first_expansion(*args: Any, **kwargs: Any) -> _RecallRoundOutcome:
        result = await original(*args, **kwargs)
        if kwargs["admission"] is None:
            return result
        # Put the new FTS-only hit first in the returned round. Accumulation must still append it
        # after round zero's vector-backed first hit rather than replace the family's first hit.
        memory = tuple(replace(group, hits=tuple(reversed(group.hits))) for group in result.memory)
        expansion_first_distance.extend(group.hits[0].hit.distance for group in memory if group.hits)
        return replace(result, memory=memory)

    monkeypatch.setattr(ScopedContextApplication, "_recall_round", fts_first_expansion)

    async def scenario() -> None:
        database = tmp_path / "first-hit.db"
        request = _memory_request()
        async with _runtime(
            database,
            RuntimeConfig(recall_gate_enabled=True, recall_gate_max_rounds=0),
            embedding_model=DeterministicEmbedding(),
        ) as runtime:
            scope_id = await _create_scope(runtime, "first-hit")
            await _seed(runtime, scope_id, ["alpha beta gamma evidence", "alpha solo evidence"])
            _before_build, before_effort = await _prepare_build(runtime, scope_id, request)

        async with _runtime(
            database,
            RuntimeConfig(recall_gate_enabled=True),
            embedding_model=DeterministicEmbedding(),
        ) as runtime:
            after_build, after_effort = await _prepare_build(runtime, scope_id, request)

        assert before_effort is not None
        assert before_effort.candidates_by_round == (1,)
        assert before_effort.signals is not None
        assert before_effort.signals.top_score == pytest.approx(0.5, abs=0.005)
        assert expansion_first_distance == [None]
        assert after_effort is not None
        assert after_effort.rounds == 2
        assert after_effort.candidates_by_round == (1, 2)
        assert after_effort.signals is not None
        assert after_effort.signals.top_score == before_effort.signals.top_score
        assert after_effort.assessment == REASON_SUFFICIENT
        assert "alpha solo evidence" in (after_build.context.content or "")

    asyncio.run(scenario())


def test_scoped_first_hit_rule_applies_per_scope_through_the_runtime(tmp_path, monkeypatch) -> None:
    embedding_profile = EmbeddingProfile(
        profile_id="recall-gate-scopes", model="deterministic", dimension=2, distance="l2", normalization="unit"
    )

    class DeterministicEmbedding:
        profile = embedding_profile

        async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
            vectors = {_QUERY: (1.0, 0.0), "alpha beta gamma vector evidence": (0.31, math.sqrt(1 - 0.31**2))}
            return EmbeddingResult(
                vectors=tuple(vectors.get(text.removeprefix("fact\n"), (0.0, 1.0)) for text in texts)
            )

    log = _RecallRoundLog()
    log.install(monkeypatch)

    async def scenario() -> None:
        database = tmp_path / "scoped-first-hit.db"
        async with _runtime(
            database,
            RuntimeConfig(recall_gate_enabled=True, recall_gate_min_candidates=1),
            embedding_model=DeterministicEmbedding(),
        ) as runtime:
            referenced = await _create_scope(runtime, "referenced")
            await _seed(runtime, referenced, ["alpha beta gamma vector evidence"])
            # The primary Scope's fused top is FTS-only; the referenced Scope's fused top
            # carries cosine 0.31. Each Scope's first hit must be judged on its own fusion
            # run, so the verdict is weak-top-1 on the referenced evidence rather than the
            # whole family going unscored. weak-top-1 is not terminal: the lowered floor
            # still re-admits the primary Scope's floor-dropped "alpha solo" entry.
            primary = await _create_scope(runtime, "primary", references=(referenced,))
            await _seed(runtime, primary, ["alpha beta gamma fts evidence", "alpha solo fts evidence"])
            build, effort = await _prepare_build(runtime, primary, _memory_request())

        assert effort is not None
        assert effort.assessment == "weak-top-1"
        # All three identities have been delivered. Zero-cosine rows remain below
        # the policy floor, so the exact probe stops after one expansion.
        assert effort.rounds == 2
        assert effort.candidates_by_round == (2, 3)
        assert [call["memory_recoverable"] for call in log.calls] == [True, False]
        assert effort.signals is not None
        assert effort.signals.scored_families == 1
        assert effort.signals.top_score == pytest.approx(0.31, abs=0.005)
        assert "alpha solo fts evidence" in (build.context.content or "")

    asyncio.run(scenario())


def test_one_expansion_round_re_admits_a_candidate_blocked_at_round_zero(tmp_path, monkeypatch) -> None:
    log = _RecallRoundLog()
    log.install(monkeypatch)

    async def scenario() -> None:
        database = tmp_path / "e2.db"
        async with _runtime(
            database,
            RuntimeConfig(
                recall_gate_enabled=True,
                recall_gate_min_candidates=2,
                recall_gate_min_top_score=0.0,
                recall_gate_min_top_gap=0.0,
                recall_gate_min_lexical_overlap=0.0,
            ),
        ) as runtime:
            scope_id = await _create_scope(runtime, "e2")
            await _seed(runtime, scope_id, ["alpha beta gamma evidence", "alpha solo marker"])
            build, effort = await _prepare_build(runtime, scope_id, _memory_request())

        assert effort is not None
        assert effort.rounds == 2
        assert effort.expansion_actions == ("admission",)
        assert len(effort.candidates_by_round) == 2
        assert effort.candidates_by_round[1] > effort.candidates_by_round[0]
        assert len(log.calls) == 2
        assert build.context.content is not None
        assert "alpha beta gamma evidence" in build.context.content
        assert "alpha solo marker" in build.context.content

    asyncio.run(scenario())


def test_fully_admitted_memory_does_not_expand_when_no_candidate_can_be_recovered(tmp_path, monkeypatch) -> None:
    log = _RecallRoundLog()
    log.install(monkeypatch)

    async def scenario() -> None:
        database = tmp_path / "fully-admitted.db"
        async with _runtime(
            database,
            RuntimeConfig(
                recall_gate_enabled=True,
                recall_gate_min_candidates=2,
                recall_gate_min_top_score=0.0,
                recall_gate_min_top_gap=0.0,
                recall_gate_min_lexical_overlap=0.0,
            ),
        ) as runtime:
            scope_id = await _create_scope(runtime, "fully-admitted")
            await _seed(runtime, scope_id, ["alpha beta gamma evidence"])
            build, effort = await _prepare_build(runtime, scope_id, _memory_request())

        assert effort is not None
        assert effort.rounds == 1
        assert effort.expansion_actions == ()
        assert effort.candidates_by_round == (1,)
        assert len(log.calls) == 1
        assert build.context.content is not None
        assert "alpha beta gamma evidence" in build.context.content

    asyncio.run(scenario())


@pytest.mark.parametrize("excluded", ["forgotten", "other-scope", "limit"])
def test_atomic_recovery_probe_respects_scope_state_and_candidate_limit(tmp_path, monkeypatch, excluded) -> None:
    log = _RecallRoundLog()
    log.install(monkeypatch)

    async def scenario() -> None:
        async with _runtime(
            tmp_path / "qualified-recovery.db",
            RuntimeConfig(recall_gate_enabled=True, recall_gate_min_candidates=100),
        ) as runtime:
            scope = await _create_scope(runtime, "qualified-recovery")
            if excluded == "limit":
                await _seed(runtime, scope, [f"alpha beta gamma evidence {index}" for index in range(24)])
            elif excluded == "other-scope":
                other = await _create_scope(runtime, "foreign-recovery")
                await _seed(runtime, other, ["alpha evidence"])
            else:
                await _seed(runtime, scope, ["alpha evidence"])
                assert runtime.atomic_memory is not None
                memories = runtime.atomic_memory.for_scope(scope)
                record = (await memories.list()).items[0]
                await memories.forget(
                    record.ref.artifact_id,
                    expected_revision=record.ref.revision,
                    expected_state_version=record.state.state_version,
                )
            build, effort = await _prepare_build(runtime, scope, _memory_request())
            assert effort.rounds == 1 and effort.expansion_actions == ()
            assert len(log.calls) == 1
            if excluded == "limit":
                assert effort.candidates_by_round == (16,)
                assert build.context.status == "ready"
            else:
                assert effort.assessment == REASON_NO_CONTENT
                assert effort.candidates_by_round == (0,)
                assert build.context.status == "empty"

    asyncio.run(scenario())


def test_recoverability_is_refreshed_after_an_expansion_round(tmp_path, monkeypatch) -> None:
    log = _RecallRoundLog()
    log.install(monkeypatch)

    async def scenario() -> None:
        database = tmp_path / "recoverability-refresh.db"
        async with _runtime(
            database,
            RuntimeConfig(
                recall_gate_enabled=True,
                recall_gate_min_candidates=2,
                recall_gate_min_top_score=0.0,
                recall_gate_min_top_gap=0.0,
                recall_gate_min_lexical_overlap=0.0,
            ),
        ) as runtime:
            scope_id = await _create_scope(runtime, "recoverability-refresh")
            await _seed(runtime, scope_id, ["alpha evidence"])
            build, effort = await _prepare_build(runtime, scope_id, _memory_request())

        assert effort is not None
        assert effort.rounds == 2
        assert effort.expansion_actions == ("admission",)
        assert effort.candidates_by_round == (0, 1)
        assert len(log.calls) == 2
        assert build.context.content is not None
        assert "alpha evidence" in build.context.content

    asyncio.run(scenario())


def test_two_expansion_rounds_stop_at_max_rounds(tmp_path, monkeypatch) -> None:
    log = _RecallRoundLog()
    log.force_recoverable_family = MEMORY_FAMILY
    log.install(monkeypatch)

    async def scenario() -> None:
        database = tmp_path / "e3.db"
        async with _runtime(
            database,
            RuntimeConfig(recall_gate_enabled=True, recall_gate_max_rounds=2, recall_gate_min_candidates=100),
        ) as runtime:
            scope_id = await _create_scope(runtime, "e3")
            await _seed(runtime, scope_id, ["alpha beta gamma evidence", "alpha solo marker", "beta gamma marker"])
            _build, effort = await _prepare_build(runtime, scope_id, _memory_request())

        assert effort is not None
        assert effort.rounds == 3
        assert effort.assessment == REASON_AT_MAX_ROUNDS
        assert effort.expansion_actions == ("admission", "policy-floor")
        assert len(log.calls) == 3

    asyncio.run(scenario())


def test_atomic_vector_search_uses_the_provider_query_input(tmp_path) -> None:
    class QueryEmbedding:
        profile = EmbeddingProfile(profile_id="query-input", model="query-input", dimension=2)
        queries = 0

        async def embed(self, texts):
            return EmbeddingResult(vectors=tuple((1.0, 0.0) for _ in texts))

        async def embed_query(self, texts):
            self.queries += 1
            return EmbeddingResult(vectors=tuple((-1.0, 0.0) for _ in texts))

    async def scenario() -> None:
        embedding = QueryEmbedding()
        async with open_builtin_runtime(
            BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'query-input.db'}")),
            embedding_model=embedding,
        ) as runtime:
            scope = await _create_scope(runtime, "query-input")
            await _seed(runtime, scope, ["alpha beta gamma evidence"])
            assert runtime.atomic_memory is not None
            result = await runtime.atomic_memory.for_scope(scope).search("unrelated query", mode="vector")
            assert result.mode == "vector" and result.hits == ()
            assert result.embedding_calls == 1 and embedding.queries == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["unavailable", "stalled"])
def test_atomic_optional_query_embedding_is_attempted_once_per_prepare(tmp_path, monkeypatch, failure) -> None:
    class OptionalEmbedding:
        profile = EmbeddingProfile(profile_id="optional", model="optional", dimension=2)
        queries = 0

        async def embed(self, texts):
            return EmbeddingResult(vectors=tuple((1.0, 0.0) for _ in texts))

        async def embed_query(self, texts):
            self.queries += 1
            if failure == "stalled":
                await asyncio.sleep(20)
            raise InferenceUnavailableError("embed")

    log = _RecallRoundLog()
    log.force_recoverable_family = MEMORY_FAMILY
    log.install(monkeypatch)

    async def scenario() -> None:
        embedding = OptionalEmbedding()
        async with open_builtin_runtime(
            BuiltinConfig(
                database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'optional.db'}"),
                runtime=RuntimeConfig(recall_gate_enabled=True, recall_gate_min_candidates=100),
            ),
            embedding_model=embedding,
        ) as runtime:
            scope = await _create_scope(runtime, "optional")
            await _seed(runtime, scope, ["alpha beta gamma evidence", "alpha evidence"])
            async with asyncio.timeout(2):
                build, effort = await _prepare_build(runtime, scope, _memory_request())
            assert effort.rounds == 3 and len(log.calls) == 3
            assert build.context.content is not None and "alpha evidence" in build.context.content
            assert embedding.queries == 1

    asyncio.run(scenario())


def test_topic_embedding_timeout_is_paid_once_per_prepare(tmp_path, monkeypatch) -> None:
    class SlowEmbedding:
        profile = EmbeddingProfile(profile_id="slow", model="slow", dimension=2)
        attempts = 0

        async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
            self.attempts += 1
            await asyncio.sleep(20)
            return EmbeddingResult(vectors=((1.0, 0.0),))

    log = _RecallRoundLog()
    log.force_recoverable_family = "topic-memory"
    log.install(monkeypatch)
    embedding = SlowEmbedding()

    async def scenario() -> None:
        async with _runtime(
            tmp_path / "topic-timeout.db",
            RuntimeConfig(recall_gate_enabled=True, recall_gate_max_rounds=2, recall_gate_min_candidates=100),
        ) as runtime:
            scope_id = await _create_scope(runtime, "topic-timeout")
            await _seed_topic_memories(runtime, scope_id, 1)
            assert runtime._topic_memory_search is not None
            runtime._topic_memory_searcher = TopicMemorySearcher(
                search=runtime._topic_memory_search,
                get=runtime._topic_memory_get,
                browse=runtime._topic_memory_browse,
                embedding_model=embedding,
                observer=runtime._topic_memory_search_observer,
            )
            request = _memory_request(assembly=_TOPIC_MEMORY_ONLY)
            for attempt in (1, 2):
                build, effort = await _prepare_build(runtime, scope_id, request)
                assert effort.rounds == 3
                assert effort.expansion_actions == ("admission", "policy-floor")
                assert effort.added_embeddings == 0
                assert build.context.content is not None
                assert "alpha beta topic" in build.context.content
                # This is the external inference-attempt budget for one request.
                assert embedding.attempts == attempt

    asyncio.run(scenario())


def test_empty_scope_is_no_content_and_does_not_expand(tmp_path, monkeypatch) -> None:
    log = _RecallRoundLog()
    log.install(monkeypatch)

    async def scenario() -> None:
        database = tmp_path / "e4.db"
        async with _runtime(database, RuntimeConfig(recall_gate_enabled=True)) as runtime:
            scope_id = await _create_scope(runtime, "e4")
            monkeypatch.setattr(runtime, "_experience_recall", None)
            monkeypatch.setattr(runtime, "_topic_memory_search", None)
            build, effort = await _prepare_build(runtime, scope_id, PrepareContextRequest(query=_QUERY))

        assert effort is not None
        assert effort.assessment == REASON_NO_CONTENT
        assert effort.rounds == 1
        assert len(log.calls) == 1
        assert build.context.status == "empty"

    asyncio.run(scenario())


def test_configured_experience_with_no_retrieved_rows_does_not_expand(tmp_path, monkeypatch) -> None:
    log = _RecallRoundLog()
    log.install(monkeypatch)

    async def no_experience(scope_id: str, _query: str, _limit: int, **_kwargs: Any) -> ExperienceSearchOutcome:
        return ExperienceSearchOutcome(
            admission=AdmissionCounts(family="experience", scope_id=scope_id, retrieved=0, admitted=0)
        )

    async def scenario() -> None:
        database = tmp_path / "empty-experience.db"
        async with _runtime(database, RuntimeConfig(recall_gate_enabled=True)) as runtime:
            scope_id = await _create_scope(runtime, "empty-experience")
            monkeypatch.setattr(runtime, "_experience_recall", no_experience)
            monkeypatch.setattr(runtime, "_topic_memory_search", None)
            build, effort = await _prepare_build(
                runtime,
                scope_id,
                PrepareContextRequest.model_validate({
                    "query": _QUERY,
                    "assembly": {"sections": [{"family": "experience", "limit": 2}]},
                }),
            )

        assert effort is not None
        assert effort.assessment == REASON_NO_CONTENT
        assert effort.rounds == 1
        assert len(log.calls) == 1
        assert build.context.status == "empty"

    asyncio.run(scenario())


def test_expansion_never_widens_the_selected_family_set(tmp_path, monkeypatch) -> None:
    log = _RecallRoundLog()
    log.force_recoverable_family = MEMORY_FAMILY
    log.install(monkeypatch)

    async def unavailable(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("an unselected family must never be searched")  # noqa: TRY003

    async def scenario() -> None:
        database = tmp_path / "e5.db"
        async with _runtime(
            database,
            RuntimeConfig(recall_gate_enabled=True, recall_gate_min_candidates=100),
        ) as runtime:
            scope_id = await _create_scope(runtime, "e5")
            await _seed(runtime, scope_id, ["alpha beta gamma evidence", "alpha solo marker", "beta gamma marker"])
            monkeypatch.setattr(runtime, "_experience_recall", unavailable)
            monkeypatch.setattr(runtime, "_topic_memory_search", unavailable)
            build, _effort = await _prepare_build(runtime, scope_id, _memory_request())

        assert build.context.status == "ready"
        assert len(log.calls) == 3
        assert {frozenset(call["families"]) for call in log.calls} == {frozenset({"memory"})}

    asyncio.run(scenario())


def test_memory_head_change_during_expansion_fails_open_to_round_zero(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        database = tmp_path / "head-change.db"
        request = _memory_request()

        async with _runtime(database) as disabled:
            scope_id = await _create_scope(disabled, "head-change")
            await _seed(disabled, scope_id, ["alpha beta gamma evidence", "alpha solo marker"])
            baseline, _baseline_effort = await _prepare_build(disabled, scope_id, request)

        original = ScopedContextApplication._recall_round
        calls = 0

        async def changed_head(
            application: ScopedContextApplication,
            request: PrepareContextRequest,
            scope_ids: Any,
            families: set[str],
            builder: Any,
            *,
            admission: Any,
            reuse: Any,
            topic_reuse: Any,
        ) -> Any:
            nonlocal calls
            calls += 1
            if calls == 1:
                return await original(
                    application,
                    request,
                    scope_ids,
                    families,
                    builder,
                    admission=admission,
                    reuse=reuse,
                    topic_reuse=topic_reuse,
                )
            raise AtomicMemoryConflictError("Memory changed while expanding retrieval")  # noqa: TRY003

        monkeypatch.setattr(ScopedContextApplication, "_recall_round", changed_head)
        async with _runtime(
            database,
            RuntimeConfig(recall_gate_enabled=True, recall_gate_min_candidates=100),
        ) as runtime:
            build, effort = await _prepare_build(runtime, scope_id, request)

        assert effort is not None
        assert effort.assessment == REASON_EXPANSION_FAILED
        assert effort.rounds == 1
        assert calls == 2
        assert build.context.content == baseline.context.content
        assert build.origins == baseline.origins

    asyncio.run(scenario())


def test_later_rounds_never_remove_an_accumulated_candidate(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        database = tmp_path / "e6.db"
        request = _memory_request()

        async with _runtime(database) as disabled:
            scope_id = await _create_scope(disabled, "e6")
            await _seed(disabled, scope_id, ["alpha beta gamma evidence", "alpha solo marker", "beta gamma marker"])
            baseline, _baseline_effort = await _prepare_build(disabled, scope_id, request)

        log = _RecallRoundLog()
        log.force_recoverable_family = MEMORY_FAMILY
        log.empty_from = 3  # the round-two lookup returns no new candidates at all
        log.install(monkeypatch)
        async with _runtime(
            database,
            RuntimeConfig(recall_gate_enabled=True, recall_gate_min_candidates=100),
        ) as runtime:
            build, effort = await _prepare_build(runtime, scope_id, request)

        assert effort is not None
        assert effort.rounds == 3
        assert len(log.calls) == 3
        # Round zero admits the two entries covering >= 2 query terms; round one lowers the floor
        # and re-admits the one-term entry (3 total); the starved round two adds nothing, so the
        # accumulated pool never shrinks after the round it was seeded at.
        assert effort.candidates_by_round == (2, 3, 3)
        assert all(origin in build.origins for origin in baseline.origins)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure_phase", ["search", "assessment"])
def test_later_round_failure_preserves_committed_expansion(tmp_path, monkeypatch, failure_phase) -> None:
    async def scenario() -> None:
        database = tmp_path / "later-failure.db"
        request = _memory_request()

        async with _runtime(database) as disabled:
            scope_id = await _create_scope(disabled, "later-failure")
            await _seed(disabled, scope_id, ["alpha beta gamma evidence", "alpha solo marker", "beta gamma marker"])
            baseline, _baseline_effort = await _prepare_build(disabled, scope_id, request)

        async with _runtime(
            database,
            RuntimeConfig(recall_gate_enabled=True, recall_gate_min_candidates=100, recall_gate_max_rounds=1),
        ) as runtime:
            committed, committed_effort = await _prepare_build(runtime, scope_id, request)
        assert committed_effort is not None
        assert committed_effort.candidates_by_round == (2, 3)
        assert committed.context.content != baseline.context.content

        original = ScopedContextApplication._recall_round
        calls = 0

        async def fails_on_second_expansion(
            application: ScopedContextApplication,
            request: PrepareContextRequest,
            scope_ids: Any,
            families: set[str],
            builder: Any,
            *,
            admission: Any,
            reuse: Any,
            topic_reuse: Any,
        ) -> Any:
            nonlocal calls
            calls += 1
            if calls == 3 and failure_phase == "search":
                raise RuntimeError("later expansion failed")  # noqa: TRY003
            result = await original(
                application,
                request,
                scope_ids,
                families,
                builder,
                admission=admission,
                reuse=reuse,
                topic_reuse=topic_reuse,
            )
            if calls == 2:
                return _RecallRoundOutcome(
                    memory=result.memory,
                    experience=result.experience,
                    topic_memory=result.topic_memory,
                    admissions=(
                        AdmissionCounts(
                            family=MEMORY_FAMILY,
                            scope_id=scope_ids[0],
                            retrieved=2,
                            admitted=1,
                        ),
                    ),
                    embedding_calls=result.embedding_calls,
                    generation_calls=result.generation_calls,
                )
            return result

        monkeypatch.setattr(ScopedContextApplication, "_recall_round", fails_on_second_expansion)
        original_assess = RecallSufficiencyGate.assess

        def fails_on_second_assessment(*args: Any, **kwargs: Any) -> Any:
            if calls == 3 and failure_phase == "assessment":
                raise RuntimeError("later assessment failed")  # noqa: TRY003
            return original_assess(*args, **kwargs)

        monkeypatch.setattr(RecallSufficiencyGate, "assess", fails_on_second_assessment)
        async with _runtime(
            database,
            RuntimeConfig(recall_gate_enabled=True, recall_gate_min_candidates=100),
        ) as runtime:
            build, effort = await _prepare_build(runtime, scope_id, request)

        assert effort is not None
        assert effort.assessment == REASON_EXPANSION_FAILED
        assert effort.rounds == 2
        assert effort.expansion_actions == ("admission",)
        assert len(effort.candidates_by_round) == 2
        assert effort.signals is not None
        assert effort.signals.candidate_count == effort.candidates_by_round[-1]
        assert effort.signals == committed_effort.signals
        assert calls == 3
        assert build.context.content == committed.context.content
        assert build.origins == committed.origins

    asyncio.run(scenario())


def test_gate_failure_fails_open_to_the_round_zero_result(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        database = tmp_path / "e7.db"
        request = _memory_request()

        async with _runtime(database) as disabled:
            scope_id = await _create_scope(disabled, "e7")
            await _seed(disabled, scope_id, ["alpha beta gamma evidence", "alpha solo marker"])
            baseline, _baseline_effort = await _prepare_build(disabled, scope_id, request)

        def exploded(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("gate exploded")  # noqa: TRY003

        monkeypatch.setattr(RecallSufficiencyGate, "assess", exploded)
        async with _runtime(database, RuntimeConfig(recall_gate_enabled=True)) as runtime:
            build, effort = await _prepare_build(runtime, scope_id, request)

        assert effort is not None
        assert effort.assessment == REASON_EXPANSION_FAILED
        assert effort.rounds == 1
        assert effort.expansion_actions == ()
        assert len(effort.candidates_by_round) == 1
        assert build.context.content == baseline.context.content
        assert build.origins == baseline.origins

    asyncio.run(scenario())


def test_empty_assembly_returns_before_any_recall(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        database = tmp_path / "e8.db"
        async with _runtime(database, RuntimeConfig(recall_gate_enabled=True)) as runtime:
            scope_id = await _create_scope(runtime, "e8")
            await _seed(runtime, scope_id, ["alpha beta gamma evidence"])

            def forbidden(*_args: Any, **_kwargs: Any) -> Any:
                raise AssertionError("empty assembly must not reach the recall seam")  # noqa: TRY003

            monkeypatch.setattr(ScopedContextApplication, "_prepare_build", forbidden)
            result = await runtime.context.for_scope(scope_id).prepare(
                PrepareContextRequest.model_validate({"query": _QUERY, "assembly": {"sections": []}})
            )

        assert result.status == "empty"
        assert result.content is None

    asyncio.run(scenario())


def test_budget_floor_short_circuits_the_gate(tmp_path, monkeypatch) -> None:
    log = _RecallRoundLog()
    log.install(monkeypatch)

    async def scenario() -> None:
        database = tmp_path / "e9.db"
        async with _runtime(database, RuntimeConfig(recall_gate_enabled=True)) as runtime:
            scope_id = await _create_scope(runtime, "e9")
            await _seed(runtime, scope_id, ["alpha beta gamma evidence", "alpha solo marker"])
            _build, effort = await _prepare_build(runtime, scope_id, _memory_request(max_bytes=512))

        assert effort is not None
        assert effort.assessment == REASON_BUDGET_FLOOR
        assert effort.rounds == 1
        assert len(log.calls) == 1

    asyncio.run(scenario())


def test_prepare_delivers_final_recall_effort_to_the_optional_sink(tmp_path) -> None:
    async def scenario() -> None:
        observed = []

        async def sink(effort) -> None:
            observed.append(effort)

        database = tmp_path / "sink.db"
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"),
            runtime=RuntimeConfig(
                recall_gate_enabled=True,
                recall_gate_min_candidates=1,
                recall_gate_min_top_gap=0.0,
            ),
        )
        async with open_builtin_runtime(
            config,
            scheduler_path=database.with_suffix(".scheduler.db"),
            recall_effort_sink=sink,
        ) as runtime:
            scope_id = await _create_scope(runtime, "sink")
            await _seed(runtime, scope_id, ["alpha beta gamma " + "evidence " * 500])
            prepared = await runtime.context.for_scope(scope_id).prepare(_memory_request(max_bytes=800))
            assert await _effort_rows(runtime) == []

        assert prepared.status == "ready"
        assert len(observed) == 1
        assert observed[0].rounds == 1
        assert observed[0].truncated_items == 1
        assert observed[0].dropped_items == 0
        assert not hasattr(prepared, "recall_effort")

    asyncio.run(scenario())


def test_topic_memory_candidate_cap_does_not_look_recoverable_when_all_candidates_are_eligible(
    tmp_path,
    monkeypatch,
) -> None:
    log = _RecallRoundLog()
    log.install(monkeypatch)

    async def scenario() -> None:
        database = tmp_path / "topic-over-cap.db"
        config = RuntimeConfig(
            recall_gate_enabled=True,
            recall_gate_min_candidates=2,
            recall_gate_min_lexical_overlap=0.9,
        )
        async with _runtime(database, config) as runtime:
            scope_id = await _create_scope(runtime, "topic-over-cap")
            await _seed_topic_memories(runtime, scope_id, 40)
            request = PrepareContextRequest.model_validate({
                "query": "alpha beta gamma delta epsilon",
                "max_bytes": 8000,
                "assembly": _TOPIC_MEMORY_ONLY,
            })
            _build, effort = await _prepare_build(runtime, scope_id, request)

        assert effort is not None
        assert effort.rounds == 1
        assert effort.candidates_by_round == (8,)
        assert len(log.calls) == 1

    asyncio.run(scenario())


def test_recall_effort_sink_failure_does_not_fail_prepare(tmp_path, caplog) -> None:
    async def scenario() -> None:
        async def sink(_effort) -> None:
            raise RuntimeError("sink failed")  # noqa: TRY003

        database = tmp_path / "sink-failure.db"
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"),
            runtime=RuntimeConfig(recall_gate_enabled=True),
        )
        async with open_builtin_runtime(
            config,
            scheduler_path=database.with_suffix(".scheduler.db"),
            recall_effort_sink=sink,
        ) as runtime:
            scope_id = await _create_scope(runtime, "sink-failure")
            await _seed(runtime, scope_id, ["alpha beta gamma evidence"])
            prepared = await runtime.context.for_scope(scope_id).prepare(_memory_request())
            assert await _effort_rows(runtime) == []

        assert prepared.status == "ready"
        assert any(record.message == "Recall effort sink failed" for record in caplog.records)

    asyncio.run(scenario())


def test_disabled_gate_does_not_call_recall_effort_sink(tmp_path) -> None:
    async def scenario() -> None:
        calls = 0

        async def sink(_effort) -> None:
            nonlocal calls
            calls += 1

        database = tmp_path / "sink-disabled.db"
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"),
            runtime=RuntimeConfig(recall_gate_enabled=False),
        )
        async with open_builtin_runtime(
            config,
            scheduler_path=database.with_suffix(".scheduler.db"),
            recall_effort_sink=sink,
        ) as runtime:
            scope_id = await _create_scope(runtime, "sink-disabled")
            await _seed(runtime, scope_id, ["alpha beta gamma evidence"])
            prepared = await runtime.context.for_scope(scope_id).prepare(_memory_request())

        assert prepared.status == "ready"
        assert calls == 0

    asyncio.run(scenario())


@pytest.mark.parametrize("in_memory", [False, True])
def test_default_recorder_persists_real_expansions_and_isolates_caller_scopes(tmp_path, in_memory: bool) -> None:
    async def scenario() -> None:
        config = RuntimeConfig(
            recall_gate_enabled=True,
            recall_gate_min_candidates=2,
            recall_gate_min_top_score=0.0,
            recall_gate_min_top_gap=0.0,
            recall_gate_min_lexical_overlap=0.0,
        )
        async with _runtime(tmp_path / "effort.db", config, in_memory=in_memory) as runtime:
            # The caller's local date is October 10, but the statistics bucket
            # uses October 9 in UTC, just like existing usage statistics.
            runtime._clock = lambda: datetime(2026, 10, 10, 1, tzinfo=timezone(timedelta(hours=8)))
            first = await _create_scope(runtime, "effort-first")
            second = await _create_scope(runtime, "effort-second")
            for scope_id in (first, second):
                await _seed(runtime, scope_id, ["alpha beta gamma evidence", "alpha solo marker"])
            build, effort = await _prepare_build(runtime, first, _memory_request())
            assert effort is not None
            assert effort.rounds == 2
            assert effort.candidates_by_round == (1, 2)
            assert await _effort_rows(runtime) == []
            before = await _revision_state(runtime, first)
            first_prepared = await runtime.context.for_scope(first).prepare(_memory_request())
            repeated = await runtime.context.for_scope(first).prepare(_memory_request())
            other = await runtime.context.for_scope(second).prepare(_memory_request())
            assert first_prepared == repeated == build.context
            assert other.status == "ready"
            assert await _revision_state(runtime, first) == before

            rows = await _effort_rows(runtime)
            assert len(rows) == 2
            measurement = recall_effort_measurement(effort).model_dump()
            for row in rows:
                multiplier = 2 if row["scope_id"] == first else 1
                assert row == {
                    "scope_id": row["scope_id"],
                    "usage_date": date(2026, 10, 9),
                    **{
                        key: value if key in {"policy_id", "assessment"} else value * multiplier
                        for key, value in measurement.items()
                    },
                }
            assert {row["scope_id"] for row in rows} == {first, second}
            assert _QUERY not in repr(rows)

    asyncio.run(scenario())


def test_default_recorder_is_a_noop_when_gate_is_disabled(tmp_path) -> None:
    async def scenario() -> None:
        async with _runtime(tmp_path / "effort-disabled.db") as runtime:
            scope_id = await _create_scope(runtime, "effort-disabled")
            await _seed(runtime, scope_id, ["alpha beta gamma evidence"])
            prepared = await runtime.context.for_scope(scope_id).prepare(_memory_request())
            assert prepared.status == "ready"
            assert await _effort_rows(runtime) == []

    asyncio.run(scenario())


def test_default_recorder_failure_rolls_back_and_preserves_preparation(tmp_path, monkeypatch, caplog) -> None:
    original = StatisticsRepository.record_recall_effort
    attempts = 0

    async def fail_after_write(repository, connection, scope_id, usage_date, measurement) -> None:
        nonlocal attempts
        attempts += 1
        await original(repository, connection, scope_id, usage_date, measurement)
        raise RuntimeError("private query, citation and SQL parameters must not reach logs")  # noqa: TRY003

    monkeypatch.setattr(StatisticsRepository, "record_recall_effort", fail_after_write)

    async def scenario() -> None:
        async with _runtime(tmp_path / "effort-failure.db", RuntimeConfig(recall_gate_enabled=True)) as runtime:
            scope_id = await _create_scope(runtime, "effort-failure")
            await _seed(runtime, scope_id, ["alpha beta gamma " + "evidence " * 500, "alpha solo marker"])
            request = _memory_request(max_bytes=800)
            expected, effort = await _prepare_build(runtime, scope_id, request)
            assert effort is not None
            assert expected.context.status == "ready"
            assert expected.omissions.truncated_items > 0
            before = await _revision_state(runtime, scope_id)
            prepared = await runtime.context.for_scope(scope_id).prepare(request)
            after_build, _ = await _prepare_build(runtime, scope_id, request)
            assert prepared == expected.context
            assert after_build.origins == expected.origins
            assert after_build.omissions == expected.omissions
            assert await _revision_state(runtime, scope_id) == before
            assert await _effort_rows(runtime) == []

    asyncio.run(scenario())
    assert attempts == 1  # Failed accounting is not retried on the request path.
    failures = [
        record for record in caplog.records if getattr(record, "event", None) == "context.recall_gate.sink_failed"
    ]
    assert len(failures) == 1
    assert failures[0].error_type == "RuntimeError"
    assert failures[0].exc_info is None
    assert "private query" not in caplog.text


def test_default_recorder_write_contention_is_bounded_and_does_not_break_later_operations(
    tmp_path, monkeypatch
) -> None:
    original = StatisticsRepository.record_recall_effort
    database = tmp_path / "effort-lock.db"
    elapsed = []

    async def contended_write(repository, connection, scope_id, usage_date, measurement) -> None:
        with sqlite3.connect(database) as writer:
            writer.execute("BEGIN IMMEDIATE")
            started = time.monotonic()
            try:
                await original(repository, connection, scope_id, usage_date, measurement)
            finally:
                elapsed.append(time.monotonic() - started)
                writer.rollback()

    monkeypatch.setattr(StatisticsRepository, "record_recall_effort", contended_write)

    async def scenario() -> None:
        async with _runtime(
            database, RuntimeConfig(recall_gate_enabled=True, model_usage_write_timeout_seconds=0.05)
        ) as runtime:
            scope_id = await _create_scope(runtime, "effort-lock")
            await _seed(runtime, scope_id, ["alpha beta gamma evidence"])
            prepared = await runtime.context.for_scope(scope_id).prepare(_memory_request())
            assert prepared.status == "ready"
            assert await _effort_rows(runtime) == []
            await _seed(runtime, scope_id, ["later business write still succeeds"])
        assert len(elapsed) == 1
        assert elapsed[0] < 1.0  # Accounting cannot consume SQLite's normal 5-second busy timeout.

    asyncio.run(scenario())


@pytest.mark.parametrize("repeated_cancel", [False, True])
def test_cancelled_preparation_preserves_memory_and_finishes_accounting_once(
    tmp_path, monkeypatch, repeated_cancel
) -> None:
    original = StatisticsRepository.record_recall_effort

    async def scenario() -> None:
        entered = asyncio.Event()
        loop = asyncio.get_running_loop()

        async def delayed_insert(repository, connection, scope_id, usage_date, measurement) -> None:
            driver = (await connection.get_raw_connection()).driver_connection

            def pause_insert() -> int:
                loop.call_soon_threadsafe(entered.set)
                time.sleep(0.15)
                return 1

            await driver.create_function("pause_effort_insert", 0, pause_insert)
            await connection.exec_driver_sql(
                "CREATE TEMP TRIGGER pause_effort_insert BEFORE INSERT ON pc_recall_effort_daily "
                "BEGIN SELECT pause_effort_insert(); END"
            )
            try:
                await original(repository, connection, scope_id, usage_date, measurement)
            finally:
                await connection.exec_driver_sql("DROP TRIGGER IF EXISTS pause_effort_insert")

        async with _runtime(
            tmp_path / "cancel-effort.db", RuntimeConfig(recall_gate_enabled=True), in_memory=True
        ) as runtime:
            scope_id = await _create_scope(runtime, "cancel-effort")
            await _seed(runtime, scope_id, ["alpha beta gamma preserved evidence"])
            expected, _ = await _prepare_build(runtime, scope_id, _memory_request())
            before = await _revision_state(runtime, scope_id)
            monkeypatch.setattr(StatisticsRepository, "record_recall_effort", delayed_insert)
            preparing = asyncio.create_task(runtime.context.for_scope(scope_id).prepare(_memory_request()))
            try:
                await asyncio.wait_for(entered.wait(), timeout=5)
                preparing.cancel()
                if repeated_cancel:
                    await asyncio.sleep(0)
                    preparing.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await preparing
            finally:
                monkeypatch.setattr(StatisticsRepository, "record_recall_effort", original)

            assert await _revision_state(runtime, scope_id) == before
            rows = await _effort_rows(runtime)
            assert len(rows) == 1
            assert rows[0]["preparations"] == 1
            assert await runtime.context.for_scope(scope_id).prepare(_memory_request()) == expected.context
            rows = await _effort_rows(runtime)
            assert len(rows) == 1
            assert rows[0]["preparations"] == 2
            await _seed(runtime, scope_id, ["a later business write still works"])

    asyncio.run(scenario())
